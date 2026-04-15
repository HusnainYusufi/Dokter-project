import { NextRequest, NextResponse } from "next/server";

import { dropboxErrorResponse, getConfiguredDropboxCredentials, getDropboxAccount } from "../_shared";

export async function POST(_request: NextRequest) {
  try {
    const credentials = getConfiguredDropboxCredentials();
    const account = await getDropboxAccount(credentials);
    return NextResponse.json({ account });
  } catch (error) {
    return dropboxErrorResponse(error);
  }
}
