"use client";

import { motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";

import { usePortalShell } from "@/components/PortalShellContext";

interface Props {
  onClose: () => void;
}

function dispatchSelectedLocalFile(file: File) {
  window.dispatchEvent(new CustomEvent<File>("portal:local-file-selected", { detail: file }));
}

function formatLocalFileSize(size: number) {
  if (!Number.isFinite(size) || size <= 0) return "0 KB";
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentSourceModal({ onClose }: Props) {
  const { demoMode, demoExtractBusy } = usePortalShell();
  const demoUploadLocked = demoMode && demoExtractBusy;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  const localPreviewUrl = useMemo(() => {
    if (!uploadFile) return null;
    return URL.createObjectURL(uploadFile);
  }, [uploadFile]);

  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [onClose]);

  useEffect(() => {
    return () => {
      if (localPreviewUrl) {
        URL.revokeObjectURL(localPreviewUrl);
      }
    };
  }, [localPreviewUrl]);

  function handleUseLocalFile() {
    if (!uploadFile || demoUploadLocked) return;
    dispatchSelectedLocalFile(uploadFile);
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
        className="relative z-10 flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-[24px] border border-slate-200 bg-white shadow-2xl shadow-slate-950/25"
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 18, scale: 0.98 }}
        transition={{ duration: 0.2, ease: "easeOut" }}
      >
        <div className="border-b border-slate-200 px-6 py-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">Choose source</p>
              <h2 className="mt-2 text-2xl font-semibold text-slate-950">Add document</h2>
              <p className="mt-2 text-sm text-slate-500">Pick local PDF and continue from dashboard upload button.</p>
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

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {demoUploadLocked && (
            <div className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-950">
              Demo mode: wait for the current extraction to finish before adding another file.
            </div>
          )}

          <div className="min-h-0 overflow-y-auto px-6 py-6">
            <motion.div
              key="upload-tab"
              className="rounded-[20px] border border-slate-200 bg-slate-50 p-5"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.18 }}
            >
              <input
                ref={inputRef}
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
              />

              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-lg font-semibold text-slate-950">Local upload</p>
                  <p className="mt-1 text-sm text-slate-500">Pick PDF here. Then use dashboard `Upload PDF` button.</p>
                </div>

                <button
                  type="button"
                  disabled={demoUploadLocked}
                  onClick={() => inputRef.current?.click()}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                  </svg>
                  Choose PDF
                </button>
              </div>

              <div className="mt-5 grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
                <div className="rounded-xl border border-slate-200 bg-white px-4 py-4">
                  <p className="truncate text-sm font-semibold text-slate-950">{uploadFile?.name ?? "No PDF selected"}</p>
                  <p className="mt-1 text-sm text-slate-500">{uploadFile ? "Selected file ready." : "Choose file to continue."}</p>
                  {uploadFile && <p className="mt-3 text-xs text-slate-400">{formatLocalFileSize(uploadFile.size)}</p>}
                </div>

                <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
                  {!localPreviewUrl ? (
                    <div className="flex h-[360px] items-center justify-center px-6 text-center text-sm text-slate-500">
                      PDF preview appears here after file selection.
                    </div>
                  ) : (
                    <iframe
                      title="Selected PDF preview"
                      src={`${localPreviewUrl}#toolbar=0&navpanes=0&scrollbar=1`}
                      className="h-[360px] w-full bg-white"
                    />
                  )}
                </div>
              </div>

              <div className="mt-5 flex justify-end">
                <motion.button
                  type="button"
                  onClick={handleUseLocalFile}
                  disabled={!uploadFile || demoUploadLocked}
                  className="inline-flex items-center justify-center rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                  whileTap={{ scale: 0.98 }}
                >
                  Use file
                </motion.button>
              </div>
            </motion.div>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}
