"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ToastStack, { type ToastItem } from "@/components/ToastStack";
import UploadZone from "@/components/UploadZone";
import { usePortalShell } from "@/components/PortalShellContext";
import { deleteJob, listJobs } from "@/lib/api";
import { jobStatusBlocksNewUpload } from "@/lib/demoMode";
import type { ExtractionJobSummary, PipelineStep } from "@/lib/types";

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

function statusLabel(job: ExtractionJobSummary) {
  if (job.status === "completed") return "Ready";
  if (job.status === "failed") return "Failed";
  if (job.status === "processing") return "Processing";
  return "Queued";
}

function statusBadgeClass(status: ExtractionJobSummary["status"]) {
  if (status === "completed") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (status === "failed") return "border-rose-200 bg-rose-50 text-rose-700";
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

function visiblePipeline(job: ExtractionJobSummary) {
  return job.pipeline.map((step) => (step.key === "extract" ? { ...step, label: "Parse" } : step));
}

function currentRunningStep(job: ExtractionJobSummary) {
  return visiblePipeline(job).find((step) => step.status === "running")?.label ?? null;
}

function currentRunningDetail(job: ExtractionJobSummary) {
  return visiblePipeline(job).find((step) => step.status === "running")?.detail ?? null;
}

function runningElapsedLabel(job: ExtractionJobSummary) {
  if (job.status !== "processing") return null;

  const startedAt = Date.parse(job.created_at);
  if (Number.isNaN(startedAt)) return null;

  const elapsedMs = Date.now() - startedAt;
  const totalMinutes = Math.max(0, Math.floor(elapsedMs / 60000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours > 0) return `Running for ${hours}h ${minutes}m`;
  return `Running for ${minutes}m`;
}

export default function DashboardPage() {
  const { demoMode, setDemoExtractBusy } = usePortalShell();
  const [jobs, setJobs] = useState<ExtractionJobSummary[]>([]);
  const [error, setError] = useState("");
  const [jobsLoading, setJobsLoading] = useState(true);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const previousJobsRef = useRef<Map<string, ExtractionJobSummary>>(new Map());

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
      announceProgress(payload.jobs);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to load extraction jobs.");
    } finally {
      setJobsLoading(false);
    }
  }, [announceProgress]);

  useEffect(() => {
    void refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void refreshJobs();
    }, 5000);
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
            aria-label="Confirm delete PDF"
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
                  Do you really want to delete this PDF? This action cannot be undone.
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
        className="overflow-hidden rounded-[32px] border border-slate-200 bg-white shadow-sm"
      >
        <div className="bg-[linear-gradient(135deg,_#0f172a_0%,_#173b6d_100%)] px-6 py-8 text-white md:px-8">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-3xl">
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-sky-200">Dashboard</p>
              <h1 className="mt-3 text-3xl font-semibold tracking-tight">Simple secure document extraction</h1>
              <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-200">
                Upload medical PDFs, monitor extraction progress, and open completed reviews from one place.
              </p>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/10 px-4 py-3 text-sm text-sky-50">
              Encrypted upload. Secure storage. Fast review.
            </div>
          </div>
        </div>

        <div className="grid gap-4 px-6 py-5 md:grid-cols-3 md:px-8">
          <motion.div layout className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Total files</p>
            <p className="mt-2 text-3xl font-semibold text-slate-950">{jobs.length}</p>
          </motion.div>
          <motion.div layout className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Active now</p>
            <p className="mt-2 text-3xl font-semibold text-slate-950">{activeJobs}</p>
          </motion.div>
          <motion.div layout className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Ready</p>
            <p className="mt-2 text-3xl font-semibold text-slate-950">{readyJobs}</p>
          </motion.div>
        </div>
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
            <p className="text-lg font-semibold text-slate-950">Recent documents</p>
            <p className="mt-1 text-sm text-slate-500">Track uploads, progress, and completed outputs.</p>
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
                Choose PDF file to create first extraction job.
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
                  <button
                    type="button"
                    onClick={() => setPendingDeleteId(job.id)}
                    className="rounded-xl border border-rose-200 bg-white px-4 py-2.5 text-xs font-semibold text-rose-600 transition hover:bg-rose-50"
                  >
                    Delete
                  </button>
                </div>
              </div>

              <div className="mt-4 space-y-3 border-t border-slate-200 pt-4">
                <div className="flex flex-wrap gap-2">
                  {visiblePipeline(job).map((step) => (
                    <span
                      key={step.key}
                      className={`inline-flex min-w-[76px] items-center justify-center rounded-full border px-3 py-1.5 text-[11px] font-semibold ${chipClass(step.status)}`}
                    >
                      {step.label}
                    </span>
                  ))}
                </div>

                {currentRunningDetail(job) && <p className="text-xs text-blue-600">{currentRunningDetail(job)}</p>}
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
