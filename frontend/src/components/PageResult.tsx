"use client";

import { useState } from "react";
import type { PageExtraction } from "@/lib/types";

interface Props {
  page: PageExtraction;
}

function labelForRelevance(value: PageExtraction["clinical_relevance"]) {
  if (value === "clinical") return "Clinical";
  if (value === "functional") return "Functional";
  if (value === "administrative") return "Administrative";
  return "Unknown";
}

function badgeClass(value: PageExtraction["clinical_relevance"]) {
  if (value === "clinical") return "bg-blue-100 text-blue-700";
  if (value === "functional") return "bg-emerald-100 text-emerald-700";
  if (value === "administrative") return "bg-amber-100 text-amber-700";
  return "bg-slate-100 text-slate-700";
}

export default function PageResult({ page }: Props) {
  const [open, setOpen] = useState(true);

  return (
    <div className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-5 py-4 text-left transition hover:bg-slate-50"
      >
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-900 text-xs font-bold text-white">
            {page.page_number}
          </span>
          <div>
            <p className="text-sm font-semibold text-slate-900">Page {page.page_number}</p>
            <p className="text-xs text-slate-500">
              {page.document_type || page.document_title || "Captured page text"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className={`rounded-full px-3 py-1 text-xs font-medium ${badgeClass(page.clinical_relevance)}`}>
            {labelForRelevance(page.clinical_relevance)}
          </span>
          <svg
            className={`h-4 w-4 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      {open && (
        <div className="space-y-4 border-t border-slate-200 px-5 py-4">
          <div className="grid gap-3 text-xs text-slate-600 sm:grid-cols-2">
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="font-semibold text-slate-900">Patient</p>
              <p className="mt-1">{page.patient_name || "Not detected"}</p>
            </div>
            <div className="rounded-2xl bg-slate-50 px-4 py-3">
              <p className="font-semibold text-slate-900">Date / Author</p>
              <p className="mt-1">{[page.document_date, page.author].filter(Boolean).join(" | ") || "Not detected"}</p>
            </div>
          </div>

          {page.boundary_hint && (
            <div className="rounded-2xl border border-blue-100 bg-blue-50 px-4 py-3 text-xs text-blue-700">
              Boundary hint: {page.boundary_hint}
            </div>
          )}

          <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Visible Text</p>
            <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{page.visible_text || "No text captured."}</p>
          </div>
        </div>
      )}
    </div>
  );
}
