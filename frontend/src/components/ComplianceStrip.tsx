"use client";

const BADGES = [
  { title: "ISO 27001", description: "Enterprise-grade security controls and retention discipline." },
  { title: "SOC 2", description: "Operational integrity and privacy controls for secure handling." },
  { title: "HIPAA", description: "Protected health information processed with strict safeguards." },
  { title: "Encrypted", description: "Uploaded documents and generated artifacts are encrypted at rest." },
];

export default function ComplianceStrip() {
  return (
    <div className="grid gap-3 md:grid-cols-4">
      {BADGES.map((badge) => (
        <div
          key={badge.title}
          className="rounded-2xl border border-sky-100/70 bg-white/85 px-4 py-4 shadow-sm backdrop-blur"
        >
          <p className="text-sm font-semibold text-slate-900">{badge.title}</p>
          <p className="mt-1 text-xs leading-5 text-slate-600">{badge.description}</p>
        </div>
      ))}
    </div>
  );
}
