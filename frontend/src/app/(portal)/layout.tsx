import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";

import { authOptions } from "@/lib/auth";
import PortalShell from "@/components/PortalShell";
import Providers from "@/components/Providers";

export default async function PortalLayout({ children }: { children: React.ReactNode }) {
  const session = await getServerSession(authOptions);
  if (!session) redirect("/login");

  return (
    <Providers>
      <PortalShell>{children}</PortalShell>
    </Providers>
  );
}
