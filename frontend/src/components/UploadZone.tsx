"use client";

import { useEffect, useRef, useState } from "react";

import { createJobFromVaultFile, uploadVaultFiles } from "@/lib/api";
import type { ExtractionJobSummary, VaultFileSummary } from "@/lib/types";
import { usePortalShell } from "@/components/PortalShellContext";

function dispatchVaultChanged() {
  window.dispatchEvent(new CustomEvent("portal:vault-changed"));
}

function formatFileSize(size: number) {
  if (!Number.isFinite(size) || size <= 0) return "0 KB";
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function typeLabel(file: VaultFileSummary | null) {
  if (!file) return "No file selected";
  if (file.preview_kind === "pdf") return "PDF";
  if (file.preview_kind === "image") return "Image";
  if (file.preview_kind === "audio") return "Audio";
  if (file.preview_kind === "video") return "Video";
  if (file.preview_kind === "doc") return file.extension === ".doc" ? "DOC" : "DOCX";
  return file.content_type;
}

function isPdfFile(file: { name: string; type?: string }) {
  return (file.type || "").toLowerCase() === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

export default function UploadZone() {
  const { openSourcePicker, demoMode, demoExtractBusy } = usePortalShell();
  const demoLocked = demoMode && demoExtractBusy;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [selectedFile, setSelectedFile] = useState<VaultFileSummary | null>(null);
  const [uploading, setUploading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [statusText, setStatusText] = useState("Drop PDF here or browse encrypted vault.");

  useEffect(() => {
    function handleVaultFileSelected(event: Event) {
      const nextFile = (event as CustomEvent<VaultFileSummary>).detail;
      setSelectedFile(nextFile);
      setStatusText(`${nextFile.name} ready in encrypted vault.`);
    }

    window.addEventListener("portal:vault-file-selected", handleVaultFileSelected as EventListener);
    return () => window.removeEventListener("portal:vault-file-selected", handleVaultFileSelected as EventListener);
  }, []);

  async function handleUpload(fileList: FileList | File[] | null) {
    const allFiles = Array.from(fileList ?? []);
    const files = allFiles.filter((file) => isPdfFile(file));
    if (allFiles.length !== files.length) {
      setStatusText("Summarizer accepts PDF files only.");
    }
    if (files.length === 0 || demoLocked) return;

    setUploading(true);
    try {
      const payload = await uploadVaultFiles(files);
      const firstFile = payload.files[0] ?? null;
      setSelectedFile(firstFile);
      setStatusText(
        payload.files.length === 1
          ? `${payload.files[0]?.name ?? "File"} stored in encrypted vault.`
          : `${payload.files.length} files stored in encrypted vault.`,
      );
      dispatchVaultChanged();
    } catch (err: unknown) {
      setStatusText(err instanceof Error ? err.message : "Vault upload failed.");
    } finally {
      setUploading(false);
      setDragActive(false);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
    }
  }

  async function handleStartExtraction() {
    if (!selectedFile || !selectedFile.can_extract || demoLocked) return;

    setExtracting(true);
    try {
      const payload = await createJobFromVaultFile(selectedFile.id);
      window.dispatchEvent(new CustomEvent<ExtractionJobSummary>("portal:job-created", { detail: payload.job }));
      dispatchVaultChanged();
      setStatusText(`${selectedFile.name} queued for extraction.`);
    } catch (err: unknown) {
      setStatusText(err instanceof Error ? err.message : "Unable to start extraction.");
    } finally {
      setExtracting(false);
    }
  }

  return (
    <div className="rounded-[32px] border border-slate-200 bg-white p-6 shadow-sm md:p-8">
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf"
        className="hidden"
        onChange={(event) => void handleUpload(event.target.files)}
      />

      <div className="flex flex-col gap-4 lg:flex-row lg:items-stretch">
        <div className="flex w-full items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-4 lg:max-w-[220px]">
          <button
            type="button"
            disabled={demoLocked}
            onClick={() => openSourcePicker("vault")}
            className="inline-flex w-full items-center justify-center rounded-xl bg-slate-950 px-4 py-3 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Select file
          </button>
        </div>

        <div
          className={`min-w-0 flex-1 rounded-2xl border px-4 py-4 transition ${
            dragActive ? "border-blue-300 bg-blue-50" : "border-slate-200 bg-white"
          }`}
          onDragEnter={(event) => {
            event.preventDefault();
            if (!demoLocked) setDragActive(true);
          }}
          onDragLeave={(event) => {
            event.preventDefault();
            setDragActive(false);
          }}
          onDragOver={(event) => {
            event.preventDefault();
          }}
          onDrop={(event) => {
            event.preventDefault();
            void handleUpload(event.dataTransfer.files);
          }}
        >
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">Current source</p>
          <p className="mt-2 truncate text-sm font-semibold text-slate-950">{selectedFile?.name ?? "No file selected"}</p>
          <p className="mt-1 text-xs text-slate-500">{statusText}</p>
          {selectedFile && (
            <p className="mt-3 text-xs text-slate-400">
              {typeLabel(selectedFile)} | {formatFileSize(selectedFile.size_bytes)}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={() => void handleStartExtraction()}
            disabled={!selectedFile?.can_extract || extracting || demoLocked}
            className="inline-flex items-center justify-center rounded-2xl bg-blue-600 px-5 py-3.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {extracting ? "Starting..." : "Start"}
          </button>
        </div>
      </div>
    </div>
  );
}
