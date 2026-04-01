"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import DocumentReviewPanel from "@/components/DocumentReviewPanel";
import UploadZone from "@/components/UploadZone";
import { buildDownloadUrl, deleteJob, getJob, listJobs } from "@/lib/api";
import type { ExtractionJobDetail, ExtractionJobSummary, PipelineStep } from "@/lib/types";

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
  if (job.status === "failed") return "Needs review";
  if (job.status === "processing") return "Processing";
  return "Queued";
}

export default function DashboardPage() {
  const [jobs, setJobs] = useState<ExtractionJobSummary[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<ExtractionJobDetail | null>(null);
  const [error, setError] = useState("");
  const [jobsLoading, setJobsLoading] = useState(true);

  const refreshJobs = useCallback(async () => {
    try {
      const payload = await listJobs();
      setJobs(payload.jobs);
      setSelectedJobId((current) => current ?? payload.jobs[0]?.id ?? null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to load extraction jobs.");
    } finally {
      setJobsLoading(false);
    }
  }, []);

  const refreshSelectedJob = useCallback(async () => {
    if (!selectedJobId) {
      setSelectedJob(null);
      return;
    }

    try {
      const payload = await getJob(selectedJobId);
      setSelectedJob(payload);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to load extraction details.");
    }
  }, [selectedJobId]);

  useEffect(() => {
    void refreshJobs();
  }, [refreshJobs]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void refreshJobs();
    }, 5000);
    return () => window.clearInterval(interval);
  }, [refreshJobs]);

  useEffect(() => {
    void refreshSelectedJob();
    if (!selectedJobId) return;
    const interval = window.setInterval(() => {
      void refreshSelectedJob();
    }, 5000);
    return () => window.clearInterval(interval);
  }, [refreshSelectedJob, selectedJobId]);

  useEffect(() => {
    if (!jobs.length) {
      setSelectedJob(null);
      setSelectedJobId(null);
      return;
    }

    if (selectedJobId && jobs.some((job) => job.id === selectedJobId)) return;
    setSelectedJobId(jobs[0].id);
  }, [jobs, selectedJobId]);

  const activeJobs = useMemo(
    () => jobs.filter((job) => job.status === "queued" || job.status === "processing").length,
    [jobs],
  );

  function handleUploaded(job: ExtractionJobSummary) {
    setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    setSelectedJobId(job.id);
    setError("");
  }

  async function handleDelete(jobId: string) {
    if (!window.confirm("Delete this extraction job and all encrypted artifacts?")) return;

    try {
      await deleteJob(jobId);
      setJobs((current) => current.filter((job) => job.id !== jobId));
      if (selectedJobId === jobId) {
        setSelectedJob(null);
        setSelectedJobId(null);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to delete the extraction job.");
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
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
        <div className="grid grid-cols-1 gap-3 border-b border-slate-200 bg-slate-50 px-5 py-4 text-xs font-semibold uppercase tracking-[0.24em] text-slate-500 md:grid-cols-[1.2fr_1.4fr_0.7fr]">
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
            const active = job.id === selectedJobId;
            return (
              <div
                key={job.id}
                role="button"
                tabIndex={0}
                onClick={() => setSelectedJobId(job.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedJobId(job.id);
                  }
                }}
                className={`grid w-full grid-cols-1 gap-4 px-5 py-5 text-left transition md:grid-cols-[1.2fr_1.4fr_0.7fr] ${
                  active ? "bg-blue-50/70" : "hover:bg-slate-50"
                }`}
              >
                <div className="min-w-0">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-950">{job.filename}</p>
                      <p className="mt-1 text-xs text-slate-500">
                        Updated {formatDate(job.updated_at)} | {job.page_count || 0} pages | {statusLabel(job)}
                      </p>
                    </div>
                    {job.status === "failed" && (
                      <span className="rounded-full bg-rose-100 px-3 py-1 text-[11px] font-semibold text-rose-700">
                        Failed
                      </span>
                    )}
                  </div>
                  {job.capture_certification && (
                    <p className="mt-3 text-xs leading-5 text-slate-500">{job.capture_certification}</p>
                  )}
                </div>

                <div className="flex flex-wrap gap-2">
                  {job.pipeline.map((step) => (
                    <span
                      key={step.key}
                      className={`inline-flex items-center rounded-full border px-3 py-1 text-[11px] font-semibold ${chipClass(step.status)}`}
                    >
                      {step.label}
                    </span>
                  ))}
                </div>

                <div className="flex flex-wrap items-start justify-start gap-2 md:justify-end">
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      setSelectedJobId(job.id);
                    }}
                    className="rounded-full bg-slate-900 px-3 py-2 text-xs font-semibold text-white"
                  >
                    Review
                  </button>
                  <a
                    href={buildDownloadUrl(job.id)}
                    onClick={(event) => {
                      event.stopPropagation();
                      if (!job.export_artifact.ready) event.preventDefault();
                    }}
                    className={`rounded-full px-3 py-2 text-xs font-semibold transition ${
                      job.export_artifact.ready
                        ? "bg-slate-100 text-slate-700 hover:bg-slate-200"
                        : "cursor-not-allowed bg-slate-100 text-slate-400"
                    }`}
                  >
                    Download
                  </a>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      void handleDelete(job.id);
                    }}
                    className="rounded-full bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-600 transition hover:bg-rose-100"
                  >
                    Delete
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {selectedJob && <DocumentReviewPanel job={selectedJob} />}
    </div>
  );
}
