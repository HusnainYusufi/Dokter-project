"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import dynamic from "next/dynamic";
import type { Components } from "react-markdown";

const MermaidDiagram = dynamic(() => import("./MermaidDiagram"), { ssr: false });

interface Props {
  pageNumber: number;
  content: string;
}

const mdComponents: Components = {
  code({ className, children, ...props }) {
    const language = /language-(\w+)/.exec(className ?? "")?.[1];
    const code = String(children).trim();

    if (language === "mermaid") {
      return <MermaidDiagram code={code} />;
    }

    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
  table({ children }) {
    return (
      <div className="overflow-x-auto my-4">
        <table className="min-w-full border-collapse text-sm">{children}</table>
      </div>
    );
  },
  th({ children }) {
    return (
      <th className="border border-border bg-subtle px-3 py-2 text-left text-xs font-semibold text-white">
        {children}
      </th>
    );
  },
  td({ children }) {
    return (
      <td className="border border-border px-3 py-2 text-xs text-gray-300">
        {children}
      </td>
    );
  },
};

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
          <span className="text-sm font-medium text-white">Page {pageNumber}</span>
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
            <div className="prose prose-invert prose-sm max-w-none prose-headings:text-white prose-p:text-gray-300 prose-strong:text-white prose-code:text-accent prose-pre:bg-surface prose-pre:border prose-pre:border-border">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={mdComponents}
              >
                {content}
              </ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
