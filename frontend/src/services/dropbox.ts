import type { CreateJobResponse } from "@/lib/types";

export interface DropboxFileItem {
  id: string;
  name: string;
  pathLower: string;
  pathDisplay: string;
  size: number;
  modifiedAt: string | null;
}

export interface DropboxAccountSummary {
  accountId: string;
  name: string;
  email: string;
}

function buildErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload?.detail ?? `Dropbox request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function formatDropboxDate(value: string | null) {
  if (!value) return "Unknown date";

  try {
    return new Intl.DateTimeFormat("en", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return "Unknown date";
  }
}

export function formatDropboxSize(size: number) {
  if (!Number.isFinite(size) || size <= 0) return "0 KB";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unitIndex = 0;

  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }

  const rounded = value >= 10 || unitIndex === 0 ? Math.round(value) : Number(value.toFixed(1));
  return `${rounded} ${units[unitIndex]}`;
}

export async function listDropboxFiles() {
  return postJson<{ account: DropboxAccountSummary | null; files: DropboxFileItem[] }>("/api/dropbox/files", {});
}

export async function importDropboxFile(file: Pick<DropboxFileItem, "name" | "pathLower">) {
  return postJson<CreateJobResponse>("/api/dropbox/import", { file });
}

export function describeDropboxFailure(error: unknown, fallback = "Dropbox request failed.") {
  return buildErrorMessage(error, fallback);
}
