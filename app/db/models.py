from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


PayloadText = Text().with_variant(LONGTEXT, "mysql")


class Base(DeclarativeBase):
    pass


class VaultFolderRecord(Base):
    __tablename__ = "vault_folders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("vault_folders.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class VaultFileRecord(Base):
    __tablename__ = "vault_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    folder_id: Mapped[str | None] = mapped_column(ForeignKey("vault_folders.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    preview_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    can_extract: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), default="upload", nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class RuleConfigRecord(Base):
    __tablename__ = "rule_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    golden_rule_prompt: Mapped[str] = mapped_column(PayloadText, default="", nullable=False)
    summary_prompt: Mapped[str | None] = mapped_column(PayloadText, nullable=True)
    opinion_prompt: Mapped[str | None] = mapped_column(PayloadText, nullable=True)
    opinion_template: Mapped[str] = mapped_column(String(32), default="disability", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_seeded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class RuleConfigRuleRecord(Base):
    __tablename__ = "rule_config_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_id: Mapped[str] = mapped_column(
        ForeignKey("rule_configs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(120), nullable=False)
    match_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    action: Mapped[str] = mapped_column(String(32), default="extract", nullable=False)
    instruction_prompt: Mapped[str] = mapped_column(PayloadText, default="", nullable=False)
    max_words: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    use_as_context: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class ExtractionJobRecord(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_file_id: Mapped[str | None] = mapped_column(ForeignKey("vault_files.id", ondelete="SET NULL"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    page_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    patient_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    document_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    capture_certification: Mapped[str | None] = mapped_column(PayloadText, nullable=True)
    pipeline_json: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    export_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    export_content_type: Mapped[str] = mapped_column(String(255), default="application/msword", nullable=False)
    export_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    export_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error: Mapped[str | None] = mapped_column(PayloadText, nullable=True)
    # Rule configuration the job ran with (denormalized for list/dedupe
    # queries; the full immutable snapshot lives in payload_encrypted).
    # These columns are added to pre-existing deployments by
    # `ensure_schema_upgrades()` in app/db/session.py - keep that helper in
    # sync when adding columns here.
    rule_config_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    rule_config_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rule_config_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload_encrypted: Mapped[str] = mapped_column(PayloadText, nullable=False)


class JobArtifactRecord(Base):
    __tablename__ = "job_artifacts"
    __table_args__ = (UniqueConstraint("job_id", "name", name="uq_job_artifacts_job_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("extraction_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
