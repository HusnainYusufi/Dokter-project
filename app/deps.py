from typing import Annotated

from fastapi import Depends

from app.services.extraction_pipeline import ExtractionPipelineService
from app.services.job_store import EncryptedJobStore


_job_store = EncryptedJobStore()
_extraction_service = ExtractionPipelineService(store=_job_store)


def get_extraction_service() -> ExtractionPipelineService:
    """Dependency provider for the extraction workflow service."""
    return _extraction_service


ExtractionServiceDep = Annotated[ExtractionPipelineService, Depends(get_extraction_service)]
