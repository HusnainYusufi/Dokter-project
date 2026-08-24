"""Saved document types, selectable in any rule."""
from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.deps import DocumentTypeStoreDep
from app.schemas.extraction import ErrorDetail
from app.schemas.rules import (
    DocumentTypeCreate,
    DocumentTypeListResponse,
    DocumentTypeResponse,
)

router = APIRouter()


@router.get("", response_model=DocumentTypeListResponse, summary="List saved document types")
def list_document_types(store: DocumentTypeStoreDep) -> DocumentTypeListResponse:
    return DocumentTypeListResponse(document_types=store.list_types())


@router.post(
    "",
    response_model=DocumentTypeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a new document type",
    responses={409: {"description": "Name already in use.", "model": ErrorDetail}},
)
def create_document_type(
    payload: DocumentTypeCreate, store: DocumentTypeStoreDep
) -> DocumentTypeResponse:
    return DocumentTypeResponse(document_type=store.create_type(payload))


@router.delete(
    "/{type_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently remove a saved document type",
    responses={
        400: {"description": "Parser document types cannot be removed.", "model": ErrorDetail},
        404: {"description": "Document type not found.", "model": ErrorDetail},
    },
)
def delete_document_type(type_id: str, store: DocumentTypeStoreDep) -> Response:
    store.delete_type(type_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
