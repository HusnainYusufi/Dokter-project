"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ToastStack, { type ToastItem } from "@/components/ToastStack";
import UploadZone from "@/components/UploadZone";
import { usePortalShell } from "@/components/PortalShellContext";
import { cancelJob, deleteJob, listJobs, retryJob } from "@/lib/api";
import { jobStatusBlocksNewUpload } from "@/lib/demoMode";
import type { ExtractionJobSummary, PipelineStep } from "@/lib/types";

const JOB_CACHE_KEY = "portal:extraction-jobs";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function chipClass(status: PipelineStep["status"]) {
  if (status === "completed") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (status === "running") return "border-blue-200 bg-blue-50 text-blue-700";
  if (status === "failed") return "border-rose-200 bg-rose-50 text-rose-700";
  return "border-slate-200 bg-slate-50 text-slate-500";
}

function formatCost(value: number | null | undefined) {
  const amount = Number(value || 0);
  if (amount <= 0) return "$0.000000";
  if (amount < 0.0001) return `$${amount.toFixed(6)}`;
  return `$${amount.toFixed(4)}`;
}

function statusLabel(job: ExtractionJobSummary) {
  if (job.status === "completed") return "Ready";
  if (job.status === "failed") return "Failed";
  if (job.status === "cancelled") return "Cancelled";
  if (job.status === "processing") return "Processing";
  return "Queued";
}

function statusBadgeClass(status: ExtractionJobSummary["status"]) {
  if (status === "completed") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (status === "failed") return "border-rose-200 bg-rose-50 text-rose-700";
  if (status === "cancelled") return "border-slate-300 bg-slate-100 text-slate-600";
  if (status === "processing") return "border-blue-200 bg-blue-50 text-blue-700";
  return "border-amber-200 bg-amber-50 text-amber-700";
}

function metaLabel(job: ExtractionJobSummary) {
  const parts = [`Updated ${formatDate(job.updated_at)}`];

  if (job.page_count > 0) {
    parts.push(`${job.page_count} page${job.page_count === 1 ? "" : "s"}`);
  }

  parts.push(statusLabel(job));
  return parts.join(" | ");
}

function formatTokens(value: number | null | undefined) {
  const n = Number(value || 0);
  if (n <= 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

function stageLabel(stage: string) {
  if (stage === "parse") return "Parse";
  if (stage === "summarize") return "Opinion";
  return stage;
}

function visiblePipeline(job: ExtractionJobSummary) {
  return job.pipeline.map((step) => (step.key === "extract" ? { ...step, label: "Parse" } : step));
}

function currentRunningStep(job: ExtractionJobSummary) {
  return visiblePipeline(job).find((step) => step.status === "running")?.label ?? null;
}

function currentRunningDetail(job: ExtractionJobSummary) {
  return visiblePipeline(job).find((step) => step.status === "running")?.detail ?? null;
}

function parseBatchDetails(job: ExtractionJobSummary) {
  const detail = currentRunningDetail(job);
  if (!detail) return [];
  const compactMatches = detail.match(/(?:Page|Batch|Bundle) \d+:[\s\S]*?(?=(?:Page|Batch|Bundle) \d+:|$)/g);
  const rows = compactMatches?.length ? compactMatches : detail.split("\n");
  return rows
    .map((line) => line.trim())
    .filter((line) => /^(Page|Batch|Bundle) \d+:/.test(line))
    .sort((left, right) => batchNumber(left) - batchNumber(right));
}

function batchNumber(detail: string) {
  return Number(detail.match(/^(?:Page|Batch|Bundle) (\d+):/)?.[1] ?? 0);
}

function displayProgressDetail(detail: string) {
  const chunk = detail.match(/^Bundle (\d+): extracting chunk (\d+)\/(\d+) pages ([\d-]+)$/);
  if (chunk) {
    const [, bundle, current, total, pages] = chunk;
    return `Patient bundle ${bundle}: chunk ${current}/${total} | pages ${pages}`;
  }
  const merge = detail.match(/^Bundle (\d+): merging (\d+) chunks pages ([\d-]+)$/);
  if (merge) {
    const [, bundle, total, pages] = merge;
    return `Patient bundle ${bundle}: merging ${total} chunks | pages ${pages}`;
  }
  const validatingMerge = detail.match(/^Bundle (\d+): validating merged output pages ([\d-]+)$/);
  if (validatingMerge) {
    const [, bundle, pages] = validatingMerge;
    return `Patient bundle ${bundle}: validating merged output | pages ${pages}`;
  }
  if (detail.startsWith("Bundle ")) return detail.replace(/^Bundle /, "Patient bundle ");
  const oldBatch = detail.match(/^Batch \d+: (parsed|parsing|cached|failed) pages? ([\d-]+)(.*)$/);
  if (oldBatch) {
    const [, state, page, rest] = oldBatch;
    if (state === "parsed") return `Page ${page}: parse${rest}`;
    if (state === "cached") return `Page ${page}: parse cached${rest}`;
    if (state === "failed") return `Page ${page}: parse failed${rest}`;
    return `Page ${page}: parsing`;
  }
  return detail.replace(/^Page (\d+): parse done/, "Page $1: parse");
}

function parseProgressSummary(job: ExtractionJobSummary) {
  const detail = currentRunningDetail(job);
  if (!detail) return null;
  return detail
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.startsWith("Parsed ") || line.startsWith("Summarized ")) ?? null;
}

function batchDetailClass(detail: string) {
  if (detail.includes("failed")) return "border-rose-200 bg-rose-50 text-rose-700";
  if (detail.includes("parsed") || detail.includes("done") || detail.includes("cached") || detail.includes("recovered") || detail.includes(": parse |")) return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (detail.includes("validating merged") || detail.includes("merging")) return "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700";
  if (detail.includes("validating")) return "border-violet-200 bg-violet-50 text-violet-700";
  if (detail.includes("chunk")) return "border-sky-200 bg-sky-50 text-sky-700";
  if (detail.includes("parsing") || detail.includes("extracting") || detail.includes("splitting")) return "border-blue-200 bg-blue-50 text-blue-700";
  return "border-slate-200 bg-white text-slate-500";
}

function runningElapsedLabel(job: ExtractionJobSummary) {
  if (job.status !== "processing") return null;

  if (!job.processing_started_at) return null;
  const startedAt = Date.parse(job.processing_started_at);
  if (Number.isNaN(startedAt)) return null;

  const elapsedMs = Date.now() - startedAt;
  const totalMinutes = Math.max(0, Math.floor(elapsedMs / 60000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours > 0) return `Running for ${hours}h ${minutes}m`;
  return `Running for ${minutes}m`;
}

function loadCachedJobs() {
  if (typeof window === "undefined") return [];
  try {
    const cached = window.localStorage.getItem(JOB_CACHE_KEY);
    if (!cached) return [];
    const parsed = JSON.parse(cached);
    return Array.isArray(parsed) ? (parsed as ExtractionJobSummary[]) : [];
  } catch {
    return [];
  }
}

export default function DashboardPage() {
  const { demoMode, setDemoExtractBusy } = usePortalShell();
  const [jobs, setJobs] = useState<ExtractionJobSummary[]>([]);
  const [error, setError] = useState("");
  const [jobsLoading, setJobsLoading] = useState(true);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [retryingJobId, setRetryingJobId] = useState<string | null>(null);
  const [cancellingJobId, setCancellingJobId] = useState<string | null>(null);
  const previousJobsRef = useRef<Map<string, ExtractionJobSummary>>(new Map());

  useEffect(() => {
    window.localStorage.setItem(JOB_CACHE_KEY, JSON.stringify(jobs));
  }, [jobs]);

  const pushToast = useCallback((message: string, tone: ToastItem["tone"]) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setToasts((current) => [...current, { id, message, tone }].slice(-4));
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 3500);
  }, []);

  const announceProgress = useCallback(
    (jobsSnapshot: ExtractionJobSummary[]) => {
      const previous = previousJobsRef.current;
      const next = new Map<string, ExtractionJobSummary>();

      jobsSnapshot.forEach((job) => {
        next.set(job.id, job);
        const oldJob = previous.get(job.id);

        if (!oldJob) {
          if (job.status === "queued" || job.status === "processing") {
            pushToast(`${job.filename}: parse started in the background.`, "info");
          }
          return;
        }

        const oldRunning = currentRunningStep(oldJob);
        const newRunning = currentRunningStep(job);
        if (newRunning && newRunning !== oldRunning) {
          pushToast(`${job.filename}: ${newRunning.toLowerCase()} in progress.`, "info");
        }

        if (oldJob.status !== "completed" && job.status === "completed") {
          pushToast(`${job.filename}: export is ready.`, "success");
        } else if (oldJob.status !== "failed" && job.status === "failed") {
          pushToast(`${job.filename}: ${job.error ?? "extraction failed."}`, "error");
        }
      });

      previousJobsRef.current = next;
    },
    [pushToast],
  );

  const refreshJobs = useCallback(async () => {
    try {
      const payload = await listJobs();
      setJobs(payload.jobs);
      setError("");
      announceProgress(payload.jobs);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to load extraction jobs.");
    } finally {
      setJobsLoading(false);
    }
  }, [announceProgress]);

  useEffect(() => {
    const cachedJobs = loadCachedJobs();
    if (cachedJobs.length > 0) {
      setJobs(cachedJobs);
      setJobsLoading(false);
    }
    void refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void refreshJobs();
    }, 1500);
    return () => window.clearInterval(interval);
  }, [refreshJobs]);

  const activeJobs = useMemo(
    () => jobs.filter((job) => job.status === "queued" || job.status === "processing").length,
    [jobs],
  );
  const readyJobs = useMemo(() => jobs.filter((job) => job.status === "completed").length, [jobs]);

  const hasActiveExtraction = useMemo(
    () => jobs.some((job) => jobStatusBlocksNewUpload(job.status)),
    [jobs],
  );

  useEffect(() => {
    if (!demoMode) {
      setDemoExtractBusy(false);
      return;
    }
    setDemoExtractBusy(hasActiveExtraction);
  }, [demoMode, hasActiveExtraction, setDemoExtractBusy]);

  const handleUploaded = useCallback(
    (job: ExtractionJobSummary) => {
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      setError("");
      pushToast(`${job.filename}: upload received. Parsing pages in the background.`, "info");
    },
    [pushToast],
  );

  useEffect(() => {
    function handleJobCreated(event: Event) {
      handleUploaded((event as CustomEvent<ExtractionJobSummary>).detail);
    }

    window.addEventListener("portal:job-created", handleJobCreated as EventListener);
    return () => window.removeEventListener("portal:job-created", handleJobCreated as EventListener);
  }, [handleUploaded]);

  useEffect(() => {
    if (!pendingDeleteId) return;

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !deleteSubmitting) {
        setPendingDeleteId(null);
      }
    }

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [pendingDeleteId, deleteSubmitting]);

  async function confirmDelete() {
    const jobId = pendingDeleteId;
    if (!jobId) return;

    setDeleteSubmitting(true);
    setError("");
    try {
      await deleteJob(jobId);
      const payload = await listJobs();
      const stillExists = payload.jobs.some((job) => job.id === jobId);
      if (stillExists) {
        throw new Error("Delete did not complete on the server.");
      }
      setJobs(payload.jobs);
      announceProgress(payload.jobs);
      pushToast("Job deleted permanently.", "success");
      setPendingDeleteId(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to delete the extraction job.");
      setPendingDeleteId(null);
    } finally {
      setDeleteSubmitting(false);
    }
  }

  async function handleRetry(jobId: string) {
    setRetryingJobId(jobId);
    setError("");
    try {
      const payload = await retryJob(jobId);
      setJobs((current) => [payload.job, ...current.filter((item) => item.id !== payload.job.id)]);
      pushToast(`${payload.job.filename}: retry queued.`, "info");
      void refreshJobs();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to retry the extraction job.");
    } finally {
      setRetryingJobId(null);
    }
  }

  async function handleCancel(jobId: string) {
    setCancellingJobId(jobId);
    setError("");
    try {
      const payload = await cancelJob(jobId);
      setJobs((current) => [payload.job, ...current.filter((item) => item.id !== payload.job.id)]);
      pushToast(`${payload.job.filename}: cancel requested.`, "info");
      void refreshJobs();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to cancel the extraction job.");
    } finally {
      setCancellingJobId(null);
    }
  }

  return (
    <>
      <ToastStack toasts={toasts} />

      <AnimatePresence>
        {pendingDeleteId && (
          <motion.div
            key="delete-confirm"
            className="fixed inset-0 z-[100] flex items-center justify-center px-4 py-8"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            role="dialog"
            aria-modal="true"
            aria-label="Confirm delete extraction job"
            aria-describedby="delete-dialog-desc"
          >
            <motion.button
              type="button"
              aria-label="Dismiss"
              disabled={deleteSubmitting}
              className="absolute inset-0 bg-slate-950/50 disabled:cursor-not-allowed"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => {
                if (!deleteSubmitting) setPendingDeleteId(null);
              }}
            />

            <motion.div
              className="relative z-10 w-full max-w-md rounded-[24px] border border-slate-200 bg-white p-6 shadow-2xl shadow-slate-950/20"
              initial={{ opacity: 0, y: 16, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 12, scale: 0.98 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              <div className="flex gap-4">
                <div
                  className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-rose-200 bg-rose-50"
                  aria-hidden
                >
                  <svg className="h-6 w-6 text-rose-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                    />
                  </svg>
                </div>
                <p id="delete-dialog-desc" className="min-w-0 text-base font-medium leading-7 text-slate-800">
                  Are you sure?
                </p>
              </div>
              <div className="mt-6 flex flex-wrap justify-end gap-3">
                <button
                  type="button"
                  disabled={deleteSubmitting}
                  onClick={() => setPendingDeleteId(null)}
                  className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  No
                </button>
                <button
                  type="button"
                  disabled={deleteSubmitting}
                  onClick={() => void confirmDelete()}
                  className="rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {deleteSubmitting ? "Deleting…" : "Yes"}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">

      <motion.section
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.32 }}
        className="grid gap-4 md:grid-cols-3"
      >
        <motion.div layout className="rounded-[28px] border border-slate-200 bg-white px-5 py-5 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Tracked jobs</p>
          <p className="mt-3 text-3xl font-semibold text-slate-950">{jobs.length}</p>
        </motion.div>
        <motion.div layout className="rounded-[28px] border border-slate-200 bg-white px-5 py-5 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Active jobs</p>
          <p className="mt-3 text-3xl font-semibold text-slate-950">{activeJobs}</p>
        </motion.div>
        <motion.div layout className="rounded-[28px] border border-slate-200 bg-white px-5 py-5 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Ready</p>
          <p className="mt-3 text-3xl font-semibold text-slate-950">{readyJobs}</p>
        </motion.div>
      </motion.section>

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.32, delay: 0.05 }}
      >
        <UploadZone />
      </motion.div>

      {error && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-start gap-3 rounded-2xl border border-rose-200 bg-rose-50 px-5 py-4"
        >
          <svg className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <div>
            <p className="text-sm font-medium text-rose-700">Something needs attention</p>
            <p className="mt-0.5 text-xs text-rose-600">{error}</p>
          </div>
        </motion.div>
      )}

      <motion.section
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.32, delay: 0.1 }}
        className="overflow-hidden rounded-[32px] border border-slate-200 bg-white shadow-sm"
      >
        <div className="flex flex-col gap-3 border-b border-slate-200 px-6 py-5 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-lg font-semibold text-slate-950">Recent summarizer jobs</p>
            <p className="mt-1 text-sm text-slate-500">Track parsing progress and completed review outputs.</p>
          </div>
          <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-600">
            {jobs.length} file{jobs.length === 1 ? "" : "s"}
          </div>
        </div>

        {jobsLoading && <div className="px-6 py-10 text-sm text-slate-500">Loading extraction queue...</div>}

        {!jobsLoading && jobs.length === 0 && (
          <div className="px-6 py-14 text-center">
            <div className="mx-auto max-w-md">
              <p className="text-base font-semibold text-slate-900">No documents yet</p>
              <p className="mt-2 text-sm text-slate-500">
                Choose PDF from encrypted vault to create first extraction job.
              </p>
            </div>
          </div>
        )}

        <div className="space-y-4 p-4 md:p-6">
          {jobs.map((job, index) => (
            <motion.div
              key={job.id}
              layout
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.24, delay: index * 0.03 }}
              className="rounded-[24px] border border-slate-200 bg-slate-50/70 p-5"
            >
              <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
                <div className="min-w-0 flex-1 space-y-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <p className="truncate text-base font-semibold text-slate-950">{job.filename}</p>
                    <span className={`inline-flex rounded-full border px-3 py-1 text-[11px] font-semibold ${statusBadgeClass(job.status)}`}>
                      {statusLabel(job)}
                    </span>
                  </div>

                  <p className="text-sm text-slate-500">{metaLabel(job)}</p>

                  {job.capture_certification && (
                    <p className="max-w-2xl text-xs leading-5 text-slate-500">{job.capture_certification}</p>
                  )}

                  {job.status === "failed" && job.error && (
                    <div className="max-w-2xl rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3">
                      <p className="text-xs font-semibold text-rose-700">{job.error}</p>
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap gap-2 xl:justify-end">
                  {job.export_artifact.ready && (
                    <Link
                      href={`/dashboard/${job.id}`}
                      className="rounded-xl bg-slate-950 px-4 py-2.5 text-xs font-semibold text-white transition hover:bg-slate-800"
                    >
                      Open review
                    </Link>
                  )}
                  {(job.status === "failed" || job.status === "cancelled") && (
                    <button
                      type="button"
                      disabled={retryingJobId === job.id}
                      onClick={() => void handleRetry(job.id)}
                      className="rounded-xl border border-blue-200 bg-white px-4 py-2.5 text-xs font-semibold text-blue-600 transition hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {retryingJobId === job.id ? "Retrying..." : "Retry"}
                    </button>
                  )}
                  {job.status === "queued" || job.status === "processing" ? (
                    <button
                      type="button"
                      disabled={cancellingJobId === job.id}
                      onClick={() => void handleCancel(job.id)}
                      className="rounded-xl border border-amber-200 bg-white px-4 py-2.5 text-xs font-semibold text-amber-700 transition hover:bg-amber-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {cancellingJobId === job.id ? "Cancelling..." : "Cancel"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setPendingDeleteId(job.id)}
                      className="rounded-xl border border-rose-200 bg-white px-4 py-2.5 text-xs font-semibold text-rose-600 transition hover:bg-rose-50"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>

              <div className="mt-4 space-y-3 border-t border-slate-200 pt-4">
                <div className="flex flex-wrap gap-2">
                  {visiblePipeline(job).map((step) => (
                    <span
                      key={step.key}
                      className={`inline-flex min-w-[92px] items-center justify-center rounded-full border px-3 py-1.5 text-[11px] font-semibold ${chipClass(step.status)}`}
                    >
                      {step.label} {step.cost_usd > 0 ? `(${formatCost(step.cost_usd)})` : ""}
                    </span>
                  ))}
                </div>

                {job.cost_summary && job.cost_summary.calls > 0 && (
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-slate-600">
                      <span className="font-semibold text-slate-800">
                        LLM cost: {formatCost(job.cost_summary.cost_usd)}
                      </span>
                      <span>Calls {job.cost_summary.calls}</span>
                      <span>In {formatTokens(job.cost_summary.input_tokens)} tok</span>
                      <span>Out {formatTokens(job.cost_summary.output_tokens)} tok</span>
                      {job.cost_summary.cached_input_tokens > 0 && (
                        <span>Cached {formatTokens(job.cost_summary.cached_input_tokens)} tok</span>
                      )}
                    </div>
                    {(job.cost_summary.by_stage.length > 0 || job.cost_summary.by_model.length > 0) && (
                      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
                        {job.cost_summary.by_stage.map((stage) => (
                          <span key={`stage-${stage.stage}`}>
                            {stageLabel(stage.stage)}: {formatCost(stage.cost_usd)} ({stage.calls})
                          </span>
                        ))}
                        {job.cost_summary.by_model.map((model) => (
                          <span key={`model-${model.provider}-${model.model}`} className="text-slate-400">
                            {model.model}: {formatTokens(model.input_tokens + model.output_tokens)} tok
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {parseBatchDetails(job).length > 0 ? (
                  <div className="space-y-2">
                    {parseProgressSummary(job) && <p className="text-xs font-semibold text-blue-700">{parseProgressSummary(job)}</p>}
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                      {parseBatchDetails(job).map((detail) => {
                        const running =
                          detail.includes("parsing") ||
                          detail.includes("extracting") ||
                          detail.includes("chunk") ||
                          detail.includes("merging");
                        const failed = detail.includes("failed");
                        const displayDetail = displayProgressDetail(detail);
                        return (
                          <div
                            key={detail}
                            className={`flex min-w-[240px] items-center gap-2 rounded-2xl border px-3 py-2 text-xs font-semibold ${batchDetailClass(detail)}`}
                          >
                            {running && (
                              <span className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-blue-200 border-t-blue-600" />
                            )}
                            {!running && failed && <span className="h-2 w-2 shrink-0 rounded-full bg-rose-500" />}
                            {!running && !failed && <span className="h-2 w-2 shrink-0 rounded-full bg-current opacity-60" />}
                            <span>{displayDetail}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  currentRunningDetail(job) && <p className="text-xs text-blue-600">{currentRunningDetail(job)}</p>
                )}
                {runningElapsedLabel(job) && <p className="text-xs text-slate-500">{runningElapsedLabel(job)}</p>}
              </div>
            </motion.div>
          ))}
        </div>
      </motion.section>
      </div>
    </>
  );
}
