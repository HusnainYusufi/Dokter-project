import { createHash, scryptSync, timingSafeEqual } from "crypto";

import type { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";

function safeCompare(left: string, right: string) {
  const leftDigest = createHash("sha256").update(left).digest();
  const rightDigest = createHash("sha256").update(right).digest();
  return timingSafeEqual(leftDigest, rightDigest);
}

function verifyHashedPassword(password: string, encodedHash: string) {
  try {
    const [scheme, salt, hash] = encodedHash.split("$");
    if (scheme !== "scrypt" || !salt || !hash) return false;

    const saltBuffer = Buffer.from(salt, "base64");
    const hashBuffer = Buffer.from(hash, "base64");
    const candidate = scryptSync(password, saltBuffer, hashBuffer.length);

    return timingSafeEqual(candidate, hashBuffer);
  } catch {
    return false;
  }
}

export const authOptions: NextAuthOptions = {
  secret: process.env.NEXTAUTH_SECRET,
  session: { strategy: "jwt", maxAge: 60 * 60 * 8 },
  pages: { signIn: "/login" },
  useSecureCookies: process.env.NEXTAUTH_URL?.startsWith("https://") ?? false,
  providers: [
    CredentialsProvider({
      name: "Credentials",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const adminEmail = process.env.ADMIN_EMAIL;
        const adminPassword = process.env.ADMIN_PASSWORD;
        const adminPasswordHash = process.env.ADMIN_PASSWORD_HASH;

        if (!credentials?.email || !credentials.password || !adminEmail) {
          return null;
        }

        const emailMatches = safeCompare(credentials.email, adminEmail);
        const passwordMatches = adminPasswordHash
          ? verifyHashedPassword(credentials.password, adminPasswordHash)
          : adminPassword
            ? safeCompare(credentials.password, adminPassword)
            : false;

        if (emailMatches && passwordMatches) {
          return { id: "1", name: "Admin", email: adminEmail };
        }
        return null;
      },
    }),
  ],
};
