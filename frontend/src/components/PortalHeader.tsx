"use client";

import { signOut } from "next-auth/react";

export default function PortalHeader() {
  return (
    <header className="border-b border-slate-800 bg-slate-950/95 text-white backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-teal-400 to-blue-700 shadow-lg shadow-blue-950/50">
            <svg className="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m6-6H6" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M7 4h6.586a1 1 0 01.707.293l3.414 3.414A1 1 0 0118 8.414V18a2 2 0 01-2 2H8a2 2 0 01-2-2V6a2 2 0 012-2z" />
            </svg>
          </div>
          <div>
            <p className="text-lg font-semibold tracking-wide text-white">Medical Intelligence</p>
            <p className="text-xs uppercase tracking-[0.28em] text-slate-400">Medical | Legal | Intelligence</p>
          </div>
        </div>

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
