from typing import Annotated

from fastapi import Depends

from app.services.extraction import ExtractionService
from app.services.job_store import EncryptedJobStore
from app.services.rules import RuleConfigStore
from app.services.rules.document_types import DocumentTypeStore


_job_store = EncryptedJobStore()
_rule_config_store = RuleConfigStore()
_document_type_store = DocumentTypeStore()
_extraction_service = ExtractionService(store=_job_store, rule_config_store=_rule_config_store)


def get_job_store() -> EncryptedJobStore:
    return _job_store


def get_rule_config_store() -> RuleConfigStore:
    return _rule_config_store


def get_document_type_store() -> DocumentTypeStore:
    return _document_type_store


def get_extraction_service() -> ExtractionService:
    """Dependency provider for the extraction workflow service."""
    return _extraction_service


JobStoreDep = Annotated[EncryptedJobStore, Depends(get_job_store)]
RuleConfigStoreDep = Annotated[RuleConfigStore, Depends(get_rule_config_store)]
DocumentTypeStoreDep = Annotated[DocumentTypeStore, Depends(get_document_type_store)]
ExtractionServiceDep = Annotated[ExtractionService, Depends(get_extraction_service)]
