"use client";

import { AnimatePresence } from "framer-motion";
import { useEffect, useMemo, useState } from "react";

import DocumentSourceModal from "@/components/DocumentSourceModal";
import PortalHeader from "@/components/PortalHeader";
import PortalSidebar from "@/components/PortalSidebar";
import {
  PortalShellProvider,
  type PageHeaderCopy,
  type PortalPickerTab,
} from "@/components/PortalShellContext";
import { isDemoMode } from "@/lib/demoMode";

export default function PortalShell({ children }: { children: React.ReactNode }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerTab, setPickerTab] = useState<PortalPickerTab>("upload");
  const [demoExtractBusy, setDemoExtractBusy] = useState(false);
  const [headerCopy, setHeaderCopy] = useState<PageHeaderCopy | null>(null);
  const demoMode = useMemo(() => isDemoMode(), []);

  useEffect(() => {
    if (!demoMode) {
      setDemoExtractBusy(false);
    }
  }, [demoMode]);

  const contextValue = useMemo(
    () => ({
      openSourcePicker: (tab: PortalPickerTab = "upload") => {
        setPickerTab(tab);
        setPickerOpen(true);
      },
      demoMode,
      demoExtractBusy,
      setDemoExtractBusy,
      headerCopy,
      setHeaderCopy,
    }),
    [demoMode, demoExtractBusy, headerCopy],
  );

  return (
    <PortalShellProvider value={contextValue}>
      <div className="min-h-screen bg-slate-100 lg:flex">
        <PortalSidebar />
        <div className="min-w-0 flex-1">
          <PortalHeader />
          <main>{children}</main>
        </div>
      </div>

      <AnimatePresence mode="wait">
        {pickerOpen && <DocumentSourceModal initialTab={pickerTab} onClose={() => setPickerOpen(false)} />}
      </AnimatePresence>
    </PortalShellProvider>
  );
}
