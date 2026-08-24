"use client";

import { createContext, useContext, useEffect } from "react";

export type PortalPickerTab = "upload" | "vault";

export interface PageHeaderCopy {
  title: string;
  subtitle: string;
  /** Optional pill shown at the right of the header (e.g. "Encrypted storage"). */
  badge?: string | null;
}

interface PortalShellContextValue {
  openSourcePicker: (tab?: PortalPickerTab) => void;
  demoMode: boolean;
  demoExtractBusy: boolean;
  setDemoExtractBusy: (busy: boolean) => void;
  /** Header copy registered by the current page, or null to use the route default. */
  headerCopy: PageHeaderCopy | null;
  setHeaderCopy: (copy: PageHeaderCopy | null) => void;
}

const PortalShellContext = createContext<PortalShellContextValue | null>(null);

export function PortalShellProvider({
  children,
  value,
}: {
  children: React.ReactNode;
  value: PortalShellContextValue;
}) {
  return <PortalShellContext.Provider value={value}>{children}</PortalShellContext.Provider>;
}

export function usePortalShell() {
  const context = useContext(PortalShellContext);
  if (!context) {
    throw new Error("usePortalShell must be used within PortalShellProvider.");
  }
  return context;
}

/**
 * Let a page own the shell header instead of duplicating a title block inside
 * its own first card. Registers on mount and clears on unmount so the header
 * never keeps stale copy after navigating away.
 */
export function usePageHeader(copy: PageHeaderCopy) {
  const { setHeaderCopy } = usePortalShell();
  const { title, subtitle, badge } = copy;

  useEffect(() => {
    setHeaderCopy({ title, subtitle, badge: badge ?? null });
    return () => setHeaderCopy(null);
  }, [setHeaderCopy, title, subtitle, badge]);
}
