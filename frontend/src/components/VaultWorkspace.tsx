"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  buildVaultDownloadUrl,
  browseVault,
  createVaultFolder,
  deleteVaultFile,
  deleteVaultFolder,
  renameVaultFile,
  renameVaultFolder,
  uploadVaultFiles,
} from "@/lib/api";
import type { PortalPickerTab } from "@/components/PortalShellContext";
import VaultFilePreview from "@/components/VaultFilePreview";
import type { VaultBrowseResponse, VaultFileSummary } from "@/lib/types";

function dispatchVaultChanged() {
  window.dispatchEvent(new CustomEvent("portal:vault-changed"));
}

function formatFileSize(size: number) {
  if (!Number.isFinite(size) || size <= 0) return "0 KB";
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function itemTypeLabel(file: VaultFileSummary) {
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

export default function VaultWorkspace({
  initialTab = "vault",
  allowUseAction = false,
  useActionLabel = "Use file",
  demoUploadLocked = false,
  onUseFile,
}: {
  initialTab?: PortalPickerTab;
  allowUseAction?: boolean;
  useActionLabel?: string;
  demoUploadLocked?: boolean;
  onUseFile?: (file: VaultFileSummary) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [browseState, setBrowseState] = useState<VaultBrowseResponse | null>(null);
  const [folderId, setFolderId] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<VaultFileSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(initialTab === "upload");
  const [busyFileId, setBusyFileId] = useState<string | null>(null);
  const [error, setError] = useState("");

  const currentFolderId = browseState?.current_folder?.id ?? null;
  const uploadAccept = allowUseAction ? ".pdf" : ".pdf,.doc,.docx,image/*,audio/*,video/*";

  const refreshVault = useCallback(async (nextFolderId: string | null, preferredFileId?: string | null) => {
    setLoading(true);
    try {
      const payload = await browseVault(nextFolderId);
      const candidateFiles = allowUseAction ? payload.files.filter((file) => file.can_extract) : payload.files;
      setBrowseState(payload);
      setFolderId(nextFolderId);
      setSelectedFile((current) => {
        if (preferredFileId) {
          return candidateFiles.find((file) => file.id === preferredFileId) ?? null;
        }
        if (current) {
          return candidateFiles.find((file) => file.id === current.id) ?? null;
        }
        return null;
      });
      setError("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to load encrypted vault.");
    } finally {
      setLoading(false);
    }
  }, [allowUseAction]);

  useEffect(() => {
    void refreshVault(null);
  }, [refreshVault]);

  useEffect(() => {
    setUploadModalOpen(initialTab === "upload");
  }, [initialTab]);

  const breadcrumbs = useMemo(() => browseState?.breadcrumbs ?? [], [browseState]);
  const folders = useMemo(() => browseState?.folders ?? [], [browseState]);
  const files = useMemo(() => browseState?.files ?? [], [browseState]);
  const currentFolder = browseState?.current_folder ?? null;
  const breadcrumbTrail = useMemo(() => {
    if (!currentFolder) return breadcrumbs;
    if (breadcrumbs[breadcrumbs.length - 1]?.id === currentFolder.id) return breadcrumbs;
    return [...breadcrumbs, currentFolder];
  }, [breadcrumbs, currentFolder]);
  const parentFolderId = useMemo(() => {
    if (!currentFolder || breadcrumbTrail.length === 0) return null;
    if (breadcrumbTrail[breadcrumbTrail.length - 1]?.id !== currentFolder.id) return breadcrumbTrail[breadcrumbTrail.length - 1]?.id ?? null;
    return breadcrumbTrail[breadcrumbTrail.length - 2]?.id ?? null;
  }, [breadcrumbTrail, currentFolder]);
  const visibleFiles = useMemo(() => (allowUseAction ? files.filter((file) => file.can_extract) : files), [allowUseAction, files]);

  async function handleUpload(fileList: FileList | File[] | null) {
    const rawFiles = Array.from(fileList ?? []);
    const nextFiles = allowUseAction ? rawFiles.filter((file) => isPdfFile(file)) : rawFiles;
    if (nextFiles.length === 0 || demoUploadLocked) return;
    if (allowUseAction && rawFiles.length !== nextFiles.length) {
      setError("Summarizer accepts PDF files only.");
    }

    setUploading(true);
    try {
      const payload = await uploadVaultFiles(nextFiles, currentFolderId);
      dispatchVaultChanged();
      setUploadModalOpen(false);
      await refreshVault(currentFolderId, payload.files[0]?.id ?? null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Vault upload failed.");
    } finally {
      setUploading(false);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
    }
  }

  async function handleCreateFolder() {
    const name = window.prompt("Folder name");
    if (!name) return;

    try {
      await createVaultFolder(name, currentFolderId);
      dispatchVaultChanged();
      await refreshVault(currentFolderId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to create folder.");
    }
  }

  async function handleRenameFolder(folderId: string, currentName: string) {
    const name = window.prompt("Rename folder", currentName);
    if (!name) return;

    try {
      await renameVaultFolder(folderId, name);
      dispatchVaultChanged();
      await refreshVault(currentFolderId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to rename folder.");
    }
  }

  async function handleDeleteFolder(folderId: string, currentName: string) {
    if (!window.confirm(`Delete folder ${currentName}?`)) return;

    try {
      await deleteVaultFolder(folderId);
      dispatchVaultChanged();
      await refreshVault(currentFolderId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to delete folder.");
    }
  }

  async function handleRenameSelected() {
    if (!selectedFile) return;
    const name = window.prompt("Rename file", selectedFile.name);
    if (!name) return;

    setBusyFileId(selectedFile.id);
    try {
      const payload = await renameVaultFile(selectedFile.id, name);
      dispatchVaultChanged();
      await refreshVault(currentFolderId, payload.file.id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to rename file.");
    } finally {
      setBusyFileId(null);
    }
  }

  async function handleDeleteSelected() {
    if (!selectedFile) return;
    if (!window.confirm(`Delete ${selectedFile.name}?`)) return;

    setBusyFileId(selectedFile.id);
    try {
      await deleteVaultFile(selectedFile.id);
      dispatchVaultChanged();
      await refreshVault(currentFolderId);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unable to delete file.");
    } finally {
      setBusyFileId(null);
    }
  }

  function handleUseSelected() {
    if (!selectedFile || demoUploadLocked) return;
    onUseFile?.(selectedFile);
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-6 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="min-w-0">
                <p className="text-sm font-semibold text-slate-950">{browseState?.current_folder?.name ?? "All files"}</p>
                <p className="mt-1 text-xs text-slate-500">{folders.length} folders | {visibleFiles.length} names loaded</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setUploadModalOpen(true)}
            disabled={demoUploadLocked || uploading}
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-slate-950 text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Upload files"
            title="Upload files"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 16V4m0 0l-4 4m4-4l4 4M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1" />
            </svg>
          </button>
          <button
            type="button"
            onClick={() => void handleCreateFolder()}
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 transition hover:bg-slate-50"
            aria-label="New folder"
            title="New folder"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14m-7-7h14" />
            </svg>
          </button>
          <button
            type="button"
            onClick={() => void refreshVault(currentFolderId)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 transition hover:bg-slate-50"
            aria-label="Refresh vault"
            title="Refresh vault"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h5M20 20v-5h-5M5.8 13A7 7 0 0117 6.3L20 9M4 15l3 2.7A7 7 0 0018.2 11" />
            </svg>
          </button>
        </div>
      </div>

      

      {error && (
        <div className="border-b border-rose-200 bg-rose-50 px-6 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      <div className="grid min-h-0 flex-1 gap-0 xl:grid-cols-[minmax(360px,0.95fr)_minmax(0,1.45fr)]">
        <div className="min-h-0 border-r border-slate-200 bg-white">
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50 px-5 py-3">
              {currentFolder && (
                <button
                  type="button"
                  onClick={() => void refreshVault(parentFolderId)}
                  className="inline-flex h-8 items-center justify-center rounded-lg border border-slate-200 bg-white px-3 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                >
                  Back
                </button>
              )}

              <div className="flex min-w-0 flex-wrap items-center gap-1 text-xs font-semibold text-slate-600">
                <button
                  type="button"
                  onClick={() => void refreshVault(null)}
                  className={`${folderId === null ? "text-slate-950" : "text-slate-600 transition hover:text-slate-950"}`}
                >
                  All files
                </button>
                {breadcrumbTrail.map((crumb, index) => {
                  const isCurrent = index === breadcrumbTrail.length - 1;

                  return (
                    <div key={`${crumb.id}-${index}`} className="flex items-center gap-1">
                      <span className="text-slate-400">/</span>
                      {isCurrent ? (
                        <span className="text-slate-950">{crumb.name}</span>
                      ) : (
                        <button type="button" onClick={() => void refreshVault(crumb.id)} className="transition hover:text-slate-950">
                          {crumb.name}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {loading ? (
                <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-8 text-center text-sm text-slate-500">
                  Loading file names...
                </div>
              ) : (
                <div className="space-y-2">
                  {folders.map((folder) => (
                    <div
                      key={folder.id}
                      className="flex items-center gap-3 rounded-xl border border-transparent bg-white px-4 py-3 transition hover:border-slate-200 hover:bg-slate-50"
                    >
                      <button type="button" onClick={() => void refreshVault(folder.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M3 7h5l2 2h11v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                          </svg>
                        </div>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-slate-950">{folder.name}</p>
                          <p className="mt-0.5 text-xs text-slate-500">Folder</p>
                        </div>
                      </button>

                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => void handleRenameFolder(folder.id, folder.name)}
                          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 transition hover:bg-slate-50"
                          aria-label="Rename folder"
                          title="Rename folder"
                        >
                          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487a2.1 2.1 0 113.03 2.91L9 18l-4 1 1-4 10.862-10.513z" />
                          </svg>
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleDeleteFolder(folder.id, folder.name)}
                          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-rose-200 bg-white text-rose-600 transition hover:bg-rose-50"
                          aria-label="Delete folder"
                          title="Delete folder"
                        >
                          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 7h12M9 7V5h6v2m-7 4v6m4-6v6m4-6v6M8 7l1 12h6l1-12" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  ))}

                  {visibleFiles.map((file) => {
                    const active = selectedFile?.id === file.id;

                    return (
                      <button
                        key={file.id}
                        type="button"
                        onClick={() => setSelectedFile(file)}
                        className={`block w-full rounded-xl border px-4 py-3 text-left transition ${
                          active
                            ? "border-blue-200 bg-blue-50 shadow-sm"
                            : "border-transparent bg-white hover:border-slate-200 hover:bg-slate-50"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex min-w-0 items-center gap-3">
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-500">
                              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M7 4h7l5 5v11a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2z" />
                              </svg>
                            </div>
                            <div className="min-w-0">
                              <p className="truncate text-sm font-semibold text-slate-950">{file.name}</p>
                              <p className="mt-0.5 text-xs text-slate-500">{itemTypeLabel(file)}</p>
                            </div>
                          </div>
                          <span className="rounded-full bg-white px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-600 shadow-sm">
                            {file.preview_kind}
                          </span>
                        </div>
                      </button>
                    );
                  })}

                  {!loading && folders.length === 0 && visibleFiles.length === 0 && (
                    <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
                      {allowUseAction ? "No PDF files in this folder." : "This folder is empty."}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="min-h-0 overflow-y-auto bg-slate-50/70 p-5">
          <div className="space-y-5">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <p className="text-lg font-semibold text-slate-950">{selectedFile?.name ?? "No file selected"}</p>
                <p className="mt-1 text-sm text-slate-500">
                  {selectedFile
                    ? `${itemTypeLabel(selectedFile)} | ${formatFileSize(selectedFile.size_bytes)} | Updated ${formatDate(selectedFile.updated_at)}`
                    : allowUseAction
                      ? "Choose a PDF name to use in the summarizer. File bytes load only when extraction starts."
                      : "Choose vault file to preview, rename, delete, or use in the summarizer."}
                </p>
              </div>

              {selectedFile && (
                <div className="flex flex-wrap gap-2">
                  {allowUseAction && (
                    <button
                      type="button"
                      onClick={handleUseSelected}
                      disabled={demoUploadLocked}
                      className="rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {useActionLabel}
                    </button>
                  )}
                  {!allowUseAction && (
                    <>
                      <button
                        type="button"
                        onClick={() => void handleRenameSelected()}
                        disabled={busyFileId === selectedFile.id}
                        className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                        aria-label="Rename file"
                        title="Rename file"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487a2.1 2.1 0 113.03 2.91L9 18l-4 1 1-4 10.862-10.513z" />
                        </svg>
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDeleteSelected()}
                        disabled={busyFileId === selectedFile.id}
                        className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-rose-200 bg-white text-rose-600 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
                        aria-label="Delete file"
                        title="Delete file"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 7h12M9 7V5h6v2m-7 4v6m4-6v6m4-6v6M8 7l1 12h6l1-12" />
                        </svg>
                      </button>
                    </>
                  )}
                  <a
                    href={buildVaultDownloadUrl(selectedFile.id)}
                    className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 transition hover:bg-slate-50"
                    aria-label="Download file"
                    title="Download file"
                  >
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v12m0 0l4-4m-4 4l-4-4M4 17v1a3 3 0 003 3h10a3 3 0 003-3v-1" />
                    </svg>
                  </a>
                </div>
              )}
            </div>

            <VaultFilePreview file={selectedFile} />

            {selectedFile && (
              <div className="rounded-[24px] border border-slate-200 bg-slate-50 p-5">
                <p className="text-sm font-semibold text-slate-950">File details</p>
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">Type</p>
                    <p className="mt-1 text-sm text-slate-700">{itemTypeLabel(selectedFile)}</p>
                  </div>
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">Size</p>
                    <p className="mt-1 text-sm text-slate-700">{formatFileSize(selectedFile.size_bytes)}</p>
                  </div>
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">Updated</p>
                    <p className="mt-1 text-sm text-slate-700">{formatDate(selectedFile.updated_at)}</p>
                  </div>
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">Summarizer</p>
                    <p className="mt-1 text-sm text-slate-700">{selectedFile.can_extract ? "Ready for PDF summarization" : "Stored safely in encrypted vault"}</p>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <input
        ref={inputRef}
        type="file"
        multiple
        accept={uploadAccept}
        className="hidden"
        onChange={(event) => void handleUpload(event.target.files)}
      />

      {uploadModalOpen && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-950/45 p-4">
          <div className="w-full max-w-xl rounded-[24px] border border-slate-200 bg-white p-6 shadow-2xl">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-lg font-semibold text-slate-950">Upload files</p>
                <p className="mt-1 text-sm text-slate-500">{allowUseAction ? "PDF only." : "PDF, audio, video, DOC, DOCX, and images."}</p>
              </div>
              <button
                type="button"
                onClick={() => setUploadModalOpen(false)}
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-700 transition hover:bg-slate-50"
                aria-label="Close upload modal"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 6l12 12M18 6L6 18" />
                </svg>
              </button>
            </div>

            <div
              className="mt-5 rounded-[20px] border-2 border-dashed border-slate-300 bg-slate-50 px-6 py-14 text-center"
              onDragOver={(event) => {
                event.preventDefault();
              }}
              onDrop={(event) => {
                event.preventDefault();
                void handleUpload(event.dataTransfer.files);
              }}
            >
              <p className="text-base font-semibold text-slate-950">Drop file here</p>
              <p className="mt-2 text-sm text-slate-500">{allowUseAction ? "Only PDF files appear in summarizer." : "Or choose file from device."}</p>
              <button
                type="button"
                disabled={uploading || demoUploadLocked}
                onClick={() => inputRef.current?.click()}
                className="mt-5 inline-flex items-center justify-center rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {uploading ? "Saving..." : "Choose file"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
