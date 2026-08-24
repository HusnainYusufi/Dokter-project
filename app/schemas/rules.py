"""Pydantic schemas for the dynamic rule engine (Rule Studio).

A *rule configuration* is a named, versioned bundle of:
  - a global "golden rule" prompt applied to every AI stage,
  - optional overrides for the summary/opinion stage prompts,
  - per-document-type rules that teach the parser how to recognize a
    document kind (match_prompt) and tell the pipeline what to do with it
    (action: extract / full_data / skip, plus an instruction prompt and an
    optional word ceiling).

Configurations live in the database and are edited from the portal's Rule
Studio page. When a job is created, the selected configuration is resolved
into an immutable `RuleConfigSnapshot` stored inside the job payload, so
later edits to the configuration never change what a past job ran with.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class RuleAction(str, Enum):
    """What the pipeline does with documents matching a rule."""

    EXTRACT = "extract"  # summarize using the rule's instructions
    FULL_DATA = "full_data"  # hand the model the document's full text, not just distilled evidence
    SKIP = "skip"  # keep the document card but render a placeholder line instead of prose


class OpinionTemplate(str, Enum):
    DISABILITY = "disability"
    CRITICAL_ILLNESS = "critical_illness"
    ACCOMMODATION = "accommodation"
    UNDERWRITING = "underwriting"


class DocumentRule(BaseModel):
    id: str = ""
    document_type: str
    match_prompt: str = ""
    action: RuleAction = RuleAction.EXTRACT
    instruction_prompt: str = ""
    max_words: int | None = None
    # Feed matching documents to the opinion stage as referral/assignment
    # context (questions for the reviewer) even when they are skipped in the
    # summary. Mirrors how administrative referral forms behave today.
    use_as_context: bool = False
    sort_order: int = 0


class DocumentRuleInput(BaseModel):
    document_type: str = Field(min_length=1, max_length=120)
    match_prompt: str = Field(default="", max_length=4000)
    action: RuleAction = RuleAction.EXTRACT
    instruction_prompt: str = Field(default="", max_length=8000)
    max_words: int | None = Field(default=None, ge=10, le=2000)
    use_as_context: bool = False

    @field_validator("document_type")
    @classmethod
    def _strip_document_type(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("document_type cannot be blank")
        return cleaned


class RuleConfigBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    golden_rule_prompt: str = Field(default="", max_length=30000)
    # Appended to the built-in summary prompt, never replacing it, so the
    # extraction rules survive while presentation is steered. This is the
    # supported way to shape output; `summary_prompt` below is the blunt
    # all-or-nothing override.
    summary_presentation: str = Field(default="", max_length=8000)
    summary_prompt: str | None = Field(default=None, max_length=30000)
    opinion_prompt: str | None = Field(default=None, max_length=30000)
    opinion_template: OpinionTemplate = OpinionTemplate.DISABILITY

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned


class RuleConfigCreate(RuleConfigBase):
    rules: list[DocumentRuleInput] = Field(default_factory=list, max_length=50)
    is_default: bool = False


class RuleConfigUpdate(RuleConfigBase):
    rules: list[DocumentRuleInput] = Field(default_factory=list, max_length=50)


class RuleConfig(RuleConfigBase):
    id: str
    is_default: bool = False
    is_seeded: bool = False
    version: int = 1
    created_at: str
    updated_at: str
    rules: list[DocumentRule] = Field(default_factory=list)


class RuleConfigSnapshot(BaseModel):
    """Immutable copy of a configuration stored inside a job's payload."""

    id: str
    name: str
    version: int = 1
    golden_rule_prompt: str = ""
    summary_presentation: str = ""
    summary_prompt: str | None = None
    opinion_prompt: str | None = None
    opinion_template: OpinionTemplate = OpinionTemplate.DISABILITY
    rules: list[DocumentRule] = Field(default_factory=list)


class RuleConfigResponse(BaseModel):
    config: RuleConfig


class RuleConfigListResponse(BaseModel):
    configs: list[RuleConfig]


class DocumentTypesResponse(BaseModel):
    document_types: list[str]
