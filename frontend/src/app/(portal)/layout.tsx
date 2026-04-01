import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";

import { authOptions } from "@/lib/auth";
import PortalHeader from "@/components/PortalHeader";
import Providers from "@/components/Providers";

export default async function PortalLayout({ children }: { children: React.ReactNode }) {
  const session = await getServerSession(authOptions);
  if (!session) redirect("/login");

  return (
    <Providers>
      <div className="min-h-screen bg-slate-100">
        <PortalHeader />
        <main>{children}</main>
        <div className="bg-emerald-500 py-3 text-center text-sm font-semibold tracking-[0.28em] text-white">
          END-TO-END ENCRYPTED
        </div>
      </div>
    </Providers>
  );
}
