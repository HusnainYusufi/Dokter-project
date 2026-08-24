"""Saved document types.

A document type is selectable in any rule. The parser's own buckets are seeded
as builtin and cannot be removed, because the page parser emits them whether or
not a row exists. Every other type is operator-owned: it can be created from
Rule Studio, is registered automatically the first time a rule uses it, and can
be permanently deleted.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import status
from sqlalchemy import func, select

from app.core.exceptions import ProcessingError
from app.db.models import DocumentTypeRecord, RuleConfigRuleRecord
from app.db.session import SessionLocal
from app.schemas.rules import DocumentType, DocumentTypeCreate
from app.services.job_store import datetime_to_iso

logger = logging.getLogger(__name__)

# The parser's own taxonomy. These need no detection prompt - the page parser
# already classifies every document into one of them.
BUILTIN_DOCUMENT_TYPES: list[tuple[str, str]] = [
    ("clinical", "Consultations, clinical notes, referral letters, hospital records, telephone interviews, case-management notes, and member-filled claim forms carrying symptoms, diagnoses, or history."),
    ("imaging", "Radiology reports (CT, MRI, X-ray, ultrasound, PET, mammography), specimen radiography, and standalone diagnostic images such as films, ECG tracings, or clinical photographs."),
    ("pathology", "Lab blood and urine results, microbiology, and tissue histopathology. The specimen or procedure date is the controlling date."),
    ("functional", "Functional abilities and capacity evaluations, job descriptions, and work-capacity or restrictions documents."),
    ("administrative", "Cover sheets, billing, consent forms, fax covers, and medical-file-review referral or question forms addressed to the reviewing consultant."),
]

# Common medico-legal kinds offered out of the box. Custom types, so each needs
# a description for the parser to recognize it.
CATCH_ALL_DOCUMENT_TYPE = "Other"

# Surface-level categories shipped out of the box. Deliberately broad: they
# cover the file at a glance, and anything narrower is added by the operator
# from Rule Studio when a claim actually needs it. "Other" closes the set so
# no document is left ungoverned.
SUGGESTED_DOCUMENT_TYPES: list[tuple[str, str]] = [
    (
        CATCH_ALL_DOCUMENT_TYPE,
        "Anything that matches no other document type. A rule on this type is the fallback "
        "for everything unmatched, so no document is left ungoverned.",
    ),
    (
        "Mental health",
        "Psychology, psychiatry, and neuropsychology material: assessments, therapy notes, "
        "mental status examinations, and psychometric testing.",
    ),
    (
        "Rehabilitation therapy",
        "Physiotherapy, occupational therapy, chiropractic, kinesiology, and other hands-on "
        "treatment notes recording modalities, progression, and functional change.",
    ),
    (
        "Diagnostic testing",
        "Specialty testing outside radiology and routine labs: pulmonary function, ECG or "
        "echocardiogram, EMG or nerve conduction, sleep studies, audiology, vestibular "
        "testing, and cognitive screening such as MoCA.",
    ),
    (
        "Vocational and employment",
        "Job descriptions, employer statements, vocational and transferable-skills "
        "assessments, return-to-work plans, and accommodation offers.",
    ),
    (
        "Claim and policy",
        "Insurer-side material: claim forms, case management notes, policy wording and "
        "benefit definitions, insurance applications, prior consultant opinions, and "
        "surveillance reports.",
    ),
    (
        "Legal",
        "Correspondence from counsel, records-request authorizations, and other legal "
        "documents about the claim.",
    ),
]




class DocumentTypeNotFoundError(ProcessingError):
    def __init__(self, type_id: str) -> None:
        super().__init__(
            f"Document type '{type_id}' was not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class DocumentTypeStore:
    def _usage_counts(self, session) -> dict[str, int]:
        rows = session.execute(
            select(
                func.lower(RuleConfigRuleRecord.document_type),
                func.count(RuleConfigRuleRecord.id),
            ).group_by(func.lower(RuleConfigRuleRecord.document_type))
        ).all()
        return {name: count for name, count in rows}

    def _to_schema(self, record: DocumentTypeRecord, usage: dict[str, int]) -> DocumentType:
        return DocumentType(
            id=record.id,
            name=record.name,
            description=record.description or "",
            is_builtin=record.is_builtin,
            usage_count=usage.get(record.name.lower(), 0),
            created_at=datetime_to_iso(record.created_at),
            updated_at=datetime_to_iso(record.updated_at),
        )

    def list_types(self) -> list[DocumentType]:
        """Builtin buckets first, then saved custom types alphabetically."""
        with SessionLocal() as session:
            records = session.execute(
                select(DocumentTypeRecord).order_by(
                    DocumentTypeRecord.is_builtin.desc(),
                    DocumentTypeRecord.name.asc(),
                )
            ).scalars().all()
            usage = self._usage_counts(session)
            builtin_order = {name: index for index, (name, _) in enumerate(BUILTIN_DOCUMENT_TYPES)}
            schemas = [self._to_schema(record, usage) for record in records]
            schemas.sort(
                key=lambda item: (
                    0 if item.is_builtin else 1,
                    builtin_order.get(item.name.lower(), 0) if item.is_builtin else 0,
                    item.name.lower() if not item.is_builtin else "",
                )
            )
            return schemas

    def create_type(self, payload: DocumentTypeCreate, *, is_builtin: bool = False) -> DocumentType:
        with SessionLocal() as session:
            existing = session.execute(
                select(DocumentTypeRecord).where(
                    func.lower(DocumentTypeRecord.name) == payload.name.lower()
                )
            ).scalar_one_or_none()
            if existing:
                raise ProcessingError(
                    f"A document type named '{existing.name}' already exists.",
                    status_code=status.HTTP_409_CONFLICT,
                )
            record = DocumentTypeRecord(
                id=f"dt_{uuid4().hex[:12]}",
                name=payload.name,
                description=payload.description,
                is_builtin=is_builtin,
            )
            session.add(record)
            session.commit()
            return self._to_schema(record, self._usage_counts(session))

    def delete_type(self, type_id: str) -> None:
        """Permanently remove a saved type.

        Rules already referencing it keep their literal document type, so no
        configuration silently changes behavior; the type simply stops being
        offered for selection.
        """
        with SessionLocal() as session:
            record = session.get(DocumentTypeRecord, type_id)
            if not record:
                raise DocumentTypeNotFoundError(type_id)
            if record.is_builtin:
                raise ProcessingError(
                    f"'{record.name}' is one of the parser's own document types and cannot be "
                    "removed. The parser emits it whether or not it is listed here.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            session.delete(record)
            session.commit()

    def register_missing(self, names: list[str]) -> None:
        """Save any type a rule uses that is not in the registry yet, so typing
        a new type into a rule and saving keeps it available afterwards."""
        cleaned = {" ".join((name or "").split()) for name in names}
        cleaned.discard("")
        if not cleaned:
            return
        with SessionLocal() as session:
            known = {
                name.lower()
                for name in session.execute(select(DocumentTypeRecord.name)).scalars().all()
            }
            added = False
            for name in sorted(cleaned):
                if name.lower() in known:
                    continue
                session.add(
                    DocumentTypeRecord(id=f"dt_{uuid4().hex[:12]}", name=name, description="")
                )
                known.add(name.lower())
                added = True
            if added:
                session.commit()

    def seed_defaults(self) -> int:
        """Insert the shipped types once. Existing rows are never overwritten,
        so operator edits and deletions stick."""
        with SessionLocal() as session:
            already_seeded = bool(
                session.execute(
                    select(DocumentTypeRecord.id)
                    .where(DocumentTypeRecord.is_builtin.is_(True))
                    .limit(1)
                ).scalar_one_or_none()
            )
        if already_seeded:
            return 0

        seeded = 0
        for name, description, is_builtin in [
            *((name, description, True) for name, description in BUILTIN_DOCUMENT_TYPES),
            *((name, description, False) for name, description in SUGGESTED_DOCUMENT_TYPES),
        ]:
            with SessionLocal() as session:
                existing = session.execute(
                    select(DocumentTypeRecord).where(
                        func.lower(DocumentTypeRecord.name) == name.lower()
                    )
                ).scalar_one_or_none()
                if existing:
                    # A bare row auto-registered from a rule: complete it rather
                    # than leaving it without its description or builtin flag.
                    existing.description = existing.description or description
                    existing.is_builtin = is_builtin
                    session.commit()
                    continue
            self.create_type(
                DocumentTypeCreate(name=name, description=description), is_builtin=is_builtin
            )
            seeded += 1
        logger.info("Seeded %s document types.", seeded)
        return seeded
