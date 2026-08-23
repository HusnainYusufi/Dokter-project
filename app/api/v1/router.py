from fastapi import APIRouter

from app.api.v1.endpoints import extraction, llm_dev, rule_configs, vault

api_router = APIRouter()
api_router.include_router(extraction.router, prefix="/extract", tags=["extract"])
api_router.include_router(vault.router, prefix="/vault", tags=["vault"])
api_router.include_router(rule_configs.router, prefix="/rule-configs", tags=["rule-configs"])
api_router.include_router(llm_dev.router, prefix="/llm-dev", tags=["llm-dev"])
