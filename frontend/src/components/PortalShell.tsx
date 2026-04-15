"use client";

import { AnimatePresence } from "framer-motion";
import { useEffect, useMemo, useState } from "react";

import DocumentSourceModal from "@/components/DocumentSourceModal";
import PortalHeader from "@/components/PortalHeader";
import { PortalShellProvider, type PortalPickerTab } from "@/components/PortalShellContext";
import { isDemoMode } from "@/lib/demoMode";

export default function PortalShell({ children }: { children: React.ReactNode }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerTab, setPickerTab] = useState<PortalPickerTab>("upload");
  const [demoExtractBusy, setDemoExtractBusy] = useState(false);
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
    }),
    [demoMode, demoExtractBusy],
  );

  return (
    <PortalShellProvider value={contextValue}>
      <div className="min-h-screen bg-slate-100">
        <PortalHeader />
        <main>{children}</main>
      </div>

      <AnimatePresence mode="wait">
        {pickerOpen && (
          <DocumentSourceModal
            pickerTab={pickerTab}
            onClose={() => setPickerOpen(false)}
            onPickerTabChange={setPickerTab}
          />
        )}
      </AnimatePresence>
    </PortalShellProvider>
  );
}
