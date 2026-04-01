"use client";

import { useEffect, useMemo, useState } from "react";

import PageResult from "@/components/PageResult";
import { buildDownloadUrl, buildSourceUrl } from "@/lib/api";
import type { DocumentSummary, ExtractionJobDetail } from "@/lib/types";

interface Props {
  job: ExtractionJobDetail;
}

function summaryMeta(document: DocumentSummary) {
  return [document.document_date, document.document_type, document.author].filter(Boolean).join(" | ");
}

export default function DocumentReviewPanel({ job }: Props) {
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(job.documents[0]?.id ?? null);

  useEffect(() => {
    setSelectedDocumentId((current) => {
      if (current && job.documents.some((document) => document.id === current)) {
        return current;
      }
      return job.documents[0]?.id ?? null;
    });
  }, [job.id, job.documents]);

  const selectedDocument = useMemo(
    () => job.documents.find((document) => document.id === selectedDocumentId) ?? job.documents[0] ?? null,
    [job.documents, selectedDocumentId],
  );

  const selectedPages = useMemo(() => {
    if (!selectedDocument) return job.pages;
    const pageSet = new Set(selectedDocument.page_numbers);
    return job.pages.filter((page) => pageSet.has(page.page_number));
  }, [job.pages, selectedDocument]);

  const sourceUrl = selectedDocument
    ? `${buildSourceUrl(job.id)}#page=${selectedDocument.page_numbers[0] ?? 1}`
    : buildSourceUrl(job.id);

  async function copySummary(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard failures are non-fatal for the review UI.
    }
  }

  return (
    <section className="space-y-6">
      <div className="grid gap-6 xl:grid-cols-[1.2fr_0.95fr]">
        <div className="rounded-[32px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <p className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-blue-700">
                Doc. View
              </p>
              <h2 className="mt-3 text-2xl font-semibold text-slate-950">{job.filename}</h2>
              <p className="mt-1 text-sm text-slate-500">
                {job.page_count} pages | {job.document_count} indexed documents | {job.patient_count} patient groups
              </p>
            </div>

            <a
              href={job.export_artifact.ready ? buildDownloadUrl(job.id) : undefined}
              onClick={(event) => {
                if (!job.export_artifact.ready) event.preventDefault();
              }}
              className={`inline-flex items-center justify-center rounded-2xl px-4 py-2 text-sm font-semibold transition ${
                job.export_artifact.ready
                  ? "bg-slate-900 text-white hover:bg-slate-800"
                  : "cursor-not-allowed bg-slate-100 text-slate-400"
              }`}
              aria-disabled={!job.export_artifact.ready}
            >
              Download DOC
            </a>
          </div>

          <div className="overflow-hidden rounded-[28px] border border-slate-200 bg-slate-50">
            {job.source_available ? (
              <iframe
                key={sourceUrl}
                src={sourceUrl}
                title={`${job.filename} preview`}
                className="h-[720px] w-full bg-white"
              />
            ) : (
              <div className="flex h-[720px] items-center justify-center px-6 text-center text-sm text-slate-500">
                Source preview becomes available as soon as the encrypted upload is stored.
              </div>
            )}
          </div>
        </div>

        <div className="rounded-[32px] border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-blue-700">
                Summary
              </p>
              <h2 className="mt-3 text-2xl font-semibold text-slate-950">Generated Summaries</h2>
            </div>
            {job.capture_certification && (
              <span className="hidden rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 lg:inline-flex">
                Capture certified
              </span>
            )}
          </div>

          <div className="mt-4 max-h-[720px] space-y-3 overflow-y-auto pr-1">
            {job.documents.length === 0 && (
              <div className="rounded-3xl border border-dashed border-slate-300 px-5 py-8 text-sm text-slate-500">
                The extraction job has not produced indexed documents yet.
              </div>
            )}

            {job.documents.map((document) => {
              const active = selectedDocument?.id === document.id;
              return (
                <div
                  key={document.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => setSelectedDocumentId(document.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedDocumentId(document.id);
                    }
                  }}
                  className={`w-full rounded-3xl border px-5 py-4 text-left transition ${
                    active
                      ? "border-blue-300 bg-blue-50 shadow-sm"
                      : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-950">{document.title}</p>
                      <p className="mt-1 text-xs text-slate-500">{summaryMeta(document) || "Awaiting metadata"}</p>
                    </div>
                    <span className="rounded-full bg-white px-3 py-1 text-[11px] font-semibold text-slate-600 shadow-sm">
                      pages {document.page_range}
                    </span>
                  </div>

                  <p className="mt-3 text-sm leading-6 text-slate-700">
                    {document.summary || "Summary generation is still in progress for this document."}
                  </p>

                  <div className="mt-4 flex items-center justify-between">
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.16em] text-slate-600">
                      {document.classification}
                    </span>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        void copySummary(document.summary);
                      }}
                      className="inline-flex items-center gap-1 rounded-full border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:bg-white"
                    >
                      Copy
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="rounded-[32px] border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-blue-700">
              Page-wise Result
            </p>
            <h2 className="mt-3 text-2xl font-semibold text-slate-950">
              {selectedDocument ? selectedDocument.title : "Captured Pages"}
            </h2>
          </div>
          {selectedDocument && (
            <p className="text-sm text-slate-500">
              Showing pages {selectedDocument.page_range} for {selectedDocument.patient_name || "selected patient"}.
            </p>
          )}
        </div>

        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          {selectedPages.map((page) => (
            <PageResult key={page.page_number} page={page} />
          ))}
        </div>
      </div>
    </section>
  );
}
