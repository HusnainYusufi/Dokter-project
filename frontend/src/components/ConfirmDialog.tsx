"use client";

import { AnimatePresence, motion } from "framer-motion";

export type ConfirmTone = "danger" | "warning";

interface Props {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
  busy?: boolean;
  busyLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

const TONE_STYLES: Record<ConfirmTone, { icon: string; confirm: string; path: string }> = {
  danger: {
    icon: "border-rose-200 bg-rose-50 text-rose-600",
    confirm: "bg-rose-600 hover:bg-rose-700",
    path: "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z",
  },
  warning: {
    icon: "border-amber-200 bg-amber-50 text-amber-600",
    confirm: "bg-slate-950 hover:bg-slate-800",
    path: "M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
  },
};

/**
 * Shared confirmation modal. Replaces `window.confirm` for destructive and
 * lossy actions so they match the rest of the portal instead of surfacing a
 * browser dialog.
 */
export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  tone = "danger",
  busy = false,
  busyLabel,
  onConfirm,
  onCancel,
}: Props) {
  const styles = TONE_STYLES[tone];

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key="confirm-dialog"
          className="fixed inset-0 z-[100] flex items-center justify-center px-4 py-8"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          aria-describedby="confirm-dialog-desc"
        >
          <motion.button
            type="button"
            aria-label="Dismiss"
            disabled={busy}
            className="absolute inset-0 bg-slate-950/50 disabled:cursor-not-allowed"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => {
              if (!busy) onCancel();
            }}
          />

          <motion.div
            className="relative z-10 w-full max-w-md rounded-[24px] border border-slate-200 bg-white p-6 shadow-2xl shadow-slate-950/20"
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.98 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            <div className="flex gap-4">
              <div
                className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border ${styles.icon}`}
                aria-hidden
              >
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d={styles.path} />
                </svg>
              </div>
              <div className="min-w-0">
                <p className="text-base font-semibold text-slate-950">{title}</p>
                <p id="confirm-dialog-desc" className="mt-1 text-sm leading-6 text-slate-500">
                  {description}
                </p>
              </div>
            </div>
            <div className="mt-6 flex flex-wrap justify-end gap-3">
              <button
                type="button"
                disabled={busy}
                onClick={onCancel}
                className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {cancelLabel}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={onConfirm}
                className={`rounded-xl px-4 py-2.5 text-sm font-semibold text-white transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:cursor-not-allowed disabled:opacity-50 ${styles.confirm}`}
              >
                {busy ? (busyLabel ?? "Working…") : confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
