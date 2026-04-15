import { NextRequest, NextResponse } from "next/server";

import {
  dropboxErrorResponse,
  getConfiguredDropboxCredentials,
  getDropboxAccount,
  listDropboxPdfFiles,
} from "../_shared";

export async function POST(_request: NextRequest) {
  try {
    const credentials = getConfiguredDropboxCredentials();
    const [account, files] = await Promise.all([getDropboxAccount(credentials), listDropboxPdfFiles(credentials)]);
    return NextResponse.json({ account, files });
  } catch (error) {
    return dropboxErrorResponse(error);
  }
}
