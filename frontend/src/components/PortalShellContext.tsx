"use client";

import { createContext, useContext } from "react";

export type PortalPickerTab = "upload" | "vault";

interface PortalShellContextValue {
  openSourcePicker: (tab?: PortalPickerTab) => void;
  demoMode: boolean;
  demoExtractBusy: boolean;
  setDemoExtractBusy: (busy: boolean) => void;
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
