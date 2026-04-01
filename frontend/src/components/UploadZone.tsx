"use client";

import { useRef, useState } from "react";

import { createJob } from "@/lib/api";
import type { ExtractionJobSummary } from "@/lib/types";

interface Props {
  onUploaded: (job: ExtractionJobSummary) => void;
  onError: (msg: string) => void;
}

export default function UploadZone({ onUploaded, onError }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  async function handleUpload() {
    if (!file) return;
    setLoading(true);
    onError("");

    try {
      const payload = await createJob(file);
      onUploaded(payload.job);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : "An unexpected error occurred.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-[28px] border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="inline-flex items-center justify-center gap-2 rounded-2xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          Choose File
        </button>

        <div className="min-w-0 flex-1 rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-3">
          <p className="truncate text-sm font-medium text-slate-900">{file?.name ?? "No file chosen"}</p>
          <p className="mt-1 text-xs text-slate-500">
            Upload any medical document PDF for encrypted extraction, boundary indexing, and Word export.
          </p>
        </div>

        <button
          type="button"
          onClick={handleUpload}
          disabled={!file || loading}
          className="inline-flex items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-teal-500 to-blue-700 px-6 py-3 text-sm font-semibold text-white shadow-lg shadow-blue-900/20 transition hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {loading ? (
            <>
              <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Encrypting...
            </>
          ) : (
            <>
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
              </svg>
              Upload PDF
            </>
          )}
        </button>
      </div>
    </div>
  );
}
