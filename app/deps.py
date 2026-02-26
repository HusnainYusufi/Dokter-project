from typing import Annotated
from fastapi import Depends
from app.services.llama_parse import LlamaParseService


def get_llama_parse_service() -> LlamaParseService:
    """Dependency provider for LlamaParseService."""
    return LlamaParseService()


LlamaParseServiceDep = Annotated[LlamaParseService, Depends(get_llama_parse_service)]
