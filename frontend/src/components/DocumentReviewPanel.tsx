"use client";

import { AnimatePresence, motion } from "framer-motion";
import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { buildDownloadUrl, buildSourceUrl } from "@/lib/api";
import { usePortalShell } from "@/components/PortalShellContext";
import type { ExtractionJobDetail, PatientSummary, SubSummaryParagraph, SummaryParagraph } from "@/lib/types";

const PdfDocumentViewer = dynamic(() => import("@/components/PdfDocumentViewer"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[720px] items-center justify-center rounded-[28px] border border-slate-200 bg-slate-100 p-3 text-sm font-medium text-slate-500">
      Loading PDF preview...
    </div>
  ),
});

interface Props {
  job: ExtractionJobDetail;
  backHref?: string;
}

function paragraphPages(patient: PatientSummary) {
  return patient.summary_paragraphs.flatMap((paragraph) =>
    [paragraph.page_start, paragraph.page_end].filter((page) => page > 0),
  );
}

function patientPageRange(patient: PatientSummary) {
  const pages = paragraphPages(patient);
  if ((patient.page_start <= 0 || patient.page_end <= 0) && pages.length > 0) {
    const pageStart = Math.min(...pages);
    const pageEnd = Math.max(...pages);
    if (pageStart > 0 && pageEnd > 0) {
      if (pageStart === pageEnd) return `${pageStart}`;
      return `${pageStart}-${pageEnd}`;
    }
  }
  if (patient.page_start <= 0 || patient.page_end <= 0) return "Not indexed";
  if (patient.page_start === patient.page_end) return `${patient.page_start}`;
  return `${patient.page_start}-${patient.page_end}`;
}

function patientPageNumbers(patient: PatientSummary) {
  let pageStart = patient.page_start;
  let pageEnd = patient.page_end;

  const pages = paragraphPages(patient);
  if ((pageStart <= 0 || pageEnd <= 0) && pages.length > 0) {
    pageStart = Math.min(...pages);
    pageEnd = Math.max(...pages);
  }

  if (pageStart <= 0 || pageEnd <= 0 || pageEnd < pageStart) {
    return [];
  }

  return Array.from({ length: pageEnd - pageStart + 1 }, (_, index) => pageStart + index);
}

// Distinct color per document so a reviewer can tell document boundaries apart
// at a glance. Classes are spelled out as literals so Tailwind keeps them.
const DOC_COLORS = [
  { border: "border-l-blue-400", chip: "bg-blue-100 text-blue-700", hover: "hover:border-blue-300 hover:bg-blue-50/60" },
  { border: "border-l-emerald-400", chip: "bg-emerald-100 text-emerald-700", hover: "hover:border-emerald-300 hover:bg-emerald-50/60" },
  { border: "border-l-violet-400", chip: "bg-violet-100 text-violet-700", hover: "hover:border-violet-300 hover:bg-violet-50/60" },
  { border: "border-l-amber-400", chip: "bg-amber-100 text-amber-700", hover: "hover:border-amber-300 hover:bg-amber-50/60" },
  { border: "border-l-rose-400", chip: "bg-rose-100 text-rose-700", hover: "hover:border-rose-300 hover:bg-rose-50/60" },
  { border: "border-l-cyan-400", chip: "bg-cyan-100 text-cyan-700", hover: "hover:border-cyan-300 hover:bg-cyan-50/60" },
  { border: "border-l-fuchsia-400", chip: "bg-fuchsia-100 text-fuchsia-700", hover: "hover:border-fuchsia-300 hover:bg-fuchsia-50/60" },
  { border: "border-l-teal-400", chip: "bg-teal-100 text-teal-700", hover: "hover:border-teal-300 hover:bg-teal-50/60" },
] as const;

// Lab/pathology documents are not required reading — keep them visually muted.
const LAB_COLOR = {
  border: "border-l-slate-300",
  chip: "bg-slate-100 text-slate-500",
  hover: "hover:border-slate-300 hover:bg-slate-50",
} as const;

// Coverage placeholders (admin/blank/unreadable pages) — even more muted:
// they exist so every source page is accounted for, not to draw the eye.
const PLACEHOLDER_COLOR = {
  border: "border-l-slate-200",
  chip: "bg-slate-100 text-slate-400",
  hover: "hover:border-slate-200 hover:bg-slate-50",
} as const;

function documentColor(paragraph: SummaryParagraph) {
  if (paragraph.is_placeholder) return PLACEHOLDER_COLOR;
  if (paragraph.is_lab) return LAB_COLOR;
  const index = paragraph.document_number > 0 ? paragraph.document_number - 1 : 0;
  return DOC_COLORS[index % DOC_COLORS.length];
}

function paragraphPageLabel(paragraph: SummaryParagraph) {
  if (paragraph.page_end !== paragraph.page_start) {
    return `${paragraph.page_start}–${paragraph.page_end}`;
  }
  return `${paragraph.page_start}`;
}

function subSummaryPageLabel(sub: SubSummaryParagraph) {
  if (sub.page_end !== sub.page_start) {
    return `${sub.page_start}–${sub.page_end}`;
  }
  return `${sub.page_start}`;
}

const DATE_HIGHLIGHT_PATTERN =
  /\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2}|(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)(?:\s+\d{1,2}(?:,\s*|\s+)\d{4}|\s+\d{4}))\b/gi;

function renderHighlightedDates(text: string, keyPrefix: string) {
  const normalizedText = text;
  const matches = Array.from(normalizedText.matchAll(DATE_HIGHLIGHT_PATTERN));
  if (matches.length === 0) return normalizedText;

  const parts: Array<string | JSX.Element> = [];
  let lastIndex = 0;

  matches.forEach((match, index) => {
    const start = match.index ?? 0;
    const value = match[0];
    if (start > lastIndex) {
      parts.push(normalizedText.slice(lastIndex, start));
    }
    parts.push(
      <mark
        key={`${keyPrefix}-date-${index}`}
        className="rounded bg-amber-100 px-1 py-0.5 font-semibold text-amber-950"
      >
        {value}
      </mark>,
    );
    lastIndex = start + value.length;
  });

  if (lastIndex < normalizedText.length) {
    parts.push(normalizedText.slice(lastIndex));
  }

  return parts;
}

function formatMainDiagnosis(value: string | null) {
  if (!value) return value;

  const normalized = value.replace(/\|/g, " ").replace(/\s+/g, " ").trim();
  if (!normalized) return null;
  if (/\bN\/A\b/i.test(normalized)) return "N/A";

  const fragments = normalized
    .split(/(?:\r?\n|[.;])/)
    .map((piece) => piece.trim())
    .filter(Boolean);

  const firstFragment = fragments[0] ?? normalized;
  const cleaned = firstFragment
    .replace(/^diagnosis\s*[:/-]?\s*/i, "")
    .replace(/^main diagnosis\s*[:/-]?\s*/i, "")
    .trim();

  if (!cleaned) return "N/A";
  if (cleaned.length <= 120) return cleaned;

  return `${cleaned.slice(0, 117).trimEnd()}...`;
}

function patientHeaderRows(patient: PatientSummary) {
  const claimant = patient.header.claimant || patient.name || null;
  const diagnosisDod = formatMainDiagnosis(patient.header.diagnosis_dod);

  return [
    [
      { label: "To", value: patient.header.to_name },
      { label: "Claim", value: patient.header.claim_number },
    ],
    [
      { label: "From", value: patient.header.from_name },
      { label: "Age / DOB", value: patient.header.age_dob },
    ],
    [
      { label: "Date", value: patient.header.review_date },
      { label: "Occupation", value: patient.header.occupation },
    ],
    [
      { label: "Claimant", value: claimant },
      { label: "Diagnosis / DOD", value: diagnosisDod },
    ],
  ].filter((row) => row.some((item) => item.value));
}

function PatientHeaderGrid({ patient, compact = false }: { patient: PatientSummary; compact?: boolean }) {
  const rows = patientHeaderRows(patient);
  if (rows.length === 0) return null;

  return (
    <div className={`border border-slate-200 bg-slate-50 ${compact ? "mt-3 px-3 py-3" : "px-4 py-4"}`}>
      <div className={`grid gap-3 ${compact ? "md:grid-cols-2" : "md:grid-cols-2"}`}>
        {rows.flat().map((item) =>
          item.value ? (
            <div key={`${item.label}-${item.value}`} className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">{item.label}</p>
              <p className={`${compact ? "mt-1 text-xs" : "mt-1 text-sm"} whitespace-pre-wrap text-slate-700`}>
                {item.value}
              </p>
            </div>
          ) : null,
        )}
      </div>
    </div>
  );
}

export default function DocumentReviewPanel({ job, backHref }: Props) {
  const { openSourcePicker } = usePortalShell();
  const [openPatientId, setOpenPatientId] = useState<string | null>(null);
  const [focusedPatientPage, setFocusedPatientPage] = useState<number | null>(null);

  useEffect(() => {
    setOpenPatientId((current) => {
      if (current && job.patients.some((patient) => patient.id === current)) {
        return current;
      }
      return null;
    });
  }, [job.id, job.patients]);

  const openPatient = useMemo(
    () => job.patients.find((patient) => patient.id === openPatientId) ?? null,
    [job.patients, openPatientId],
  );

  const sourceUrl = job.source_available ? buildSourceUrl(job.id) : null;
  const openPatientPageNumbers = useMemo(() => (openPatient ? patientPageNumbers(openPatient) : []), [openPatient]);

  useEffect(() => {
    if (!openPatient) {
      setFocusedPatientPage(null);
      return;
    }

    setFocusedPatientPage(openPatientPageNumbers[0] ?? null);
  }, [openPatient, openPatientPageNumbers]);

  useEffect(() => {
    if (!openPatient) return;

    const previousBodyOverflow = document.body.style.overflow;
    const previousHtmlOverflow = document.documentElement.style.overflow;

    document.body.style.overflow = "hidden";
    document.documentElement.style.overflow = "hidden";

    return () => {
      document.body.style.overflow = previousBodyOverflow;
      document.documentElement.style.overflow = previousHtmlOverflow;
    };
  }, [openPatient]);

  async function copySummary(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard failures are non-fatal for the review UI.
    }
  }

  return (
    <motion.section
      className="space-y-6"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
    >
      <motion.div
        className="flex items-center justify-between gap-4"
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.28, delay: 0.02, ease: "easeOut" }}
      >
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">File Review</p>
          <h1 className="mt-2 text-3xl font-semibold text-slate-950">{job.filename}</h1>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => openSourcePicker("vault")}
            className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          >
            Browse vault
          </button>
          {backHref && (
            <Link
              href={backHref}
              className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
              </svg>
              Back to dashboard
            </Link>
          )}
        </div>
      </motion.div>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.95fr)]">
        <motion.div
          className="min-w-0 rounded-[32px] border border-slate-200 bg-white p-5 shadow-sm"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.06, ease: "easeOut" }}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <p className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-blue-700">
                Doc. View
              </p>
              <h2 className="mt-3 text-2xl font-semibold text-slate-950">{job.filename}</h2>
              <p className="mt-1 text-sm text-slate-500">
                {job.page_count} pages | {job.patient_count} patient groups
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

          <PdfDocumentViewer sourceUrl={sourceUrl} filename={job.filename} />
        </motion.div>

        <motion.div
          className="min-w-0 rounded-[32px] border border-slate-200 bg-white p-5 shadow-sm"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1, ease: "easeOut" }}
        >
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-blue-700">
                Patients
              </p>
              <h2 className="mt-3 text-2xl font-semibold text-slate-950">Patient Sections</h2>
            </div>
            {job.capture_certification && (
              <span className="hidden rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 lg:inline-flex">
                Capture certified
              </span>
            )}
          </div>

          <div className="mt-4 max-h-[720px] space-y-3 overflow-y-auto pr-1">
            {job.patients.length === 0 && (
              <div className="rounded-3xl border border-dashed border-slate-300 px-5 py-8 text-sm text-slate-500">
                The extraction job has not produced patient sections yet.
              </div>
            )}

            {job.patients.map((patient, index) => {
              const active = openPatient?.id === patient.id;
              const patientLabel = patient.name || "Patient";
              return (
                <motion.div
                  key={patient.id}
                  role="button"
                  tabIndex={0}
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.22, delay: Math.min(index * 0.04, 0.28), ease: "easeOut" }}
                  onClick={() => {
                    setOpenPatientId(patient.id);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setOpenPatientId(patient.id);
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
                      <p className="truncate text-sm font-semibold text-slate-950">{patientLabel}</p>
                      <p className="mt-1 text-xs text-slate-500">Patient section</p>
                    </div>
                    <span className="rounded-full bg-white px-3 py-1 text-[11px] font-semibold text-slate-600 shadow-sm">
                      pages {patientPageRange(patient)}
                    </span>
                  </div>

                  <PatientHeaderGrid patient={patient} compact />

                  <p
                    className="mt-3 overflow-hidden text-sm leading-6 text-slate-700"
                    style={{
                      display: "-webkit-box",
                      WebkitBoxOrient: "vertical",
                      WebkitLineClamp: 3,
                    }}
                  >
                    {patient.summary || "Patient summary generation is still in progress."}
                  </p>

                  <div className="mt-4 flex items-center justify-between gap-3">
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.16em] text-slate-600">
                      {patient.summary_paragraphs.length} document{patient.summary_paragraphs.length === 1 ? "" : "s"}
                    </span>
                    <span className="text-xs text-slate-500">Click to open</span>
                  </div>
                </motion.div>
              );
            })}
          </div>
        </motion.div>
      </div>

      <AnimatePresence>
        {openPatient && (
          <motion.div
            key={`patient-summary-${openPatient.id}`}
            className="fixed inset-0 z-50 !mt-0"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
          >
            <motion.div
              className="absolute inset-0 bg-slate-950/45"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            />

            <motion.div
              className="absolute inset-0 overflow-hidden bg-white shadow-2xl"
              initial={{ opacity: 0, y: 22, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 18, scale: 0.98 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
            <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
              <div>
                <p className="inline-flex rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-blue-700">
                  Patient Summary
                </p>
                <h3 className="mt-3 text-2xl font-semibold text-slate-950">
                  {openPatient.name || "Patient"}
                </h3>
                <p className="mt-1 text-sm text-slate-500">
                  pages {patientPageRange(openPatient)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpenPatientId(null)}
                className="rounded-full border border-slate-200 px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
              >
                Close
              </button>
            </div>

            <div className="grid h-[calc(90vh-92px)] min-h-0 gap-6 px-6 py-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(320px,0.95fr)]">
              <div className="min-h-0">
                {sourceUrl && patientPageNumbers(openPatient).length > 0 ? (
                  <section className="flex h-full min-h-0 flex-col space-y-3">
                    <h4 className="text-lg font-semibold text-slate-950">Patient Pages</h4>
                    <PdfDocumentViewer
                      sourceUrl={sourceUrl}
                      filename={job.filename}
                      pageNumbers={openPatientPageNumbers}
                      scrollToPageNumber={focusedPatientPage}
                      heightClassName="h-full"
                      roundedClassName="rounded-none"
                      pageRoundedClassName="rounded-none"
                    />
                  </section>
                ) : (
                  <section className="flex h-full min-h-0 flex-col space-y-3">
                    <h4 className="text-lg font-semibold text-slate-950">Patient Pages</h4>
                    <div className="flex h-full items-center justify-center border border-dashed border-slate-300 bg-slate-50 px-6 text-center text-sm text-slate-500">
                      Patient-specific pages are not available for this section yet.
                    </div>
                  </section>
                )}
              </div>

              <div className="min-h-0 overflow-y-auto pr-1">
                <div className="space-y-6">
                  <section className="space-y-3">
                    <h4 className="text-lg font-semibold text-slate-950">Patient Header</h4>
                    <PatientHeaderGrid patient={openPatient} />
                  </section>

                  <section className="space-y-3">
                    <div className="flex items-center justify-between gap-4">
                      <h4 className="text-lg font-semibold text-slate-950">Summary</h4>
                      <button
                        type="button"
                        onClick={() => {
                          void copySummary(openPatient.summary);
                        }}
                        className="inline-flex items-center gap-1 rounded-full border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:bg-slate-50"
                      >
                        Copy
                      </button>
                    </div>
                    <div className="border border-slate-200 bg-slate-50 px-5 py-4">
                      {openPatient.summary_paragraphs && openPatient.summary_paragraphs.length > 0 ? (
                        <div className="space-y-3">
                          {openPatient.summary_paragraphs.map((paragraph, index) => {
                            const color = documentColor(paragraph);
                            const pageLabel = paragraphPageLabel(paragraph);
                            return (
                              <button
                                key={`${openPatient.id}-summary-paragraph-${index}`}
                                type="button"
                                onClick={() => {
                                  setFocusedPatientPage(paragraph.page_start);
                                }}
                                title={`${paragraph.is_placeholder ? "Unsummarized page(s)" : `Document ${paragraph.document_number}`} \u00b7 jump to page ${pageLabel}`}
                                className={`block w-full rounded-md border border-l-4 border-transparent bg-white px-3 py-2 text-left transition ${color.border} ${color.hover}`}
                              >
                                <div className="mb-1 flex items-center justify-between gap-2">
                                  <div className="flex items-center gap-2">
                                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${color.chip}`}>
                                      {paragraph.is_placeholder ? "Not summarized" : `Document ${paragraph.document_number}`}
                                    </span>
                                    {paragraph.is_lab && (
                                      <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-slate-500">
                                        Lab report
                                      </span>
                                    )}
                                  </div>
                                  <span className="shrink-0 text-[11px] font-medium uppercase tracking-[0.14em] text-slate-400">
                                    {paragraph.page_start === paragraph.page_end ? `Page ${paragraph.page_start}` : `Pages ${pageLabel}`}
                                  </span>
                                </div>
                                {paragraph.sub_summaries.length <= 1 && (
                                  <p className={`whitespace-pre-wrap text-sm leading-6 ${paragraph.is_placeholder ? "italic text-slate-400" : "text-slate-700"}`}>
                                    {renderHighlightedDates(paragraph.text, `${openPatient.id}-summary-paragraph-${index}`)}
                                  </p>
                                )}
                                {paragraph.sub_summaries.length > 1 && (
                                  <div className="ml-1 mt-2 space-y-1 border-l-2 border-slate-100 pl-3">
                                    {paragraph.sub_summaries.map((sub, subIndex) => (
                                      <div
                                        key={`${openPatient.id}-summary-paragraph-${index}-sub-${subIndex}`}
                                        role="button"
                                        tabIndex={0}
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          setFocusedPatientPage(sub.page_start);
                                        }}
                                        onKeyDown={(event) => {
                                          if (event.key === "Enter" || event.key === " ") {
                                            event.preventDefault();
                                            event.stopPropagation();
                                            setFocusedPatientPage(sub.page_start);
                                          }
                                        }}
                                        title={`Jump to page ${subSummaryPageLabel(sub)}`}
                                        className="block w-full cursor-pointer rounded-md border border-transparent bg-slate-50/70 px-2.5 py-1.5 text-left transition hover:border-slate-200 hover:bg-white"
                                      >
                                        <div className="mb-0.5 flex items-center justify-between gap-2">
                                          <span className="text-[11px] font-medium text-slate-500">
                                            {[sub.date, sub.author].filter(Boolean).join(" · ") || "Entry"}
                                          </span>
                                          <span className="shrink-0 text-[10px] font-medium uppercase tracking-[0.12em] text-slate-400">
                                            {sub.page_start === sub.page_end ? `p. ${sub.page_start}` : `pp. ${subSummaryPageLabel(sub)}`}
                                          </span>
                                        </div>
                                        <p className="whitespace-pre-wrap text-xs leading-5 text-slate-600">
                                          {renderHighlightedDates(sub.text, `${openPatient.id}-summary-paragraph-${index}-sub-${subIndex}`)}
                                        </p>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </button>
                            );
                          })}
                        </div>
                      ) : (
                        openPatient.summary.split("\n\n").map((paragraph, index) => (
                          <p key={`${openPatient.id}-summary-${index}`} className="whitespace-pre-wrap text-sm leading-6 text-slate-700">
                            {renderHighlightedDates(paragraph, `${openPatient.id}-summary-${index}`)}
                          </p>
                        ))
                      )}
                    </div>
                  </section>

                  <section className="space-y-3">
                    <div className="flex items-center justify-between gap-4">
                      <h4 className="text-lg font-semibold text-slate-950">Opinion</h4>
                      <button
                        type="button"
                        onClick={() => {
                          void copySummary(openPatient.opinion);
                        }}
                        className="inline-flex items-center gap-1 rounded-full border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:bg-slate-50"
                      >
                        Copy
                      </button>
                    </div>
                    <div className="border border-slate-200 bg-slate-50 px-5 py-4">
                      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">
                        {renderHighlightedDates(openPatient.opinion, `${openPatient.id}-opinion`)}
                      </p>
                    </div>
                  </section>
                </div>
              </div>
            </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.section>
  );
}
