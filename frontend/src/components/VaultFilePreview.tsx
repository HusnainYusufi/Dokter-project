"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import mammoth from "mammoth";

import { buildVaultContentUrl, buildVaultDownloadUrl, vaultDownloadFilename } from "@/lib/api";
import type { VaultFileSummary } from "@/lib/types";

const PdfDocumentViewer = dynamic(() => import("@/components/PdfDocumentViewer"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[420px] items-center justify-center rounded-[24px] border border-slate-200 bg-slate-100 px-6 text-sm text-slate-500">
      Loading preview...
    </div>
  ),
});

export default function VaultFilePreview({
  file,
  heightClassName = "h-[420px]",
}: {
  file: VaultFileSummary | null;
  heightClassName?: string;
}) {
  const contentUrl = file ? buildVaultContentUrl(file.id) : "";
  const downloadUrl = file ? buildVaultDownloadUrl(file.id) : "";
  const isDocx =
    file?.preview_kind === "doc" &&
    (file.extension === ".docx" || file.content_type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document");
  const [docHtml, setDocHtml] = useState("");
  const [docError, setDocError] = useState("");
  const [docLoading, setDocLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadDocPreview() {
      if (!isDocx) {
        setDocHtml("");
        setDocError("");
        setDocLoading(false);
        return;
      }

      setDocLoading(true);
      setDocError("");

      try {
        const response = await fetch(contentUrl, { cache: "no-store" });
        if (!response.ok) {
          throw new Error("Unable to load DOCX preview.");
        }

        const arrayBuffer = await response.arrayBuffer();
        const result = await mammoth.convertToHtml({ arrayBuffer });
        if (!cancelled) {
          setDocHtml(result.value);
        }
      } catch (error) {
        if (!cancelled) {
          setDocHtml("");
          setDocError(error instanceof Error ? error.message : "Unable to preview DOCX file.");
        }
      } finally {
        if (!cancelled) {
          setDocLoading(false);
        }
      }
    }

    void loadDocPreview();

    return () => {
      cancelled = true;
    };
  }, [contentUrl, isDocx]);

  if (!file) {
    return (
      <div className={`flex ${heightClassName} items-center justify-center rounded-[24px] border border-dashed border-slate-300 bg-slate-50 px-6 text-center text-sm text-slate-500`}>
        Select file to preview.
      </div>
    );
  }

  if (file.preview_kind === "pdf") {
    return <PdfDocumentViewer sourceUrl={contentUrl} filename={file.name} heightClassName={heightClassName} />;
  }

  if (file.preview_kind === "image") {
    return (
      <div className={`overflow-hidden rounded-[24px] border border-slate-200 bg-slate-50 ${heightClassName}`}>
        <img src={contentUrl} alt={file.name} className="h-full w-full object-contain bg-white" />
      </div>
    );
  }

  if (file.preview_kind === "audio") {
    return (
      <div className={`flex ${heightClassName} items-center justify-center rounded-[24px] border border-slate-200 bg-slate-50 px-6`}>
        <audio controls className="w-full max-w-lg" src={contentUrl}>
          Your browser cannot play this audio file.
        </audio>
      </div>
    );
  }

  if (file.preview_kind === "video") {
    return (
      <div className={`flex ${heightClassName} items-center justify-center rounded-[24px] border border-slate-200 bg-slate-950 px-6`}>
        <video controls className="h-full w-full rounded-[20px] object-contain" src={contentUrl}>
          Your browser cannot play this video file.
        </video>
      </div>
    );
  }

  if (file.preview_kind === "doc") {
    if (isDocx) {
      if (docLoading) {
        return (
          <div className={`flex ${heightClassName} items-center justify-center rounded-[24px] border border-slate-200 bg-slate-50 px-6 text-sm text-slate-500`}>
            Loading DOCX preview...
          </div>
        );
      }

      if (!docError && docHtml) {
        return (
          <div className={`overflow-y-auto rounded-[24px] border border-slate-200 bg-white p-6 ${heightClassName}`}>
            <article className="prose prose-slate max-w-none" dangerouslySetInnerHTML={{ __html: docHtml }} />
          </div>
        );
      }
    }

    return (
      <div className={`flex ${heightClassName} flex-col items-center justify-center rounded-[24px] border border-slate-200 bg-slate-50 px-6 text-center`}>
        <p className="text-base font-semibold text-slate-900">{file.name}</p>
        <p className="mt-2 text-sm text-slate-500">{docError || "Inline preview is available for DOCX files. Download this file to view it locally."}</p>
        <a
          href={downloadUrl}
          download={vaultDownloadFilename(file)}
          className="mt-5 inline-flex items-center justify-center rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
        >
          Download file
        </a>
      </div>
    );
  }

  return (
    <div className={`flex ${heightClassName} flex-col items-center justify-center rounded-[24px] border border-slate-200 bg-slate-50 px-6 text-center`}>
      <p className="text-base font-semibold text-slate-900">{file.name}</p>
      <p className="mt-2 text-sm text-slate-500">Inline preview is not available for this file type.</p>
      <a
        href={downloadUrl}
        download={vaultDownloadFilename(file)}
        className="mt-5 inline-flex items-center justify-center rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
      >
        Download file
      </a>
    </div>
  );
}
