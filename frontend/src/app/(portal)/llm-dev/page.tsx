"use client";

import { useEffect, useMemo, useState } from "react";

import { getLlmRunEntries, listLlmRuns } from "@/lib/api";
import type { LlmRunEntriesResponse, LlmRunStage, LlmRunSummary } from "@/lib/types";

function formatBytes(value: number) {
  if (!value) return "0 KB";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatLogTime(value: number | null | undefined) {
  if (!value) return "Never";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

function entryLabel(entry: Record<string, unknown>, index: number) {
  const task = typeof entry.task_label === "string" ? entry.task_label : `Entry ${index + 1}`;
  const process = typeof entry.process === "string" ? entry.process : "";
  const direction = typeof entry.direction === "string" ? entry.direction : "";
  return [task, process, direction].filter(Boolean).join(" | ");
}

export default function LlmDevPage() {
  const [runs, setRuns] = useState<LlmRunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [stage, setStage] = useState<LlmRunStage>("parse");
  const [entriesPayload, setEntriesPayload] = useState<LlmRunEntriesResponse | null>(null);
  const [selectedEntryIndex, setSelectedEntryIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadRuns() {
      setLoading(true);
      try {
        const payload = await listLlmRuns();
        setRuns(payload.runs);
        setSelectedRunId((current) => current || payload.runs[0]?.id || "");
        setError("");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unable to load LLM runs.");
      } finally {
        setLoading(false);
      }
    }

    void loadRuns();
  }, []);

  useEffect(() => {
    async function loadEntries() {
      if (!selectedRunId) {
        setEntriesPayload(null);
        return;
      }
      setLoading(true);
      try {
        const payload = await getLlmRunEntries(selectedRunId, stage, 0, 100);
        setEntriesPayload(payload);
        setSelectedEntryIndex(0);
        setError("");
      } catch (err) {
        setEntriesPayload(null);
        setError(err instanceof Error ? err.message : "Unable to load log file.");
      } finally {
        setLoading(false);
      }
    }

    void loadEntries();
  }, [selectedRunId, stage]);

  const selectedRun = useMemo(() => runs.find((run) => run.id === selectedRunId) ?? null, [runs, selectedRunId]);
  const entries = entriesPayload?.entries ?? [];
  const selectedEntry = entries[selectedEntryIndex] ?? null;

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <section className="rounded-[32px] border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-200 px-6 py-6 md:px-8">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">LLM dev</p>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">Run logs</h2>
          <p className="mt-3 text-sm leading-7 text-slate-500">
            Explore parse and summarize inputs, outputs, models, and parsed JSON for each extraction run.
          </p>
        </div>

        {error && <div className="border-b border-rose-200 bg-rose-50 px-6 py-3 text-sm text-rose-700">{error}</div>}

        <div className="grid min-h-[42rem] lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="border-b border-slate-200 bg-slate-50 p-4 lg:border-b-0 lg:border-r">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-semibold text-slate-950">Runs</p>
              <button
                type="button"
                onClick={() => void listLlmRuns().then((payload) => setRuns(payload.runs))}
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-50"
              >
                Refresh
              </button>
            </div>

            <div className="mt-4 space-y-2">
              {runs.map((run) => (
                <button
                  key={run.id}
                  type="button"
                  onClick={() => setSelectedRunId(run.id)}
                  className={`w-full rounded-xl border px-3 py-3 text-left text-sm transition ${
                    run.id === selectedRunId ? "border-blue-200 bg-blue-50" : "border-slate-200 bg-white hover:bg-slate-50"
                  }`}
                >
                  <p className="font-semibold text-slate-950">{run.id}</p>
                  <p className="mt-1 text-xs text-slate-500">Updated {formatLogTime(run.updated_at)}</p>
                </button>
              ))}
              {!loading && runs.length === 0 && <p className="rounded-xl border border-dashed border-slate-200 p-4 text-sm text-slate-500">No run logs yet.</p>}
            </div>
          </aside>

          <main className="min-w-0 p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-slate-950">{selectedRun?.id ?? "No run selected"}</p>
                {selectedRun && (
                  <p className="mt-1 text-xs text-slate-500">
                    Parse {formatBytes(selectedRun.files.parse.size_bytes)} | Summarize {formatBytes(selectedRun.files.summarize.size_bytes)}
                  </p>
                )}
              </div>

              <div className="flex rounded-xl border border-slate-200 bg-white p-1">
                {(["parse", "summarize"] as LlmRunStage[]).map((item) => (
                  <button
                    key={item}
                    type="button"
                    onClick={() => setStage(item)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-semibold capitalize ${
                      item === stage ? "bg-slate-950 text-white" : "text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-5 grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
              <div className="max-h-[34rem] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-2">
                {entries.map((entry, index) => (
                  <button
                    key={`${index}-${entry.timestamp ?? ""}`}
                    type="button"
                    onClick={() => setSelectedEntryIndex(index)}
                    className={`block w-full rounded-xl px-3 py-2 text-left text-xs transition ${
                      index === selectedEntryIndex ? "bg-blue-50 text-blue-900" : "hover:bg-slate-50"
                    }`}
                  >
                    <p className="font-semibold">{entryLabel(entry, index)}</p>
                    <p className="mt-1 text-slate-500">{typeof entry.timestamp === "string" ? entry.timestamp : ""}</p>
                  </button>
                ))}
                {!loading && entries.length === 0 && <p className="p-4 text-sm text-slate-500">No entries in this file.</p>}
              </div>

              <pre className="max-h-[34rem] overflow-auto rounded-2xl border border-slate-200 bg-slate-950 p-4 text-xs leading-6 text-slate-100">
                {selectedEntry ? JSON.stringify(selectedEntry, null, 2) : "Select an entry."}
              </pre>
            </div>
          </main>
        </div>
      </section>
    </div>
  );
}
