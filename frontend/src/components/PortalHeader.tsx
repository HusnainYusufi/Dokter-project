"use client";

import { signOut } from "next-auth/react";

import BrandLogo from "@/components/BrandLogo";

export default function PortalHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-slate-800 bg-slate-950/95 text-white backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
        <BrandLogo theme="dark" />

        <div className="flex items-center gap-3">
          <div className="hidden rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-300 sm:block">
            End-to-end encrypted storage
          </div>
          <button
            onClick={() => signOut({ callbackUrl: "/login" })}
            className="rounded-full border border-slate-700 px-4 py-2 text-sm font-medium text-slate-200 transition hover:border-slate-500 hover:bg-slate-900"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
