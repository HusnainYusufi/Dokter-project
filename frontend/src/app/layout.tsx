import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MedPortal — Medical PDF Parser",
  description: "Secure portal for parsing medical PDF documents using LlamaParse.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-base text-white antialiased">{children}</body>
    </html>
  );
}
