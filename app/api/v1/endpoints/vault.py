from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, File, Form, Response, UploadFile, status

from app.deps import ExtractionServiceDep, JobStoreDep
from app.schemas.extraction import CreateJobResponse, ErrorDetail
from app.schemas.vault import (
    CreateVaultFolderRequest,
    RenameVaultFolderRequest,
    RenameVaultFileRequest,
    VaultBrowseResponse,
    VaultFileResponse,
    VaultFolderResponse,
    VaultRecentResponse,
    VaultUploadResponse,
)

router = APIRouter()


@router.get(
    "/browse",
    response_model=VaultBrowseResponse,
    summary="Browse vault folders and files",
)
def browse_vault(store: JobStoreDep, folder_id: str | None = None) -> VaultBrowseResponse:
    return store.browse_vault(folder_id)


@router.get(
    "/files/recent",
    response_model=VaultRecentResponse,
    summary="List recent vault files",
)
def recent_vault_files(store: JobStoreDep) -> VaultRecentResponse:
    return store.list_recent_vault_files()


@router.post(
    "/folders",
    response_model=VaultFolderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create vault folder",
)
def create_folder(payload: CreateVaultFolderRequest, store: JobStoreDep) -> VaultFolderResponse:
    return store.create_folder(payload.name, parent_id=payload.parent_id)


@router.patch(
    "/folders/{folder_id}",
    response_model=VaultFolderResponse,
    summary="Rename vault folder",
)
def rename_folder(folder_id: str, payload: RenameVaultFolderRequest, store: JobStoreDep) -> VaultFolderResponse:
    return store.rename_folder(folder_id, payload.name)


@router.delete(
    "/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete vault folder",
    responses={404: {"description": "Folder not found.", "model": ErrorDetail}},
)
def delete_folder(folder_id: str, store: JobStoreDep) -> Response:
    store.delete_folder(folder_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/files/upload",
    response_model=VaultUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload files into the encrypted vault",
    responses={400: {"description": "Unsupported file type.", "model": ErrorDetail}},
)
async def upload_files(
    store: JobStoreDep,
    files: list[UploadFile] = File(...),
    folder_id: str | None = Form(None),
) -> VaultUploadResponse:
    payloads = [(item.filename or "upload", item.content_type, await item.read()) for item in files]
    return store.upload_vault_files(payloads, folder_id=folder_id)


@router.get(
    "/files/{file_id}",
    response_model=VaultFileResponse,
    summary="Get vault file metadata",
)
def get_vault_file(file_id: str, store: JobStoreDep) -> VaultFileResponse:
    return store.get_vault_file(file_id)


@router.patch(
    "/files/{file_id}",
    response_model=VaultFileResponse,
    summary="Rename vault file",
)
def rename_vault_file(file_id: str, payload: RenameVaultFileRequest, store: JobStoreDep) -> VaultFileResponse:
    return store.rename_vault_file(file_id, payload.name)


@router.delete(
    "/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete vault file",
    responses={
        404: {"description": "File not found.", "model": ErrorDetail},
        409: {"description": "Linked extraction job exists.", "model": ErrorDetail},
    },
)
def delete_vault_file(file_id: str, store: JobStoreDep) -> Response:
    store.delete_vault_file(file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/files/{file_id}/content",
    summary="Stream decrypted vault file inline",
    responses={404: {"description": "File not found.", "model": ErrorDetail}},
)
def get_vault_file_content(file_id: str, store: JobStoreDep) -> Response:
    file, payload = store.get_vault_file_bytes(file_id)
    headers = {"Content-Disposition": f'inline; filename="{file.name}"'}
    return Response(content=payload, media_type=file.content_type, headers=headers)


@router.get(
    "/files/{file_id}/download",
    summary="Download decrypted vault file",
    responses={404: {"description": "File not found.", "model": ErrorDetail}},
)
def download_vault_file(file_id: str, store: JobStoreDep) -> Response:
    file, payload = store.get_vault_file_bytes(file_id)
    headers = {"Content-Disposition": f'attachment; filename="{file.name}"'}
    return Response(content=payload, media_type=file.content_type, headers=headers)


@router.post(
    "/files/{file_id}/extract",
    response_model=CreateJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create extraction job from vault PDF",
    responses={400: {"description": "File cannot be extracted.", "model": ErrorDetail}},
)
async def extract_vault_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    service: ExtractionServiceDep,
) -> CreateJobResponse:
    job = await service.create_job_from_vault_file(file_id)
    if job.status == "queued":
        background_tasks.add_task(service.process_job, job.id)
    return CreateJobResponse(job=job)
