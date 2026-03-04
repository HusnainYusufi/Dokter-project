"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";

interface Props {
  onResult: (filename: string, pages: string[]) => void;
  onError: (msg: string) => void;
}

export default function UploadZone({ onResult, onError }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);

  const onDrop = useCallback((accepted: File[]) => {
    if (accepted[0]) setFile(accepted[0]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    maxFiles: 1,
  });

  async function handleParse() {
    if (!file) return;
    setLoading(true);
    onError("");

    try {
      const form = new FormData();
      form.append("file", file);

      const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
      const res = await fetch(`${apiUrl}/api/v1/parse/upload`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail ?? `Server error ${res.status}`);
      }

      const data = await res.json();
      onResult(data.filename, data.pages);
    } catch (err: unknown) {
      onError(err instanceof Error ? err.message : "An unexpected error occurred.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div
        {...getRootProps()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-14 text-center transition ${
          isDragActive
            ? "border-accent bg-accent/10"
            : "border-border bg-card hover:border-accent/50 hover:bg-card/80"
        }`}
      >
        <input {...getInputProps()} />
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-accent/10">
          <svg className="h-7 w-7 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
        </div>
        {file ? (
          <div>
            <p className="text-sm font-medium text-white">{file.name}</p>
            <p className="mt-1 text-xs text-muted">{(file.size / 1024).toFixed(1)} KB — click or drop to replace</p>
          </div>
        ) : (
          <div>
            <p className="text-sm font-medium text-white">
              {isDragActive ? "Drop your PDF here" : "Drag & drop a PDF, or click to browse"}
            </p>
            <p className="mt-1 text-xs text-muted">Only PDF files are accepted</p>
          </div>
        )}
      </div>

      <button
        onClick={handleParse}
        disabled={!file || loading}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-6 py-3 text-sm font-semibold text-white transition hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40"
      >
        {loading ? (
          <>
            <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Parsing…
          </>
        ) : (
          <>
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            Parse PDF
          </>
        )}
      </button>
    </div>
  );
}
