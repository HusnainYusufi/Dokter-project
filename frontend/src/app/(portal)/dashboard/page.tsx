"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ToastStack, { type ToastItem } from "@/components/ToastStack";
import UploadZone from "@/components/UploadZone";
import { deleteJob, listJobs } from "@/lib/api";
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

function metaLabel(job: ExtractionJobSummary) {
  const parts = [`Updated ${formatDate(job.updated_at)}`];

  if (job.page_count > 0) {
    parts.push(`${job.page_count} page${job.page_count === 1 ? "" : "s"}`);
  }

  parts.push(statusLabel(job));
  return parts.join(" | ");
}

function currentRunningStep(job: ExtractionJobSummary) {
  return job.pipeline.find((step) => step.status === "running")?.label ?? null;
}

function currentRunningDetail(job: ExtractionJobSummary) {
  return job.pipeline.find((step) => step.status === "running")?.detail ?? null;
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
  const [jobs, setJobs] = useState<ExtractionJobSummary[]>([]);
  const [error, setError] = useState("");
  const [jobsLoading, setJobsLoading] = useState(true);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
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
            pushToast(`${job.filename}: extraction started in the background.`, "info");
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
      console.log(
        "[DashboardPage] jobs refreshed",
        payload.jobs.map((job) => ({
          id: job.id,
          status: job.status,
          runningStep: currentRunningStep(job),
          detail: currentRunningDetail(job),
        })),
      );
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

  function handleUploaded(job: ExtractionJobSummary) {
    setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    setError("");
    pushToast(`${job.filename}: upload received. Extracting document in the background.`, "info");
  }

  async function handleDelete(jobId: string) {
    if (!window.confirm("Delete this extraction job and all encrypted artifacts?")) return;

    try {
      await deleteJob(jobId);
      setJobs((current) => current.filter((job) => job.id !== jobId));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to delete the extraction job.");
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <ToastStack toasts={toasts} />

      <section className="rounded-[32px] bg-[linear-gradient(135deg,_#0f172a_0%,_#0f2f57_45%,_#12396b_100%)] px-6 py-7 text-white shadow-xl shadow-slate-300/40 md:px-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <p className="text-sm font-semibold uppercase tracking-[0.28em] text-sky-200">Dashboard</p>
            <h1 className="mt-4 text-3xl font-semibold">Encrypted extraction and document-level review</h1>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-200">
              Upload sensitive medical PDFs, detect patient/document boundaries, inspect page-wise capture, and download a
              Word-compatible summary without leaving the secure portal.
            </p>
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-3xl border border-white/10 bg-white/10 px-4 py-4">
              <p className="text-xs uppercase tracking-[0.2em] text-sky-100">Files</p>
              <p className="mt-2 text-3xl font-semibold">{jobs.length}</p>
            </div>
            <div className="rounded-3xl border border-white/10 bg-white/10 px-4 py-4">
              <p className="text-xs uppercase tracking-[0.2em] text-sky-100">Active</p>
              <p className="mt-2 text-3xl font-semibold">{activeJobs}</p>
            </div>
            <div className="rounded-3xl border border-white/10 bg-white/10 px-4 py-4">
              <p className="text-xs uppercase tracking-[0.2em] text-sky-100">Security</p>
              <p className="mt-2 text-sm font-semibold leading-6 text-sky-50">Encrypted upload, encrypted artifacts, limited retention.</p>
            </div>
          </div>
        </div>
      </section>

      <UploadZone onUploaded={handleUploaded} onError={setError} />

      {error && (
        <div className="flex items-start gap-3 rounded-3xl border border-rose-200 bg-rose-50 px-5 py-4">
          <svg className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <div>
            <p className="text-sm font-medium text-rose-700">Something needs attention</p>
            <p className="mt-0.5 text-xs text-rose-600">{error}</p>
          </div>
        </div>
      )}

      <section className="overflow-hidden rounded-[32px] border border-slate-200 bg-white shadow-sm">
        <div className="grid grid-cols-1 gap-3 border-b border-slate-200 bg-slate-50 px-6 py-4 text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 md:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)_auto]">
          <p>File</p>
          <p>Pipeline</p>
          <p>Actions</p>
        </div>

        {jobsLoading && (
          <div className="px-5 py-8 text-sm text-slate-500">Loading extraction queue...</div>
        )}

        {!jobsLoading && jobs.length === 0 && (
          <div className="px-5 py-12 text-center text-sm text-slate-500">
            No documents uploaded yet. Start with a PDF to create the first extraction job.
          </div>
        )}

        <div className="divide-y divide-slate-200">
          {jobs.map((job) => {
            return (
              <div
                key={job.id}
                className="grid w-full grid-cols-1 gap-5 px-6 py-5 text-left transition hover:bg-slate-50 md:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)_auto]"
              >
                <div className="min-w-0 space-y-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <p className="truncate text-sm font-semibold text-slate-950">{job.filename}</p>
                    {job.status === "failed" && (
                      <span className="inline-flex rounded-full bg-rose-100 px-3 py-1 text-[11px] font-semibold text-rose-700">
                        Failed
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-500">{metaLabel(job)}</p>
                  {job.capture_certification && (
                    <p className="max-w-xl text-xs leading-5 text-slate-500">{job.capture_certification}</p>
                  )}
                  {job.status === "failed" && job.error && (
                    <div className="max-w-xl rounded-2xl border border-rose-200 bg-rose-50 px-3.5 py-3">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-rose-500">Failure Reason</p>
                      <p className="mt-1 text-xs leading-5 text-rose-700">{job.error}</p>
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap items-start gap-2">
                  {job.pipeline.map((step) => (
                    <span
                      key={step.key}
                      className={`inline-flex min-w-[76px] items-center justify-center rounded-full border px-3 py-1.5 text-[11px] font-semibold ${chipClass(step.status)}`}
                    >
                      {step.label}
                    </span>
                  ))}
                  {currentRunningDetail(job) && (
                    <p className="w-full pt-1 text-xs text-blue-600">{currentRunningDetail(job)}</p>
                  )}
                  {runningElapsedLabel(job) && (
                    <p className="w-full text-[11px] text-slate-500">{runningElapsedLabel(job)}</p>
                  )}
                </div>

                <div className="flex flex-wrap items-start justify-start gap-2 md:justify-end">
                  {job.export_artifact.ready && (
                    <Link
                      href={`/dashboard/${job.id}`}
                      className="rounded-full bg-slate-900 px-4 py-2 text-xs font-semibold text-white"
                    >
                      View
                    </Link>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      void handleDelete(job.id);
                    }}
                    className="rounded-full bg-rose-50 px-4 py-2 text-xs font-semibold text-rose-600 transition hover:bg-rose-100"
                  >
                    Delete
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
