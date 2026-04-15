"use client";

import { useEffect, useRef, useState } from "react";

import { createJob } from "@/lib/api";
import type { ExtractionJobSummary } from "@/lib/types";
import { usePortalShell } from "@/components/PortalShellContext";

export default function UploadZone() {
  const { openSourcePicker, demoMode, demoExtractBusy } = usePortalShell();
  const demoLocked = demoMode && demoExtractBusy;
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    function handleLocalFileSelected(event: Event) {
      const nextFile = (event as CustomEvent<File>).detail;
      setFile(nextFile);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
    }

    window.addEventListener("portal:local-file-selected", handleLocalFileSelected as EventListener);
    return () => window.removeEventListener("portal:local-file-selected", handleLocalFileSelected as EventListener);
  }, []);

  async function handleUpload() {
    if (!file || demoLocked) return;

    setLoading(true);

    try {
      const payload = await createJob(file);
      window.dispatchEvent(new CustomEvent<ExtractionJobSummary>("portal:job-created", { detail: payload.job }));
      setFile(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
        <input ref={inputRef} type="file" accept="application/pdf,.pdf" className="hidden" readOnly />

        <button
          type="button"
          disabled={demoLocked}
          onClick={() => openSourcePicker("upload")}
          className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          Choose File
        </button>

        <div className="min-w-0 flex-1 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-3">
          <p className="truncate text-sm font-medium text-slate-900">{file?.name ?? "No file chosen"}</p>
          <p className="mt-1 text-xs text-slate-500">
            Choose local PDF from modal, or open Dropbox tab there for direct Dropbox import.
          </p>
        
        </div>

        <button
          type="button"
          onClick={() => void handleUpload()}
          disabled={!file || loading || demoLocked}
          className="inline-flex items-center justify-center rounded-2xl bg-gradient-to-r from-teal-500 via-cyan-500 to-blue-700 px-5 py-3.5 text-sm font-semibold text-white shadow-[0_18px_40px_rgba(37,99,235,0.28)] transition hover:-translate-y-0.5 hover:shadow-[0_22px_48px_rgba(37,99,235,0.32)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "Starting extraction..." : "Start extraction"}
        </button>
      </div>
    </div>
  );
}
