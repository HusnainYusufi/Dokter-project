"use client";

import { usePathname } from "next/navigation";

import { usePortalShell, type PageHeaderCopy } from "@/components/PortalShellContext";

// Default copy per route section. Matched by longest PREFIX, not by exact
// pathname, so nested routes such as /dashboard/<job id> resolve to their
// section deliberately rather than falling through to an unrelated page.
const PAGE_COPY: Record<string, PageHeaderCopy> = {
  "/dashboard": {
    title: "Summarizer",
    subtitle: "Select files from your encrypted vault, then run medical summary extraction.",
    badge: "Encrypted storage",
  },
  "/vault": {
    title: "Vault",
    subtitle: "Browse, preview, organize, and manage encrypted source files.",
    badge: "Encrypted storage",
  },
  "/rule-studio": {
    title: "Rule Studio",
    subtitle: "Author the golden rules and per-document-type behavior the AI follows.",
    badge: null,
  },
  "/billing": {
    title: "Billing",
    subtitle: "Track LLM spend this week and per summarizer job run.",
    badge: null,
  },
  "/llm-dev": {
    title: "LLM Dev",
    subtitle: "Inspect model inputs, outputs, and run logs.",
    badge: null,
  },
};

const FALLBACK_COPY: PageHeaderCopy = {
  title: "Workspace",
  subtitle: "",
  badge: null,
};

function copyForPath(pathname: string): PageHeaderCopy {
  const match = Object.keys(PAGE_COPY)
    .filter((route) => pathname === route || pathname.startsWith(`${route}/`))
    .sort((left, right) => right.length - left.length)[0];
  return match ? PAGE_COPY[match] : FALLBACK_COPY;
}

export default function PortalHeader() {
  const pathname = usePathname();
  const { headerCopy } = usePortalShell();
  // A page that registered its own copy wins; everything else keeps the
  // route defaults, so pages need no changes to keep working.
  const copy = headerCopy ?? copyForPath(pathname);

  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-slate-100/90 backdrop-blur">
      <div className="flex items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">Medical intelligence workspace</p>
          <h1 className="mt-1 truncate text-2xl font-semibold text-slate-950">{copy.title}</h1>
          {copy.subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-500">{copy.subtitle}</p>}
        </div>

        {copy.badge && (
          <div className="hidden shrink-0 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 lg:block">
            {copy.badge}
          </div>
        )}
      </div>
    </header>
  );
}
