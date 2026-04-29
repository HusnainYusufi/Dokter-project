import type { ExtractionJobSummary } from "@/lib/types";

export function formatCost(value: number | null | undefined) {
  const amount = Number(value || 0);
  if (amount <= 0) return "$0.000000";
  if (amount < 0.0001) return `$${amount.toFixed(6)}`;
  return `$${amount.toFixed(4)}`;
}

export function formatTokens(value: number | null | undefined) {
  const n = Number(value || 0);
  if (n <= 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

export function stageLabel(stage: string) {
  if (stage === "parse") return "Parse";
  if (stage === "summarize") return "LLM";
  return stage;
}

export function startOfWeek(date = new Date()) {
  const weekStart = new Date(date);
  const day = weekStart.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  weekStart.setDate(weekStart.getDate() + diff);
  weekStart.setHours(0, 0, 0, 0);
  return weekStart;
}

export function jobCost(job: ExtractionJobSummary) {
  return Number(job.cost_summary?.cost_usd ?? 0);
}

export function isJobInWeek(job: ExtractionJobSummary, weekStart = startOfWeek()) {
  const timestamp = Date.parse(job.updated_at || job.created_at);
  return !Number.isNaN(timestamp) && timestamp >= weekStart.getTime();
}
