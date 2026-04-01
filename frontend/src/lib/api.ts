import type {
  CreateJobResponse,
  ExtractionJobDetail,
  JobListResponse,
} from "@/lib/types";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
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

  return response.json() as Promise<T>;
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
  const response = await fetch(`${API_BASE}/api/v1/extract/jobs/${jobId}`, {
    method: "DELETE",
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail ?? `Delete failed with status ${response.status}`);
  }
}

export function buildSourceUrl(jobId: string) {
  return `${API_BASE}/api/v1/extract/jobs/${jobId}/source`;
}

export function buildDownloadUrl(jobId: string) {
  return `${API_BASE}/api/v1/extract/jobs/${jobId}/download`;
}
