import { NextResponse } from "next/server";

const DROPBOX_API_BASE = "https://api.dropboxapi.com/2";
const DROPBOX_CONTENT_BASE = "https://content.dropboxapi.com/2";

export interface DropboxCredentialsPayload {
  accessToken?: string;
  refreshToken?: string;
  appKey?: string;
  appSecret?: string;
}

export interface DropboxFileRecord {
  id: string;
  name: string;
  pathLower: string;
  pathDisplay: string;
  size: number;
  modifiedAt: string | null;
}

export interface DropboxAccountRecord {
  accountId: string;
  name: string;
  email: string;
}

export class DropboxRouteError extends Error {
  status: number;

  constructor(message: string, status = 400) {
    super(message);
    this.name = "DropboxRouteError";
    this.status = status;
  }
}

function trimValue(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

export function readDropboxCredentialsFromEnv(): DropboxCredentialsPayload {
  return {
    accessToken: trimValue(process.env.DROPBOX_ACCESS_TOKEN ?? process.env.NEXT_PUBLIC_DROPBOX_ACCESS_TOKEN),
    refreshToken: trimValue(process.env.DROPBOX_REFRESH_TOKEN ?? process.env.NEXT_PUBLIC_DROPBOX_REFRESH_TOKEN),
    appKey: trimValue(process.env.DROPBOX_APP_KEY ?? process.env.NEXT_PUBLIC_DROPBOX_APP_KEY),
    appSecret: trimValue(process.env.DROPBOX_APP_SECRET ?? process.env.NEXT_PUBLIC_DROPBOX_APP_SECRET),
  };
}

async function readErrorMessage(response: Response, fallback: string) {
  const payload = await response.json().catch(() => null);
  if (payload && typeof payload === "object") {
    const detail = "detail" in payload && typeof payload.detail === "string" ? payload.detail : null;
    const summary = "error_summary" in payload && typeof payload.error_summary === "string" ? payload.error_summary : null;
    if (detail) return detail;
    if (summary) return summary;
  }
  return fallback;
}

async function getAccessToken(credentials: DropboxCredentialsPayload) {
  if (credentials.refreshToken && credentials.appKey && credentials.appSecret) {
    const params = new URLSearchParams();
    params.set("grant_type", "refresh_token");
    params.set("refresh_token", credentials.refreshToken);
    params.set("client_id", credentials.appKey);
    params.set("client_secret", credentials.appSecret);

    const response = await fetch("https://api.dropboxapi.com/oauth2/token", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: params,
      cache: "no-store",
    });

    if (!response.ok) {
      throw new DropboxRouteError(
        await readErrorMessage(response, "Unable to refresh Dropbox access token."),
        response.status,
      );
    }

    const payload = (await response.json()) as { access_token?: string };
    if (!payload.access_token) {
      throw new DropboxRouteError("Dropbox refresh flow returned no access token.", 502);
    }

    return payload.access_token;
  }

  if (credentials.accessToken) {
    return credentials.accessToken;
  }

  throw new DropboxRouteError("Add Dropbox access token or refresh credentials first.", 400);
}

export function getConfiguredDropboxCredentials() {
  const credentials = readDropboxCredentialsFromEnv();
  if (!credentials.accessToken && !(credentials.refreshToken && credentials.appKey && credentials.appSecret)) {
    throw new DropboxRouteError("Configure Dropbox env credentials first.", 400);
  }
  return credentials;
}

async function dropboxJsonRequest<T>(path: string, credentials: DropboxCredentialsPayload, body: unknown) {
  const accessToken = await getAccessToken(credentials);
  const response = await fetch(`${DROPBOX_API_BASE}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (!response.ok) {
    throw new DropboxRouteError(await readErrorMessage(response, "Dropbox request failed."), response.status);
  }

  return response.json() as Promise<T>;
}

export async function getDropboxAccount(credentials: DropboxCredentialsPayload): Promise<DropboxAccountRecord> {
  const payload = await dropboxJsonRequest<{
    account_id: string;
    email: string;
    name?: { display_name?: string };
  }>("/users/get_current_account", credentials, null);

  return {
    accountId: payload.account_id,
    email: payload.email,
    name: payload.name?.display_name ?? payload.email,
  };
}

export async function listDropboxPdfFiles(credentials: DropboxCredentialsPayload): Promise<DropboxFileRecord[]> {
  type DropboxEntry = {
    ".tag": string;
    id?: string;
    name?: string;
    path_lower?: string;
    path_display?: string;
    size?: number;
    server_modified?: string;
  };

  type DropboxListResponse = {
    entries: DropboxEntry[];
    cursor: string;
    has_more: boolean;
  };

  const files: DropboxFileRecord[] = [];

  let page = await dropboxJsonRequest<DropboxListResponse>("/files/list_folder", credentials, {
    path: "",
    recursive: true,
    include_deleted: false,
    include_mounted_folders: true,
    limit: 2000,
  });

  while (true) {
    page.entries.forEach((entry) => {
      if (
        entry[".tag"] === "file" &&
        entry.name &&
        entry.path_lower &&
        entry.path_display &&
        entry.name.toLowerCase().endsWith(".pdf")
      ) {
        files.push({
          id: entry.id ?? entry.path_lower,
          name: entry.name,
          pathLower: entry.path_lower,
          pathDisplay: entry.path_display,
          size: entry.size ?? 0,
          modifiedAt: entry.server_modified ?? null,
        });
      }
    });

    if (!page.has_more) break;
    page = await dropboxJsonRequest<DropboxListResponse>("/files/list_folder/continue", credentials, {
      cursor: page.cursor,
    });
  }

  return files.sort((left, right) => {
    const leftTime = left.modifiedAt ? Date.parse(left.modifiedAt) : 0;
    const rightTime = right.modifiedAt ? Date.parse(right.modifiedAt) : 0;
    return rightTime - leftTime || left.name.localeCompare(right.name);
  });
}

export async function downloadDropboxPdf(
  credentials: DropboxCredentialsPayload,
  file: { name?: string; pathLower?: string },
) {
  const pathLower = trimValue(file.pathLower);
  const fileName = trimValue(file.name);

  if (!pathLower || !fileName) {
    throw new DropboxRouteError("Choose Dropbox PDF first.", 400);
  }

  if (!fileName.toLowerCase().endsWith(".pdf")) {
    throw new DropboxRouteError("Only PDF files can be imported from Dropbox.", 400);
  }

  const accessToken = await getAccessToken(credentials);
  const response = await fetch(`${DROPBOX_CONTENT_BASE}/files/download`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Dropbox-API-Arg": JSON.stringify({ path: pathLower }),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new DropboxRouteError(await readErrorMessage(response, "Unable to download Dropbox PDF."), response.status);
  }

  return {
    fileName,
    payload: await response.arrayBuffer(),
  };
}

export function dropboxErrorResponse(error: unknown) {
  if (error instanceof DropboxRouteError) {
    return NextResponse.json({ detail: error.message }, { status: error.status });
  }

  return NextResponse.json(
    { detail: error instanceof Error ? error.message : "Unexpected Dropbox error." },
    { status: 500 },
  );
}
