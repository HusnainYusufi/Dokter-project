import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Medical Intelligence Portal",
  description: "Secure portal for encrypted medical document extraction, page-wise review, and Word export.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-base text-white antialiased">{children}</body>
    </html>
  );
}
