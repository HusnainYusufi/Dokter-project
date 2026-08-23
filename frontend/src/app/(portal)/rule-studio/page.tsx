"use client";

import RuleStudioWorkspace from "@/components/RuleStudioWorkspace";
import { usePageHeader } from "@/components/PortalShellContext";

export default function RuleStudioPage() {
  // The shell header is the single source of truth for the page title, so this
  // page renders no title block of its own.
  usePageHeader({
    title: "Rule Studio",
    subtitle: "Author the golden rules and per-document-type behavior the AI follows.",
  });

  return (
    <div className="mx-auto flex h-[calc(100vh-8.5rem)] min-h-[40rem] w-full max-w-7xl flex-col px-4 py-6 sm:px-6 lg:px-8">
      <RuleStudioWorkspace />
    </div>
  );
}
