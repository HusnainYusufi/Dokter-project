import logging
import uvicorn

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import ProcessingError
from app.core.security import ApiTokenMiddleware
from app.db.session import init_database_schema
from app.deps import (
    get_document_type_store,
    get_extraction_service,
    get_job_store,
    get_rule_config_store,
)
from app.services.migration_service import LegacyJobMigrationService
from app.services.job_runner import enqueue_extraction_job

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "A secure medico-legal extraction API that captures medical PDF content "
        "page by page, detects patient/document boundaries, generates extractive "
        "summaries, and produces a downloadable Word-compatible .doc artifact."
    ),
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs" if settings.ENABLE_DOCS else None,
    openapi_tags=[
        {
            "name": "extract",
            "description": "Create extraction jobs, review page-wise results, and download Word-compatible exports.",
        }
    ],
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.add_middleware(ApiTokenMiddleware, protected_prefix=settings.API_V1_STR)


@app.exception_handler(ProcessingError)
async def processing_error_handler(request, exc: ProcessingError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request, exc: Exception):
    """Keep tracebacks and internal detail out of API responses.

    Every client reads `detail`, so unexpected failures answer in the same
    shape as handled ones instead of leaking a stack trace.
    """
    logging.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected server error occurred."},
    )


app.include_router(api_router, prefix=settings.API_V1_STR)


def initialize_app_state() -> None:
    init_database_schema()
    get_document_type_store().seed_defaults()
    get_rule_config_store().seed_defaults()
    store = get_job_store()
    store.initialize()
    imported_count = LegacyJobMigrationService(store=store).import_legacy_jobs()
    if imported_count:
        logging.info("Imported %s legacy job(s) into MySQL and object storage", imported_count)


@app.on_event("startup")
async def resume_stored_jobs() -> None:
    initialize_app_state()
    service = get_extraction_service()
    recovered_job_ids = service.recover_incomplete_jobs()
    for job_id in recovered_job_ids:
        enqueue_extraction_job(job_id)


@app.get("/", include_in_schema=False)
async def service_info() -> dict[str, str]:
    return {
        "service": settings.PROJECT_NAME,
        "status": "ok",
        "docs": "/docs" if settings.ENABLE_DOCS else "disabled",
    }


@app.get("/health", include_in_schema=False)
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
