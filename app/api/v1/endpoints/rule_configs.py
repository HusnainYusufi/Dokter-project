"""CRUD API for rule configurations (Rule Studio)."""
from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.deps import RuleConfigStoreDep
from app.schemas.extraction import ErrorDetail
from app.schemas.rules import (
    DocumentTypesResponse,
    RuleConfigCreate,
    RuleConfigListResponse,
    RuleConfigResponse,
    RuleConfigUpdate,
)

router = APIRouter()


@router.get(
    "",
    response_model=RuleConfigListResponse,
    summary="List rule configurations",
)
def list_rule_configs(store: RuleConfigStoreDep) -> RuleConfigListResponse:
    return RuleConfigListResponse(configs=store.list_configs())


@router.get(
    "/document-types",
    response_model=DocumentTypesResponse,
    summary="List known document types (built-in buckets plus custom types in use)",
)
def list_document_types(store: RuleConfigStoreDep) -> DocumentTypesResponse:
    return DocumentTypesResponse(document_types=store.list_document_types())


@router.post(
    "",
    response_model=RuleConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a rule configuration",
    responses={409: {"description": "Name already in use.", "model": ErrorDetail}},
)
def create_rule_config(payload: RuleConfigCreate, store: RuleConfigStoreDep) -> RuleConfigResponse:
    return RuleConfigResponse(config=store.create_config(payload))


@router.get(
    "/{config_id}",
    response_model=RuleConfigResponse,
    summary="Get one rule configuration",
    responses={404: {"description": "Configuration not found.", "model": ErrorDetail}},
)
def get_rule_config(config_id: str, store: RuleConfigStoreDep) -> RuleConfigResponse:
    return RuleConfigResponse(config=store.get_config(config_id))


@router.put(
    "/{config_id}",
    response_model=RuleConfigResponse,
    summary="Update a rule configuration (bumps its version)",
    responses={
        404: {"description": "Configuration not found.", "model": ErrorDetail},
        409: {"description": "Name already in use.", "model": ErrorDetail},
    },
)
def update_rule_config(
    config_id: str, payload: RuleConfigUpdate, store: RuleConfigStoreDep
) -> RuleConfigResponse:
    return RuleConfigResponse(config=store.update_config(config_id, payload))


@router.post(
    "/{config_id}/set-default",
    response_model=RuleConfigResponse,
    summary="Make this configuration the default for new extractions",
    responses={404: {"description": "Configuration not found.", "model": ErrorDetail}},
)
def set_default_rule_config(config_id: str, store: RuleConfigStoreDep) -> RuleConfigResponse:
    return RuleConfigResponse(config=store.set_default(config_id))


@router.post(
    "/{config_id}/duplicate",
    response_model=RuleConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate a configuration as a starting point for edits",
    responses={404: {"description": "Configuration not found.", "model": ErrorDetail}},
)
def duplicate_rule_config(config_id: str, store: RuleConfigStoreDep) -> RuleConfigResponse:
    return RuleConfigResponse(config=store.duplicate_config(config_id))


@router.delete(
    "/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a rule configuration",
    responses={
        400: {"description": "The last configuration cannot be deleted.", "model": ErrorDetail},
        404: {"description": "Configuration not found.", "model": ErrorDetail},
    },
)
def delete_rule_config(config_id: str, store: RuleConfigStoreDep) -> Response:
    store.delete_config(config_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
