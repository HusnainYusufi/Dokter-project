"use client";

import { motion } from "framer-motion";

import { usePortalShell } from "@/components/PortalShellContext";
import VaultWorkspace from "@/components/VaultWorkspace";

export default function VaultPage() {
  const { demoMode, demoExtractBusy } = usePortalShell();
  const demoUploadLocked = demoMode && demoExtractBusy;

  return (
    <div className="space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <motion.section
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.28 }}
        className="flex h-[calc(100vh-9rem)] min-h-[42rem] flex-col overflow-hidden rounded-[32px] border border-slate-200 bg-white shadow-sm"
      >
        <div className="border-b border-slate-200 px-6 py-6 md:px-8">
          <div className="max-w-3xl">
            <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">Encrypted vault</p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">All stored files and folders</h2>
            <p className="mt-3 text-sm leading-7 text-slate-500">
              Review every stored dictation, transcript, PDF, image, and final document in one browser-style workspace.
            </p>
          </div>
        </div>

        <VaultWorkspace initialTab="vault" demoUploadLocked={demoUploadLocked} />
      </motion.section>
    </div>
  );
}
