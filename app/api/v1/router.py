from fastapi import APIRouter
from app.api.v1.endpoints import parsing

api_router = APIRouter()
api_router.include_router(parsing.router, prefix="/parse", tags=["parsing"])
