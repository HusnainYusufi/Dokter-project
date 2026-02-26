import sys
import os
import uvicorn

# Add project root to sys.path to allow running directly from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import ParsingError

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "A microservice that accepts medical PDF documents and converts them "
        "to structured Markdown using LlamaCloud's agentic-plus parsing tier. "
        "Each page is returned independently to support per-page processing and downstream AI summarisation."
    ),
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=None,  # Disable default light-mode Swagger UI; we serve a dark one below
    openapi_tags=[
        {
            "name": "parsing",
            "description": "Upload a PDF and receive per-page Markdown content extracted via LlamaParse.",
        }
    ],
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception Handlers
@app.exception_handler(ParsingError)
async def parsing_error_handler(request, exc: ParsingError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# Include API Router
app.include_router(api_router, prefix=settings.API_V1_STR)


# Dark-mode Swagger UI (must be added before the static catch-all mount)
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui() -> HTMLResponse:
    # Get the default Swagger UI HTML (keeps the official layout CSS from CDN)
    html = get_swagger_ui_html(
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        title=f"{settings.PROJECT_NAME} — API Docs",
    )
    # Inject our dark-mode overrides AFTER the default stylesheet so they win
    dark_link = '<link rel="stylesheet" type="text/css" href="/swagger-dark.css">'
    patched = html.body.decode().replace("</head>", f"{dark_link}\n</head>")
    return HTMLResponse(content=patched, status_code=html.status_code)

# Mount Static Files (Frontend)
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
