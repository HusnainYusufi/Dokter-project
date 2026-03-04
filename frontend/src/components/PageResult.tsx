"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface Props {
  pageNumber: number;
  content: string;
}

export default function PageResult({ pageNumber, content }: Props) {
  const [open, setOpen] = useState(true);
  const failed = content.startsWith("[Failed to parse page");

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      {/* Header */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-5 py-3.5 text-left transition hover:bg-subtle/30"
      >
        <div className="flex items-center gap-3">
          <span
            className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold ${
              failed ? "bg-red-500/20 text-red-400" : "bg-accent/20 text-accent"
            }`}
          >
            {pageNumber}
          </span>
          <span className="text-sm font-medium text-white">
            Page {pageNumber}
          </span>
          {failed && (
            <span className="rounded-full bg-red-500/15 px-2 py-0.5 text-[10px] font-medium text-red-400">
              Parse Error
            </span>
          )}
        </div>
        <svg
          className={`h-4 w-4 text-muted transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Body */}
      {open && (
        <div className="border-t border-border px-5 py-4">
          {failed ? (
            <p className="text-sm text-red-400">{content}</p>
          ) : (
            <div className="prose prose-invert prose-sm max-w-none prose-headings:text-white prose-p:text-gray-300 prose-strong:text-white prose-code:text-accent prose-pre:bg-surface prose-pre:border prose-pre:border-border prose-table:text-gray-300 prose-th:text-white">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
