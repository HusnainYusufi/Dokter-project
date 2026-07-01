import type {
  CreateJobResponse,
  ExtractionJobDetail,
  JobListResponse,
  LlmRunEntriesResponse,
  LlmRunListResponse,
  LlmRunStage,
  VaultBrowseResponse,
  VaultFileSummary,
  VaultFileResponse,
  VaultFolderResponse,
  VaultRecentResponse,
  VaultUploadResponse,
} from "@/lib/types";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
// Default timeout for ordinary reads/writes. Some endpoints (see
// VAULT_EXTRACT_TIMEOUT_MS below) legitimately take much longer and must opt
// into a larger budget rather than share this one.
const REQUEST_TIMEOUT_MS = 12000;
// Triggering extraction from a vault file re-downloads, hashes, and
// re-encrypts+re-uploads the source PDF synchronously before the server
// responds - proportional to file size, so a large merged PDF can easily take
// longer than the default read timeout. Give it a much longer budget instead
// of aborting client-side while the server is still legitimately working.
const VAULT_EXTRACT_TIMEOUT_MS = 120000;

async function requestJson<T>(path: string, init?: RequestInit, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);
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
  return requestJson<CreateJobResponse>(
    `/api/v1/vault/files/${fileId}/extract`,
    { method: "POST" },
    VAULT_EXTRACT_TIMEOUT_MS,
  );
}

export function buildVaultContentUrl(fileId: string) {
  return `${API_BASE}/api/v1/vault/files/${fileId}/content`;
}

export function buildVaultDownloadUrl(fileId: string) {
  return `${API_BASE}/api/v1/vault/files/${fileId}/download`;
}

export function vaultDownloadFilename(file: VaultFileSummary) {
  if (file.extension && file.name.toLowerCase().endsWith(file.extension.toLowerCase())) {
    return file.name;
  }
  if (file.extension) return `${file.name}${file.extension}`;
  if (file.content_type === "application/pdf") return `${file.name}.pdf`;
  if (file.content_type === "application/msword") return `${file.name}.doc`;
  if (file.content_type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document") return `${file.name}.docx`;
  return file.name;
}

export async function listLlmRuns() {
  return requestJson<LlmRunListResponse>("/api/v1/llm-dev/runs");
}

export async function getLlmRunEntries(runId: string, stage: LlmRunStage, offset = 0, limit = 50) {
  const query = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  return requestJson<LlmRunEntriesResponse>(`/api/v1/llm-dev/runs/${encodeURIComponent(runId)}/${stage}?${query}`);
}
