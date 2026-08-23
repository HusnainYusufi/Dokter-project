"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";

import BrandLogo from "@/components/BrandLogo";

const NAV_ITEMS = [
  {
    href: "/dashboard",
    label: "Summarizer",
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6M7 4h7l5 5v11a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2z" />
      </svg>
    ),
  },
  {
    href: "/vault",
    label: "Vault",
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 7h5l2 2h11v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
      </svg>
    ),
  },
  {
    href: "/billing",
    label: "Billing",
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m4-8.5C15.2 8.4 13.9 8 12.4 8c-2 0-3.4.9-3.4 2.3 0 1.5 1.5 2 3.4 2.4 2 .5 3.6 1 3.6 2.6 0 1.5-1.5 2.7-3.8 2.7-1.8 0-3.3-.6-4.2-1.7" />
      </svg>
    ),
  },
  {
    href: "/rule-studio",
    label: "Rule Studio",
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
        />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
  {
    href: "/llm-dev",
    label: "LLM Dev",
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 7h16M4 12h10M4 17h16" />
      </svg>
    ),
  },
];

export default function PortalSidebar() {
  const pathname = usePathname();

  return (
    <aside className="border-b border-slate-200 bg-white lg:sticky lg:top-0 lg:h-screen lg:w-64 lg:shrink-0 lg:border-b-0 lg:border-r">
      <div className="flex h-full flex-col p-3">
        <div className="flex items-center gap-3 rounded-lg px-3 py-3">
          <BrandLogo compact theme="light" iconSizeClassName="h-9 w-9" />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-slate-950">Medical Intelligence</p>
            <p className="text-xs text-slate-500">Workspace</p>
          </div>
        </div>

        <div className="mt-4 px-3 text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">Navigation</div>

        <nav className="mt-2 grid gap-1">
          {NAV_ITEMS.map((item) => {
            const active = item.href === "/dashboard" ? pathname.startsWith("/dashboard") : pathname.startsWith(item.href);

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                  active ? "bg-slate-100 text-slate-950" : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
                }`}
              >
                <span
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${
                    active ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-500"
                  }`}
                >
                  {item.icon}
                </span>
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto border-t border-slate-200 pt-3">
          <button
            onClick={() => signOut({ callbackUrl: "/login" })}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50 hover:text-slate-950"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-md bg-slate-100 text-slate-500">
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H9m4 8H7a2 2 0 01-2-2V6a2 2 0 012-2h6" />
              </svg>
            </span>
            Sign out
          </button>
        </div>
      </div>
    </aside>
  );
}
