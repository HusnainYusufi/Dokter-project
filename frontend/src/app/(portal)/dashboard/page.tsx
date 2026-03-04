"use client";

import { useState } from "react";
import UploadZone from "@/components/UploadZone";
import PageResult from "@/components/PageResult";

interface ParseResult {
  filename: string;
  pages: string[];
}

export default function DashboardPage() {
  const [result, setResult] = useState<ParseResult | null>(null);
  const [error, setError] = useState("");

  function handleResult(filename: string, pages: string[]) {
    setResult({ filename, pages });
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-white">Dashboard</h1>
        <p className="mt-1 text-sm text-muted">
          Upload a medical PDF to extract structured Markdown content, page by page.
        </p>
      </div>

      {/* Upload card */}
      <div className="rounded-2xl border border-border bg-card p-6">
        <div className="mb-4 flex items-center gap-2">
          <svg className="h-4 w-4 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
          </svg>
          <h2 className="text-sm font-semibold text-white">Upload PDF</h2>
        </div>
        <UploadZone onResult={handleResult} onError={setError} />
      </div>

      {/* Error banner */}
      {error && (
        <div className="flex items-start gap-3 rounded-xl border border-red-500/30 bg-red-500/10 px-5 py-4">
          <svg className="mt-0.5 h-4 w-4 shrink-0 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <div>
            <p className="text-sm font-medium text-red-400">Parsing failed</p>
            <p className="mt-0.5 text-xs text-red-400/70">{error}</p>
          </div>
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-white">Results</h2>
              <p className="text-xs text-muted">
                {result.filename} &mdash; {result.pages.length} page{result.pages.length !== 1 ? "s" : ""}
              </p>
            </div>
            <span className="rounded-full bg-accent/15 px-3 py-1 text-xs font-medium text-accent">
              {result.pages.length} pages
            </span>
          </div>

          <div className="space-y-3">
            {result.pages.map((content, i) => (
              <PageResult key={i} pageNumber={i + 1} content={content} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
