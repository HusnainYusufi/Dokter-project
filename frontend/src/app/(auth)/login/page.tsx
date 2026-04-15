import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";

import { authOptions } from "@/lib/auth";
import LoginScreen from "@/components/LoginScreen";

export const metadata = { title: "Sign in - Medical Intelligence" };

export default async function LoginPage() {
  const session = await getServerSession(authOptions);
  if (session) redirect("/dashboard");

  return <LoginScreen />;
}
