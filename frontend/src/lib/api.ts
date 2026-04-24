import type {
  CreateJobResponse,
  ExtractionJobDetail,
  JobListResponse,
  VaultBrowseResponse,
  VaultFileResponse,
  VaultFolderResponse,
  VaultRecentResponse,
  VaultUploadResponse,
} from "@/lib/types";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const REQUEST_TIMEOUT_MS = 12000;

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    signal: init?.signal ?? controller.signal,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  }).finally(() => globalThis.clearTimeout(timeout));

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail ?? `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

async function requestEmpty(path: string, init?: RequestInit): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail ?? `Request failed with status ${response.status}`);
  }
}

export async function listJobs() {
  return requestJson<JobListResponse>("/api/v1/extract/jobs");
}

export async function getJob(jobId: string) {
  return requestJson<ExtractionJobDetail>(`/api/v1/extract/jobs/${jobId}`);
}

export async function createJob(file: File) {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE}/api/v1/extract/jobs`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail ?? `Upload failed with status ${response.status}`);
  }

  return response.json() as Promise<CreateJobResponse>;
}

export async function deleteJob(jobId: string) {
  return requestEmpty(`/api/v1/extract/jobs/${jobId}`, {
    method: "DELETE",
  });
}

export async function retryJob(jobId: string) {
  return requestJson<CreateJobResponse>(`/api/v1/extract/jobs/${jobId}/retry`, {
    method: "POST",
  });
}

export async function cancelJob(jobId: string) {
  return requestJson<CreateJobResponse>(`/api/v1/extract/jobs/${jobId}/cancel`, {
    method: "POST",
  });
}

export function buildSourceUrl(jobId: string) {
  return `${API_BASE}/api/v1/extract/jobs/${jobId}/source`;
}

export function buildDownloadUrl(jobId: string) {
  return `${API_BASE}/api/v1/extract/jobs/${jobId}/download`;
}

export async function browseVault(folderId?: string | null) {
  const query = folderId ? `?folder_id=${encodeURIComponent(folderId)}` : "";
  return requestJson<VaultBrowseResponse>(`/api/v1/vault/browse${query}`);
}

export async function listRecentVaultFiles() {
  return requestJson<VaultRecentResponse>("/api/v1/vault/files/recent");
}

export async function uploadVaultFiles(files: File[], folderId?: string | null) {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  if (folderId) {
    formData.append("folder_id", folderId);
  }

  const response = await fetch(`${API_BASE}/api/v1/vault/files/upload`, {
    method: "POST",
    body: formData,
    cache: "no-store",
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail ?? `Upload failed with status ${response.status}`);
  }

  return response.json() as Promise<VaultUploadResponse>;
}

export async function createVaultFolder(name: string, parentId?: string | null) {
  return requestJson<VaultFolderResponse>("/api/v1/vault/folders", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      name,
      parent_id: parentId ?? null,
    }),
  });
}

export async function renameVaultFolder(folderId: string, name: string) {
  return requestJson<VaultFolderResponse>(`/api/v1/vault/folders/${folderId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ name }),
  });
}

export async function deleteVaultFolder(folderId: string) {
  return requestEmpty(`/api/v1/vault/folders/${folderId}`, {
    method: "DELETE",
  });
}

export async function getVaultFile(fileId: string) {
  return requestJson<VaultFileResponse>(`/api/v1/vault/files/${fileId}`);
}

export async function renameVaultFile(fileId: string, name: string) {
  return requestJson<VaultFileResponse>(`/api/v1/vault/files/${fileId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ name }),
  });
}

export async function deleteVaultFile(fileId: string) {
  return requestEmpty(`/api/v1/vault/files/${fileId}`, {
    method: "DELETE",
  });
}

export async function createJobFromVaultFile(fileId: string) {
  return requestJson<CreateJobResponse>(`/api/v1/vault/files/${fileId}/extract`, {
    method: "POST",
  });
}

export function buildVaultContentUrl(fileId: string) {
  return `${API_BASE}/api/v1/vault/files/${fileId}/content`;
}

export function buildVaultDownloadUrl(fileId: string) {
  return `${API_BASE}/api/v1/vault/files/${fileId}/download`;
}
