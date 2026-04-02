"use client";

type ComplianceStripProps = {
  variant?: "light" | "dark";
};

const BADGES = [
  {
    title: "ISO 27001",
    description: "Enterprise-grade controls and retention discipline.",
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 3l7 3v5c0 5-3.5 8.5-7 10-3.5-1.5-7-5-7-10V6l7-3z" />
      </svg>
    ),
  },
  {
    title: "SOC 2",
    description: "Operational integrity and privacy monitoring for secure handling.",
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5 2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    title: "HIPAA",
    description: "Protected health information processed with strict safeguards.",
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 11c1.657 0 3-1.567 3-3.5S13.657 4 12 4 9 5.567 9 7.5 10.343 11 12 11z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M7 20v-1a5 5 0 0110 0v1" />
      </svg>
    ),
  },
  {
    title: "Encrypted",
    description: "Uploaded documents and generated artifacts are encrypted at rest.",
    icon: (
      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M16 10V7a4 4 0 10-8 0v3m-1 0h10a1 1 0 011 1v7a1 1 0 01-1 1H7a1 1 0 01-1-1v-7a1 1 0 011-1z" />
      </svg>
    ),
  },
];

export default function ComplianceStrip({ variant = "light" }: ComplianceStripProps) {
  const dark = variant === "dark";

  return (
    <div className="grid grid-cols-2 gap-3">
      {BADGES.map((badge) => (
        <div
          key={badge.title}
          className={`rounded-[22px] px-4 py-4 shadow-sm ${
            dark
              ? "border border-white/12 bg-white/8 backdrop-blur-sm"
              : "border border-slate-200 bg-gradient-to-b from-white to-slate-50"
          }`}
        >
          <div
            className={`mb-3 flex h-9 w-9 items-center justify-center rounded-2xl ${
              dark
                ? "bg-gradient-to-br from-cyan-400/20 to-blue-400/25 text-cyan-100"
                : "bg-gradient-to-br from-teal-100 to-blue-100 text-blue-700"
            }`}
          >
            {badge.icon}
          </div>
          <p className={`text-sm font-semibold ${dark ? "text-white" : "text-slate-900"}`}>{badge.title}</p>
          <p className={`mt-1 text-xs leading-5 ${dark ? "text-sky-50/78" : "text-slate-600"}`}>{badge.description}</p>
        </div>
      ))}
    </div>
  );
}
