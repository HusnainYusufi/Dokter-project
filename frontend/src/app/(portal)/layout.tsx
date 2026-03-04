import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";
import { authOptions } from "@/lib/auth";
import Providers from "@/components/Providers";
import Sidebar from "@/components/Sidebar";

export default async function PortalLayout({ children }: { children: React.ReactNode }) {
  const session = await getServerSession(authOptions);
  if (!session) redirect("/login");

  return (
    <Providers>
      <div className="flex min-h-screen">
        <Sidebar />
        <main className="ml-60 flex-1 p-8">{children}</main>
      </div>
    </Providers>
  );
}
