import uvicorn

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import ProcessingError

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
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(ProcessingError)
async def processing_error_handler(request, exc: ProcessingError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


app.include_router(api_router, prefix=settings.API_V1_STR)


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
