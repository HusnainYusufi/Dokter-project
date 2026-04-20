from fastapi import APIRouter

from app.api.v1.endpoints import extraction, vault

api_router = APIRouter()
api_router.include_router(extraction.router, prefix="/extract", tags=["extract"])
api_router.include_router(vault.router, prefix="/vault", tags=["vault"])
