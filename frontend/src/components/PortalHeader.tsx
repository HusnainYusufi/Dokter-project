"use client";

import { usePathname } from "next/navigation";

const PAGE_COPY: Record<string, { title: string; subtitle: string }> = {
  "/dashboard": {
    title: "Summarizer",
    subtitle: "Select files from your encrypted vault, then run medical summary extraction.",
  },
  "/vault": {
    title: "Vault",
    subtitle: "Browse, preview, organize, and manage encrypted source files.",
  },
};

export default function PortalHeader() {
  const pathname = usePathname();
  const copy = PAGE_COPY[pathname] ?? PAGE_COPY["/dashboard"];

  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-slate-100/90 backdrop-blur">
      <div className="flex items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-400">Medical intelligence workspace</p>
          <h1 className="mt-1 truncate text-2xl font-semibold text-slate-950">{copy.title}</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-500">{copy.subtitle}</p>
        </div>

        <div className="hidden rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700 lg:block">
          Encrypted storage
        </div>
      </div>
    </header>
  );
}
