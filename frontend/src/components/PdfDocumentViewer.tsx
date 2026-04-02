"use client";

import type { MutableRefObject } from "react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

type Props = {
  sourceUrl: string | null;
  filename: string;
  pageNumbers?: number[];
  scrollToPageNumber?: number | null;
  heightClassName?: string;
  roundedClassName?: string;
  pageRoundedClassName?: string;
};

type LazyPageCardProps = {
  pageNumber: number;
  filename: string;
  containerWidth: number;
  pageRoundedClassName: string;
  pageRefs: MutableRefObject<Record<number, HTMLDivElement | null>>;
  scrollRoot: HTMLDivElement | null;
};

function LazyPageCard({
  pageNumber,
  filename,
  containerWidth,
  pageRoundedClassName,
  pageRefs,
  scrollRoot,
}: LazyPageCardProps) {
  const [isVisible, setIsVisible] = useState(false);
  const estimatedHeight = Math.max(360, Math.round(containerWidth * 1.36) + 72);

  useEffect(() => {
    const node = pageRefs.current[pageNumber];
    if (!node || !scrollRoot) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        setIsVisible(entry?.isIntersecting ?? false);
      },
      {
        root: scrollRoot,
        rootMargin: "300px 0px",
        threshold: 0.01,
      },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, [pageNumber, pageRefs, scrollRoot]);

  return (
    <div
      ref={(node) => {
        pageRefs.current[pageNumber] = node;
      }}
      className={`mx-auto max-w-full overflow-hidden bg-white p-3 shadow-sm ${pageRoundedClassName}`}
      style={{ width: `${containerWidth + 24}px`, minHeight: `${estimatedHeight}px` }}
    >
      {isVisible ? (
        <Page
          pageNumber={pageNumber}
          width={containerWidth}
          renderTextLayer={false}
          renderAnnotationLayer={false}
          loading={<div className="py-16 text-center text-sm text-slate-400">Rendering page {pageNumber}...</div>}
          onRenderSuccess={() => {
            console.log("[PdfDocumentViewer] page rendered", {
              filename,
              pageNumber,
              width: containerWidth,
            });
          }}
          onRenderError={(error) => {
            console.error("[PdfDocumentViewer] page render error", {
              filename,
              pageNumber,
              message: error.message,
            });
          }}
        />
      ) : (
        <div
          className="flex items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-400"
          style={{ minHeight: `${estimatedHeight - 48}px` }}
        >
          Page {pageNumber} will render when in view
        </div>
      )}
      <p className="pt-3 text-center text-xs font-medium uppercase tracking-[0.2em] text-slate-400">
        {filename} - page {pageNumber}
      </p>
    </div>
  );
}

function PdfDocumentViewer({
  sourceUrl,
  filename,
  pageNumbers: pageNumbersOverride,
  scrollToPageNumber = null,
  heightClassName = "h-[720px]",
  roundedClassName = "rounded-[28px]",
  pageRoundedClassName = "rounded-3xl",
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [containerWidth, setContainerWidth] = useState<number | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [loadError, setLoadError] = useState("");
  const documentOptions = useMemo(() => ({ verbosity: 0 }), []);

  useEffect(() => {
    if (!containerRef.current) return;

    const updateWidth = () => {
      if (!containerRef.current) return;
      const nextWidth = Math.max(280, Math.floor(containerRef.current.clientWidth) - 24);
      setContainerWidth((current) => {
        if (current !== nextWidth) {
          console.log("[PdfDocumentViewer] container width changed", {
            filename,
            previousWidth: current,
            nextWidth,
          });
          return nextWidth;
        }
        return current;
      });
    };

    const scheduleUpdate = () => window.requestAnimationFrame(updateWidth);

    scheduleUpdate();
    const observer = new ResizeObserver(scheduleUpdate);
    observer.observe(containerRef.current);

    return () => observer.disconnect();
  }, [filename]);

  useEffect(() => {
    setNumPages(0);
    setLoadError("");
    console.log("[PdfDocumentViewer] source changed", {
      filename,
      sourceUrl,
    });
  }, [sourceUrl]);

  const pageNumbers = useMemo(() => {
    if (pageNumbersOverride && pageNumbersOverride.length > 0) {
      return pageNumbersOverride;
    }
    return Array.from({ length: numPages }, (_, index) => index + 1);
  }, [numPages, pageNumbersOverride]);

  useEffect(() => {
    if (!scrollToPageNumber) return;

    const target = pageRefs.current[scrollToPageNumber];
    if (!target) return;

    window.requestAnimationFrame(() => {
      target.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    });
  }, [scrollToPageNumber, pageNumbers]);

  return (
    <div
      ref={containerRef}
      className={`${heightClassName} ${roundedClassName} overflow-x-hidden overflow-y-auto border border-slate-200 bg-slate-100 p-3`}
      style={{ scrollbarGutter: "stable" }}
    >
      {!sourceUrl ? (
        <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-500">
          Source preview becomes available as soon as the encrypted upload is stored.
        </div>
      ) : containerWidth === null ? (
        <div className="flex h-full items-center justify-center px-6 text-center text-sm text-slate-500">
          Measuring preview viewport...
        </div>
      ) : (
        <Document
          key={sourceUrl}
          file={sourceUrl}
          options={documentOptions}
          loading={
            <div className="flex h-40 items-center justify-center text-sm font-medium text-slate-500">
              Loading PDF pages...
            </div>
          }
          onLoadSuccess={({ numPages: loadedPages }) => {
            setLoadError("");
            setNumPages(loadedPages);
            console.log("[PdfDocumentViewer] document loaded", {
              filename,
              loadedPages,
              containerWidth,
            });
          }}
          onLoadError={(error) => {
            setLoadError(error.message || "Unable to load the PDF preview.");
            console.error("[PdfDocumentViewer] document load error", {
              filename,
              sourceUrl,
              message: error.message,
            });
          }}
          className="space-y-4"
        >
          {loadError ? (
            <div className="flex h-40 items-center justify-center rounded-3xl bg-white px-6 text-center text-sm text-rose-600 shadow-sm">
              {loadError}
            </div>
          ) : (
            pageNumbers.map((pageNumber) => (
              <LazyPageCard
                key={pageNumber}
                pageNumber={pageNumber}
                filename={filename}
                containerWidth={containerWidth}
                pageRoundedClassName={pageRoundedClassName}
                pageRefs={pageRefs}
                scrollRoot={containerRef.current}
              />
            ))
          )}
        </Document>
      )}
    </div>
  );
}

export default memo(PdfDocumentViewer);
