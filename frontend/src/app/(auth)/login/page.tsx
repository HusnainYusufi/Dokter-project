import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";

import { authOptions } from "@/lib/auth";
import ComplianceStrip from "@/components/ComplianceStrip";
import LoginForm from "@/components/LoginForm";

export const metadata = { title: "Sign in - Medical Intelligence" };

export default async function LoginPage() {
  const session = await getServerSession(authOptions);
  if (session) redirect("/dashboard");

  return (
    <div className="relative min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top_left,_rgba(45,212,191,0.35),_transparent_28%),radial-gradient(circle_at_right,_rgba(59,130,246,0.30),_transparent_32%),linear-gradient(135deg,_#07243f_0%,_#0f4d6e_42%,_#12335c_100%)] px-4 py-10">
      <div className="pointer-events-none absolute inset-0 opacity-40">
        <div className="absolute left-[8%] top-[15%] h-48 w-48 rounded-full border border-white/15" />
        <div className="absolute right-[10%] top-[22%] h-72 w-72 rounded-full border border-white/10" />
        <div className="absolute bottom-[16%] left-[14%] h-60 w-60 rounded-full border border-white/10" />
      </div>

      <div className="relative mx-auto flex min-h-[calc(100vh-5rem)] max-w-6xl items-center justify-center">
        <div className="w-full max-w-5xl rounded-[36px] border border-white/20 bg-white/14 p-4 shadow-2xl shadow-slate-950/40 backdrop-blur-xl md:p-6">
          <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
            <div className="rounded-[30px] bg-white/10 px-6 py-8 text-white">
              <p className="text-sm font-semibold uppercase tracking-[0.32em] text-sky-100">Login Screen</p>
              <h1 className="mt-4 max-w-md text-4xl font-semibold leading-tight">
                Secure medical document extraction for sensitive medico-legal workflows.
              </h1>
              <p className="mt-4 max-w-lg text-sm leading-7 text-sky-50/85">
                Upload, index, review, and export patient-specific records with page-wise capture, patient-boundary separation,
                and encrypted storage designed for high-sensitivity document handling.
              </p>

              <div className="mt-8 grid gap-4 sm:grid-cols-2">
                <div className="rounded-3xl border border-white/15 bg-white/10 px-5 py-5">
                  <p className="text-sm font-semibold">Patient-safe review</p>
                  <p className="mt-2 text-sm leading-6 text-sky-50/80">
                    Boundary-aware extraction separates mixed-patient files into reviewable document groups.
                  </p>
                </div>
                <div className="rounded-3xl border border-white/15 bg-white/10 px-5 py-5">
                  <p className="text-sm font-semibold">Encrypted artifacts</p>
                  <p className="mt-2 text-sm leading-6 text-sky-50/80">
                    Uploaded PDFs, extracted text, and downloadable Word artifacts are encrypted before persistence.
                  </p>
                </div>
              </div>
            </div>

            <div className="rounded-[30px] bg-white px-6 py-8 shadow-xl shadow-slate-950/15 md:px-8">
              <div className="mb-8 text-center">
                <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-[28px] bg-gradient-to-br from-teal-400 to-blue-700 shadow-lg shadow-blue-900/20">
                  <svg className="h-10 w-10 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m6-6H6" />
                    <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 16.5L19 19m-7-9a4.5 4.5 0 100 9 4.5 4.5 0 000-9z" />
                  </svg>
                </div>
                <h2 className="mt-5 text-3xl font-semibold text-slate-950">Medical Intelligence</h2>
                <p className="mt-2 text-sm text-slate-500">Medical | Legal | Intelligence</p>
              </div>

              <LoginForm />

              <div className="mt-8 border-t border-slate-200 pt-6">
                <ComplianceStrip />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
