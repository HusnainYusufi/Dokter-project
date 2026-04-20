"use client";

import { motion } from "framer-motion";
import { useEffect } from "react";

import { usePortalShell, type PortalPickerTab } from "@/components/PortalShellContext";
import VaultWorkspace from "@/components/VaultWorkspace";
import type { VaultFileSummary } from "@/lib/types";

interface Props {
  initialTab?: PortalPickerTab;
  onClose: () => void;
}

function dispatchSelectedVaultFile(file: VaultFileSummary) {
  window.dispatchEvent(new CustomEvent<VaultFileSummary>("portal:vault-file-selected", { detail: file }));
}

export default function DocumentSourceModal({ initialTab = "upload", onClose }: Props) {
  const { demoMode, demoExtractBusy } = usePortalShell();
  const demoUploadLocked = demoMode && demoExtractBusy;

  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [onClose]);

  function handleUseSelected(file: VaultFileSummary) {
    if (demoUploadLocked) return;
    dispatchSelectedVaultFile(file);
    onClose();
  }

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-center justify-center px-4 py-6"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
    >
      <motion.div
        className="absolute inset-0 bg-slate-950/50"
        onClick={onClose}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
      />

      <motion.div
        className="relative z-10 flex max-h-[94vh] w-full max-w-7xl flex-col overflow-hidden rounded-[24px] border border-slate-200 bg-white shadow-2xl shadow-slate-950/25"
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 18, scale: 0.98 }}
        transition={{ duration: 0.2, ease: "easeOut" }}
      >
        <div className="border-b border-slate-200 px-6 py-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">Select source file</p>
              <h2 className="mt-2 text-2xl font-semibold text-slate-950">Choose PDF or upload a new one</h2>
              <p className="mt-2 text-sm text-slate-500">Only PDF files appear here for summarizer use.</p>
            </div>

            <button
              type="button"
              onClick={onClose}
              className="rounded-xl border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
            >
              Close
            </button>
          </div>
        </div>

        <VaultWorkspace
          initialTab={initialTab}
          allowUseAction
          useActionLabel="Use in summarizer"
          demoUploadLocked={demoUploadLocked}
          onUseFile={handleUseSelected}
        />
      </motion.div>
    </motion.div>
  );
}
