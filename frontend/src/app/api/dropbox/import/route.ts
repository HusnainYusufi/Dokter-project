import { NextRequest, NextResponse } from "next/server";

import { downloadDropboxPdf, dropboxErrorResponse, getConfiguredDropboxCredentials } from "../_shared";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export async function POST(request: NextRequest) {
  try {
    const body = (await request.json().catch(() => ({}))) as { file?: { name?: string; pathLower?: string } };
    const credentials = getConfiguredDropboxCredentials();
    const downloadedFile = await downloadDropboxPdf(credentials, body.file ?? {});

    const formData = new FormData();
    formData.append("file", new File([downloadedFile.payload], downloadedFile.fileName, { type: "application/pdf" }));

    const response = await fetch(`${API_BASE}/api/v1/extract/jobs`, {
      method: "POST",
      body: formData,
      cache: "no-store",
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      return NextResponse.json(
        { detail: payload?.detail ?? `Upload failed with status ${response.status}` },
        { status: response.status },
      );
    }

    const payload = await response.json();
    return NextResponse.json(payload, { status: response.status });
  } catch (error) {
    return dropboxErrorResponse(error);
  }
}
