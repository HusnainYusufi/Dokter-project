"""Database-backed CRUD for rule configurations (Rule Studio).

Mirrors the EncryptedJobStore style: plain methods opening short-lived
sessions via SessionLocal. Rule configurations are small and unencrypted -
they are operator-authored prompt text, not patient data.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import status
from sqlalchemy import select

from app.core.exceptions import ProcessingError
from app.db.models import RuleConfigRecord, RuleConfigRuleRecord
from app.db.session import SessionLocal
from app.schemas.rules import (
    DocumentRule,
    DocumentRuleInput,
    OpinionTemplate,
    RuleAction,
    RuleConfig,
    RuleConfigCreate,
    RuleConfigSnapshot,
    RuleConfigUpdate,
)
from app.services.job_store import datetime_to_iso
from app.services.rules.defaults import default_rule_config
from app.services.rules.document_types import DocumentTypeStore

logger = logging.getLogger(__name__)

# The parser's own taxonomy. These work without a detection prompt because the
# page parser already classifies every document into one of them.
BUILTIN_DOCUMENT_TYPES = ["clinical", "imaging", "pathology", "functional", "administrative"]

# Common medico-legal document kinds offered as starting points in Rule Studio.
# Unlike the buckets above these are CUSTOM types: the parser only learns to tag
# one once its rule carries a detection prompt describing it.
SUGGESTED_DOCUMENT_TYPES = [
    "Attending physician statement",
    "Consultation report",
    "Progress note",
    "Discharge summary",
    "Emergency department record",
    "Hospital admission record",
    "Operative report",
    "Imaging report",
    "Laboratory report",
    "Independent medical examination",
    "Functional abilities evaluation",
    "Functional capacity evaluation",
    "Job description",
    "Return-to-work plan",
    "Physiotherapy note",
    "Occupational therapy note",
    "Psychology report",
    "Psychiatry report",
    "Chiropractic note",
    "Medication list",
    "Immunization record",
    "Referral form",
    "Case management note",
    "Telephone interview note",
    "Claimant statement",
    "Insurance claim form",
    "Consent form",
    "Billing statement",
    "Fax cover sheet",
]


class RuleConfigNotFoundError(ProcessingError):
    def __init__(self, config_id: str) -> None:
        super().__init__(
            f"Rule configuration '{config_id}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


def _rule_to_schema(record: RuleConfigRuleRecord) -> DocumentRule:
    return DocumentRule(
        id=record.id,
        document_type=record.document_type,
        match_prompt=record.match_prompt or "",
        action=RuleAction(record.action),
        instruction_prompt=record.instruction_prompt or "",
        override_presentation=bool(record.override_presentation),
        presentation_prompt=record.presentation_prompt or "",
        max_words=record.max_words,
        use_as_context=record.use_as_context,
        sort_order=record.sort_order,
    )


class RuleConfigStore:
    def _config_to_schema(self, session, record: RuleConfigRecord) -> RuleConfig:
        rules = session.execute(
            select(RuleConfigRuleRecord)
            .where(RuleConfigRuleRecord.config_id == record.id)
            .order_by(RuleConfigRuleRecord.sort_order.asc(), RuleConfigRuleRecord.id.asc())
        ).scalars().all()
        return RuleConfig(
            id=record.id,
            name=record.name,
            description=record.description or "",
            golden_rule_prompt=record.golden_rule_prompt or "",
            summary_presentation=record.summary_presentation or "",
            summary_max_words=record.summary_max_words,
            summary_prompt=record.summary_prompt,
            opinion_prompt=record.opinion_prompt,
            opinion_template=OpinionTemplate(record.opinion_template or "disability"),
            is_default=record.is_default,
            is_seeded=record.is_seeded,
            version=record.version,
            created_at=datetime_to_iso(record.created_at),
            updated_at=datetime_to_iso(record.updated_at),
            rules=[_rule_to_schema(rule) for rule in rules],
        )

    def _get_record(self, session, config_id: str) -> RuleConfigRecord:
        record = session.get(RuleConfigRecord, config_id)
        if not record:
            raise RuleConfigNotFoundError(config_id)
        return record

    def _require_unique_name(self, session, name: str, *, exclude_id: str | None = None) -> None:
        query = select(RuleConfigRecord.id).where(RuleConfigRecord.name == name)
        if exclude_id:
            query = query.where(RuleConfigRecord.id != exclude_id)
        if session.execute(query.limit(1)).scalar_one_or_none():
            raise ProcessingError(
                f"A rule configuration named '{name}' already exists.",
                status_code=status.HTTP_409_CONFLICT,
            )

    def _replace_rules(self, session, config_id: str, rules: list[DocumentRuleInput]) -> None:
        existing = session.execute(
            select(RuleConfigRuleRecord).where(RuleConfigRuleRecord.config_id == config_id)
        ).scalars().all()
        for record in existing:
            session.delete(record)
        for index, rule in enumerate(rules):
            session.add(
                RuleConfigRuleRecord(
                    id=f"rule_{uuid4().hex[:12]}",
                    config_id=config_id,
                    document_type=rule.document_type,
                    match_prompt=rule.match_prompt,
                    action=rule.action.value,
                    instruction_prompt=rule.instruction_prompt,
                    override_presentation=rule.override_presentation,
                    presentation_prompt=rule.presentation_prompt,
                    max_words=rule.max_words,
                    use_as_context=rule.use_as_context,
                    sort_order=index,
                )
            )

    def _clear_default_flag(self, session, *, except_id: str | None = None) -> None:
        records = session.execute(
            select(RuleConfigRecord).where(RuleConfigRecord.is_default.is_(True))
        ).scalars().all()
        for record in records:
            if record.id != except_id:
                record.is_default = False

    def list_configs(self) -> list[RuleConfig]:
        with SessionLocal() as session:
            records = session.execute(
                select(RuleConfigRecord).order_by(
                    RuleConfigRecord.is_default.desc(), RuleConfigRecord.name.asc()
                )
            ).scalars().all()
            return [self._config_to_schema(session, record) for record in records]

    def get_config(self, config_id: str) -> RuleConfig:
        with SessionLocal() as session:
            return self._config_to_schema(session, self._get_record(session, config_id))

    def get_default(self) -> RuleConfig | None:
        with SessionLocal() as session:
            record = session.execute(
                select(RuleConfigRecord)
                .where(RuleConfigRecord.is_default.is_(True))
                .limit(1)
            ).scalar_one_or_none()
            if not record:
                record = session.execute(
                    select(RuleConfigRecord).order_by(RuleConfigRecord.created_at.asc()).limit(1)
                ).scalar_one_or_none()
            if not record:
                return None
            return self._config_to_schema(session, record)

    def create_config(self, payload: RuleConfigCreate, *, is_seeded: bool = False) -> RuleConfig:
        with SessionLocal() as session:
            self._require_unique_name(session, payload.name)
            has_any = bool(
                session.execute(select(RuleConfigRecord.id).limit(1)).scalar_one_or_none()
            )
            make_default = payload.is_default or not has_any
            record = RuleConfigRecord(
                id=f"cfg_{uuid4().hex[:12]}",
                name=payload.name,
                description=payload.description,
                golden_rule_prompt=payload.golden_rule_prompt,
                summary_presentation=payload.summary_presentation,
                summary_max_words=payload.summary_max_words,
                summary_prompt=payload.summary_prompt,
                opinion_prompt=payload.opinion_prompt,
                opinion_template=payload.opinion_template.value,
                is_default=make_default,
                is_seeded=is_seeded,
                version=1,
            )
            session.add(record)
            session.flush()
            if make_default:
                self._clear_default_flag(session, except_id=record.id)
            self._replace_rules(session, record.id, payload.rules)
            session.commit()
            config = self._config_to_schema(session, record)
        # Typing a new type into a rule and saving keeps it selectable later.
        DocumentTypeStore().register_missing([rule.document_type for rule in payload.rules])
        return config

    def update_config(self, config_id: str, payload: RuleConfigUpdate) -> RuleConfig:
        with SessionLocal() as session:
            record = self._get_record(session, config_id)
            self._require_unique_name(session, payload.name, exclude_id=config_id)
            record.name = payload.name
            record.description = payload.description
            record.golden_rule_prompt = payload.golden_rule_prompt
            record.summary_presentation = payload.summary_presentation
            record.summary_max_words = payload.summary_max_words
            record.summary_prompt = payload.summary_prompt
            record.opinion_prompt = payload.opinion_prompt
            record.opinion_template = payload.opinion_template.value
            record.version = record.version + 1
            self._replace_rules(session, config_id, payload.rules)
            session.commit()
            config = self._config_to_schema(session, record)
        DocumentTypeStore().register_missing([rule.document_type for rule in payload.rules])
        return config

    def restore_defaults(self, config_id: str) -> RuleConfig:
        """Reset a configuration to the shipped defaults.

        Seeding only ever runs once, so a database created before a shipped
        prompt changed keeps the old text forever. This is how an operator pulls
        the current defaults in. It bumps the version like any other edit, so
        completed extractions keep the rules they actually ran with.
        """
        defaults = default_rule_config()
        current = self.get_config(config_id)
        return self.update_config(
            config_id,
            RuleConfigUpdate(
                # Keep the configuration's own identity; replace what it says.
                name=current.name,
                description=defaults.description,
                golden_rule_prompt=defaults.golden_rule_prompt,
                summary_presentation=defaults.summary_presentation,
                summary_max_words=defaults.summary_max_words,
                summary_prompt=defaults.summary_prompt,
                opinion_prompt=defaults.opinion_prompt,
                opinion_template=defaults.opinion_template,
                rules=defaults.rules,
            ),
        )

    def set_default(self, config_id: str) -> RuleConfig:
        with SessionLocal() as session:
            record = self._get_record(session, config_id)
            record.is_default = True
            self._clear_default_flag(session, except_id=config_id)
            session.commit()
            return self._config_to_schema(session, record)

    def duplicate_config(self, config_id: str) -> RuleConfig:
        source = self.get_config(config_id)
        base_name = f"{source.name} (copy)"
        name = base_name
        with SessionLocal() as session:
            suffix = 2
            while session.execute(
                select(RuleConfigRecord.id).where(RuleConfigRecord.name == name).limit(1)
            ).scalar_one_or_none():
                name = f"{base_name} {suffix}"
                suffix += 1
        return self.create_config(
            RuleConfigCreate(
                name=name,
                description=source.description,
                golden_rule_prompt=source.golden_rule_prompt,
                summary_presentation=source.summary_presentation,
                summary_max_words=source.summary_max_words,
                summary_prompt=source.summary_prompt,
                opinion_prompt=source.opinion_prompt,
                opinion_template=source.opinion_template,
                is_default=False,
                rules=[
                    DocumentRuleInput(
                        document_type=rule.document_type,
                        match_prompt=rule.match_prompt,
                        action=rule.action,
                        instruction_prompt=rule.instruction_prompt,
                        override_presentation=rule.override_presentation,
                        presentation_prompt=rule.presentation_prompt,
                        max_words=rule.max_words,
                        use_as_context=rule.use_as_context,
                    )
                    for rule in source.rules
                ],
            )
        )

    def delete_config(self, config_id: str) -> None:
        with SessionLocal() as session:
            record = self._get_record(session, config_id)
            total = len(
                session.execute(select(RuleConfigRecord.id)).scalars().all()
            )
            if total <= 1:
                raise ProcessingError(
                    "At least one rule configuration must exist. Create another "
                    "configuration before deleting this one.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            was_default = record.is_default
            rules = session.execute(
                select(RuleConfigRuleRecord).where(RuleConfigRuleRecord.config_id == config_id)
            ).scalars().all()
            for rule in rules:
                session.delete(rule)
            session.delete(record)
            session.flush()
            if was_default:
                # Promote the seeded config (or the oldest remaining one) so a
                # default always exists.
                replacement = session.execute(
                    select(RuleConfigRecord)
                    .order_by(RuleConfigRecord.is_seeded.desc(), RuleConfigRecord.created_at.asc())
                    .limit(1)
                ).scalar_one_or_none()
                if replacement:
                    replacement.is_default = True
            session.commit()

    def list_document_types(self) -> list[str]:
        """Suggestions for the document-type picker: the parser's own buckets
        first, then common medico-legal kinds, then any custom type already in
        use across configurations."""
        with SessionLocal() as session:
            rows = session.execute(select(RuleConfigRuleRecord.document_type)).scalars().all()

        # A type a rule actually uses wins on casing over a generic suggestion,
        # so a user's own "Referral Form" is never displayed back to them as
        # "Referral form".
        in_use: dict[str, str] = {}
        for value in rows:
            cleaned = " ".join((value or "").split())
            if cleaned:
                in_use.setdefault(cleaned.lower(), cleaned)

        ordered: dict[str, str] = {}
        for value in [*BUILTIN_DOCUMENT_TYPES, *SUGGESTED_DOCUMENT_TYPES, *in_use.values()]:
            key = value.lower()
            if key not in ordered:
                ordered[key] = in_use.get(key, value)
        return list(ordered.values())

    def resolve_snapshot(self, config_id: str | None) -> RuleConfigSnapshot | None:
        """Resolve a config id (or the default when None) into an immutable
        snapshot to embed in a job. Returns None only when no configuration
        exists at all (e.g. seeding failed), in which case the pipeline runs
        with its built-in prompts."""
        config = self.get_config(config_id) if config_id else self.get_default()
        if config is None:
            return None
        return RuleConfigSnapshot(
            id=config.id,
            name=config.name,
            version=config.version,
            golden_rule_prompt=config.golden_rule_prompt,
            summary_presentation=config.summary_presentation,
            summary_max_words=config.summary_max_words,
            summary_prompt=config.summary_prompt,
            opinion_prompt=config.opinion_prompt,
            opinion_template=config.opinion_template,
            rules=sorted(config.rules, key=lambda rule: rule.sort_order),
        )

    def seed_defaults(self) -> bool:
        """Insert the shipped Default configuration once. User edits persist -
        the seed never overwrites an existing seeded row."""
        with SessionLocal() as session:
            exists = session.execute(
                select(RuleConfigRecord.id).where(RuleConfigRecord.is_seeded.is_(True)).limit(1)
            ).scalar_one_or_none()
            if exists:
                return False
        try:
            self.create_config(default_rule_config(), is_seeded=True)
            logger.info("Seeded the default rule configuration.")
            return True
        except ProcessingError:
            # Name collision with a user-created config - leave theirs alone.
            logger.info("Default rule configuration name already taken; skipping seed.")
            return False
