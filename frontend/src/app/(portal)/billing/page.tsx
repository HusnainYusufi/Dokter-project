"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { listJobs } from "@/lib/api";
import { formatCost, formatTokens, isJobInWeek, jobCost, stageLabel, startOfWeek } from "@/lib/costing";
import type { ExtractionJobSummary } from "@/lib/types";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function statusLabel(job: ExtractionJobSummary) {
  if (job.status === "completed") return "Ready";
  if (job.status === "failed") return "Failed";
  if (job.status === "cancelled") return "Cancelled";
  if (job.status === "processing") return "Processing";
  return "Queued";
}

export default function BillingPage() {
  const [jobs, setJobs] = useState<ExtractionJobSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function refreshBilling() {
    setLoading(true);
    try {
      const payload = await listJobs();
      setJobs(payload.jobs);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load billing data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshBilling();
  }, []);

  const weekStart = useMemo(() => startOfWeek(), []);
  const billedJobs = useMemo(() => jobs.filter((job) => job.cost_summary && job.cost_summary.calls > 0), [jobs]);
  const weeklyJobs = useMemo(() => billedJobs.filter((job) => isJobInWeek(job, weekStart)), [billedJobs, weekStart]);
  const weeklyCost = useMemo(() => weeklyJobs.reduce((total, job) => total + jobCost(job), 0), [weeklyJobs]);
  const totalCost = useMemo(() => billedJobs.reduce((total, job) => total + jobCost(job), 0), [billedJobs]);
  const weeklyCalls = useMemo(() => weeklyJobs.reduce((total, job) => total + (job.cost_summary?.calls ?? 0), 0), [weeklyJobs]);

  const jobsByCost = useMemo(
    () => [...billedJobs].sort((left, right) => jobCost(right) - jobCost(left)),
    [billedJobs],
  );

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <section className="rounded-[32px] border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-col gap-4 border-b border-slate-200 px-6 py-6 md:flex-row md:items-center md:justify-between md:px-8">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">Billing</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">LLM costs</h2>
            <p className="mt-3 text-sm leading-7 text-slate-500">
              Review total LLM spend this week and cost per extraction job.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void refreshBilling()}
            disabled={loading}
            className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>

        {error && <div className="border-b border-rose-200 bg-rose-50 px-6 py-3 text-sm text-rose-700">{error}</div>}

        <div className="grid gap-4 p-4 md:grid-cols-3 md:p-6">
          <div className="rounded-[24px] border border-slate-200 bg-slate-50 px-5 py-5">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">This week</p>
            <p className="mt-3 text-3xl font-semibold text-slate-950">{formatCost(weeklyCost)}</p>
            <p className="mt-2 text-xs text-slate-500">Since {formatDate(weekStart.toISOString())}</p>
          </div>
          <div className="rounded-[24px] border border-slate-200 bg-slate-50 px-5 py-5">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Weekly calls</p>
            <p className="mt-3 text-3xl font-semibold text-slate-950">{weeklyCalls.toLocaleString()}</p>
            <p className="mt-2 text-xs text-slate-500">{weeklyJobs.length} billed run{weeklyJobs.length === 1 ? "" : "s"}</p>
          </div>
          <div className="rounded-[24px] border border-slate-200 bg-slate-50 px-5 py-5">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">All tracked</p>
            <p className="mt-3 text-3xl font-semibold text-slate-950">{formatCost(totalCost)}</p>
            <p className="mt-2 text-xs text-slate-500">{billedJobs.length} billed job{billedJobs.length === 1 ? "" : "s"}</p>
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-[32px] border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-col gap-3 border-b border-slate-200 px-6 py-5 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-lg font-semibold text-slate-950">Cost per job run</p>
            <p className="mt-1 text-sm text-slate-500">Sorted by LLM cost, highest first.</p>
          </div>
          <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-600">
            {jobsByCost.length} run{jobsByCost.length === 1 ? "" : "s"}
          </div>
        </div>

        {loading && <div className="px-6 py-10 text-sm text-slate-500">Loading billing data...</div>}

        {!loading && jobsByCost.length === 0 && (
          <div className="px-6 py-14 text-center">
            <p className="text-base font-semibold text-slate-900">No LLM costs yet</p>
            <p className="mt-2 text-sm text-slate-500">Run a summarizer job to capture billing figures.</p>
          </div>
        )}

        <div className="divide-y divide-slate-200">
          {jobsByCost.map((job) => {
            const summary = job.cost_summary;
            if (!summary) return null;

            return (
              <div key={job.id} className="p-5 md:p-6">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-3">
                      <p className="truncate text-base font-semibold text-slate-950">{job.filename}</p>
                      <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-[11px] font-semibold text-slate-600">
                        {statusLabel(job)}
                      </span>
                      {isJobInWeek(job, weekStart) && (
                        <span className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-[11px] font-semibold text-blue-700">
                          This week
                        </span>
                      )}
                    </div>
                    <p className="mt-2 text-sm text-slate-500">
                      Updated {formatDate(job.updated_at)} | {job.page_count} page{job.page_count === 1 ? "" : "s"}
                    </p>
                  </div>

                  <div className="text-left xl:text-right">
                    <p className="text-2xl font-semibold text-slate-950">{formatCost(summary.cost_usd)}</p>
                    <p className="mt-1 text-xs text-slate-500">{summary.calls.toLocaleString()} LLM call{summary.calls === 1 ? "" : "s"}</p>
                  </div>
                </div>

                <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.6fr)]">
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs">
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-slate-600">
                      <span>In {formatTokens(summary.input_tokens)} tok</span>
                      <span>Out {formatTokens(summary.output_tokens)} tok</span>
                      {summary.cached_input_tokens > 0 && <span>Cached {formatTokens(summary.cached_input_tokens)} tok</span>}
                    </div>
                    {summary.by_stage.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
                        {summary.by_stage.map((stage) => (
                          <span key={`${job.id}-${stage.stage}`}>
                            {stageLabel(stage.stage)}: {formatCost(stage.cost_usd)} ({stage.calls})
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-[11px] text-slate-500">
                    {summary.by_model.length > 0 ? (
                      <div className="space-y-1">
                        {summary.by_model.map((model) => (
                          <p key={`${job.id}-${model.provider}-${model.model}`}>
                            <span className="font-semibold text-slate-700">{model.model}</span>: {formatCost(model.cost_usd)} |{" "}
                            {formatTokens(model.input_tokens + model.output_tokens)} tok
                          </p>
                        ))}
                      </div>
                    ) : (
                      <p>No model breakdown logged.</p>
                    )}
                  </div>
                </div>

                <div className="mt-4">
                  <Link href={`/dashboard/${job.id}`} className="text-xs font-semibold text-blue-600 hover:text-blue-700">
                    Open job review
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
