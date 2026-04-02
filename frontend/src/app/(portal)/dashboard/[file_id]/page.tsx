"use client";

import { useEffect, useState } from "react";

import DocumentReviewPanel from "@/components/DocumentReviewPanel";
import { getJob } from "@/lib/api";
import type { ExtractionJobDetail } from "@/lib/types";

type Props = {
  params: {
    file_id: string;
  };
};

export default function FileReviewPage({ params }: Props) {
  const [job, setJob] = useState<ExtractionJobDetail | null>(null);
  const [error, setError] = useState("");
  const shouldPoll = !job || (!job.export_artifact.ready && job.status !== "failed");

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const payload = await getJob(params.file_id);
        if (!cancelled) {
          setJob((current) => {
            if (
              current &&
              current.id === payload.id &&
              current.updated_at === payload.updated_at &&
              current.status === payload.status &&
              current.export_artifact.ready === payload.export_artifact.ready
            ) {
              return current;
            }
            return payload;
          });
          setError("");
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Unable to load the extraction review.");
        }
      }
    };

    void load();
    if (!shouldPoll) {
      return () => {
        cancelled = true;
      };
    }

    const interval = window.setInterval(() => {
      void load();
    }, 5000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [params.file_id, shouldPoll]);

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      {error && (
        <div className="rounded-3xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-600">
          {error}
        </div>
      )}

      {!job && !error && (
        <div className="rounded-3xl border border-slate-200 bg-white px-6 py-10 text-sm text-slate-500 shadow-sm">
          Loading extraction review...
        </div>
      )}

      {job && <DocumentReviewPanel job={job} backHref="/dashboard" />}
    </div>
  );
}
