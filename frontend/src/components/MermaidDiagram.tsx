"use client";

import { useEffect, useRef, useState } from "react";

export default function MermaidDiagram({ code }: { code: string }) {
  const [svg, setSvg] = useState<string>("");
  const [error, setError] = useState<string>("");
  const idRef = useRef(`mermaid-${Math.random().toString(36).slice(2)}`);

  useEffect(() => {
    let cancelled = false;

    async function render() {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "dark",
          themeVariables: {
            background: "#1a2236",
            primaryColor: "#3b82f6",
            primaryTextColor: "#f9fafb",
            primaryBorderColor: "#1f2d45",
            lineColor: "#6b7280",
            secondaryColor: "#111827",
            tertiaryColor: "#1a2236",
            edgeLabelBackground: "#111827",
            fontFamily: "ui-sans-serif, system-ui, sans-serif",
          },
        });

        const { svg: rendered } = await mermaid.render(idRef.current, code);
        if (!cancelled) setSvg(rendered);
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Failed to render diagram");
      }
    }

    render();
    return () => { cancelled = true; };
  }, [code]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3">
        <p className="mb-2 text-xs font-medium text-red-400">Diagram render failed</p>
        <pre className="overflow-x-auto text-xs text-red-400/70">{code}</pre>
      </div>
    );
  }

  if (!svg) {
    return (
      <div className="flex items-center gap-2 py-4 text-xs text-muted">
        <svg className="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        Rendering diagram…
      </div>
    );
  }

  return (
    <div
      className="overflow-x-auto rounded-lg border border-border bg-surface p-4 [&_svg]:max-w-full"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
