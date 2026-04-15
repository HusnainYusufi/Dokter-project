/** When true, only one extraction may run at a time (queued or processing blocks new uploads). */
export function isDemoMode(): boolean {
  return process.env.NEXT_PUBLIC_DEMO_MODE === "true";
}

export function jobStatusBlocksNewUpload(status: string): boolean {
  return status === "queued" || status === "processing";
}
