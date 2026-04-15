"use client";

import { motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";

import { usePortalShell, type PortalPickerTab } from "@/components/PortalShellContext";
import type { ExtractionJobSummary } from "@/lib/types";
import {
  describeDropboxFailure,
  formatDropboxDate,
  formatDropboxSize,
  importDropboxFile,
  listDropboxFiles,
  type DropboxAccountSummary,
  type DropboxFileItem,
} from "@/services/dropbox";

interface Props {
  pickerTab: PortalPickerTab;
  onClose: () => void;
  onPickerTabChange: (tab: PortalPickerTab) => void;
}

function dispatchCreatedJob(job: ExtractionJobSummary) {
  window.dispatchEvent(new CustomEvent<ExtractionJobSummary>("portal:job-created", { detail: job }));
}

function dispatchSelectedLocalFile(file: File) {
  window.dispatchEvent(new CustomEvent<File>("portal:local-file-selected", { detail: file }));
}

function formatLocalFileSize(size: number) {
  if (!Number.isFinite(size) || size <= 0) return "0 KB";
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentSourceModal({ pickerTab, onClose, onPickerTabChange }: Props) {
  const { demoMode, demoExtractBusy } = usePortalShell();
  const demoUploadLocked = demoMode && demoExtractBusy;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [dropboxAccount, setDropboxAccount] = useState<DropboxAccountSummary | null>(null);
  const [dropboxFiles, setDropboxFiles] = useState<DropboxFileItem[]>([]);
  const [dropboxLoading, setDropboxLoading] = useState(false);
  const [dropboxError, setDropboxError] = useState("");
  const [importingPath, setImportingPath] = useState<string | null>(null);

  const localPreviewUrl = useMemo(() => {
    if (!uploadFile) return null;
    return URL.createObjectURL(uploadFile);
  }, [uploadFile]);

  useEffect(() => {
    if (pickerTab !== "dropbox") return;
    void refreshDropboxFiles();
  }, [pickerTab]);

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

  async function refreshDropboxFiles() {
    setDropboxLoading(true);
    setDropboxError("");

    try {
      const payload = await listDropboxFiles();
      setDropboxAccount(payload.account);
      setDropboxFiles(payload.files);
    } catch (error) {
      setDropboxFiles([]);
      setDropboxAccount(null);
      setDropboxError(describeDropboxFailure(error, "Unable to load Dropbox files."));
    } finally {
      setDropboxLoading(false);
    }
  }

  function handleUseLocalFile() {
    if (!uploadFile || demoUploadLocked) return;
    dispatchSelectedLocalFile(uploadFile);
    onClose();
  }

  async function handleDropboxImport(file: DropboxFileItem) {
    if (demoUploadLocked) return;
    setImportingPath(file.pathLower);
    setDropboxError("");

    try {
      const payload = await importDropboxFile(file);
      dispatchCreatedJob(payload.job);
      onClose();
    } catch (error) {
      setDropboxError(describeDropboxFailure(error, "Unable to import Dropbox PDF."));
    } finally {
      setImportingPath(null);
    }
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
              <p className="mt-2 text-sm text-slate-500">Pick local PDF or import from Dropbox.</p>
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

          <div className="border-b border-slate-200 px-6 py-4">
            <div className="inline-flex rounded-xl bg-slate-100 p-1">
              {(["upload", "dropbox"] as PortalPickerTab[]).map((tab) => (
                <motion.button
                  key={tab}
                  type="button"
                  onClick={() => onPickerTabChange(tab)}
                  className={`rounded-lg px-4 py-2 text-sm font-semibold transition ${
                    pickerTab === tab ? "bg-white text-slate-950 shadow-sm" : "text-slate-500 hover:text-slate-900"
                  }`}
                  whileTap={{ scale: 0.98 }}
                >
                  {tab === "upload" ? "Upload" : "Dropbox"}
                </motion.button>
              ))}
            </div>
          </div>

          <div className="min-h-0 overflow-y-auto px-6 py-6">
            {pickerTab === "upload" ? (
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
            ) : (
              <motion.div
                key="dropbox-tab"
                className="space-y-4"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.18 }}
              >
                <motion.div
                  className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                >
                  <div>
                    <p className="text-lg font-semibold text-slate-950">
                      {dropboxAccount ? `Connected as ${dropboxAccount.name}` : "Dropbox files"}
                    </p>
                    <p className="mt-1 text-sm text-slate-500">Uses Dropbox credentials from frontend env configuration.</p>
                  </div>

                  <motion.button
                    type="button"
                    onClick={() => void refreshDropboxFiles()}
                    disabled={dropboxLoading}
                    className="rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                    whileTap={{ scale: 0.98 }}
                  >
                    {dropboxLoading ? "Refreshing..." : "Refresh files"}
                  </motion.button>
                </motion.div>


                {dropboxError && (
                  <motion.div
                    className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                  >
                    {dropboxError}
                  </motion.div>
                )}

                <motion.div
                  className="overflow-hidden rounded-[20px] border border-slate-200 bg-white"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                >
                  <div className="grid grid-cols-[minmax(0,1.2fr)_auto_auto] gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">
                    <p>File</p>
                    <p>Modified</p>
                    <p>Action</p>
                  </div>

                  {dropboxLoading && <div className="px-4 py-8 text-sm text-slate-500">Loading Dropbox PDFs...</div>}

                  {!dropboxLoading && !dropboxError && dropboxFiles.length === 0 && (
                    <div className="px-4 py-10 text-center text-sm text-slate-500">No PDF files found in Dropbox account.</div>
                  )}

                  <div className="max-h-[52vh] divide-y divide-slate-100 overflow-y-auto">
                    {dropboxFiles.map((file) => (
                      <motion.div
                        key={file.id}
                        className="grid grid-cols-1 gap-4 px-4 py-4 transition hover:bg-slate-50 md:grid-cols-[minmax(0,1.2fr)_auto_auto] md:items-center"
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.18 }}
                      >
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-slate-950">{file.name}</p>
                          <p className="mt-1 truncate text-xs text-slate-500">
                            {file.pathDisplay} | {formatDropboxSize(file.size)}
                          </p>
                        </div>

                        <p className="text-xs text-slate-500">{formatDropboxDate(file.modifiedAt)}</p>

                        <motion.button
                          type="button"
                          onClick={() => void handleDropboxImport(file)}
                          disabled={demoUploadLocked || importingPath === file.pathLower}
                          className="rounded-xl bg-slate-950 px-4 py-2 text-xs font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                          whileTap={{ scale: 0.98 }}
                        >
                          {importingPath === file.pathLower ? "Importing..." : "Import"}
                        </motion.button>
                      </motion.div>
                    ))}
                  </div>
                </motion.div>
              </motion.div>
            )}
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}
