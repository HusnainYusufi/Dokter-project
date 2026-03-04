import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";
import { authOptions } from "@/lib/auth";
import LoginForm from "@/components/LoginForm";

export const metadata = { title: "Sign in — MedPortal" };

export default async function LoginPage() {
  const session = await getServerSession(authOptions);
  if (session) redirect("/dashboard");

  return (
    <div className="flex min-h-screen items-center justify-center bg-base px-4">
      <div className="w-full max-w-sm">
        {/* Logo mark */}
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/20">
            <svg className="h-7 w-7 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white">MedPortal</h1>
          <p className="mt-1.5 text-sm text-muted">Sign in to access the PDF parsing portal</p>
        </div>

        <div className="rounded-2xl border border-border bg-surface p-8 shadow-xl shadow-black/40">
          <LoginForm />
        </div>

        <p className="mt-6 text-center text-xs text-muted">
          Medical PDF Parsing Service &mdash; Authorized access only
        </p>
      </div>
    </div>
  );
}
