"use client";

import { AnimatePresence, motion } from "framer-motion";

type ToastTone = "info" | "success" | "error";

export type ToastItem = {
  id: string;
  message: string;
  tone: ToastTone;
};

type Props = {
  toasts: ToastItem[];
};

function toneClasses(tone: ToastTone) {
  if (tone === "success") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (tone === "error") {
    return "border-rose-200 bg-rose-50 text-rose-700";
  }
  return "border-sky-200 bg-sky-50 text-sky-700";
}

function toneDotClass(tone: ToastTone) {
  if (tone === "success") return "bg-emerald-500";
  if (tone === "error") return "bg-rose-500";
  return "bg-sky-500";
}

export default function ToastStack({ toasts }: Props) {
  if (!toasts.length) return null;

  return (
    <div className="pointer-events-none fixed inset-0 z-50 overflow-hidden">
      <div className="absolute right-4 top-20 w-[min(22rem,calc(100vw-2rem))]">
        <AnimatePresence initial={false}>
          {toasts.map((toast, index) => (
            <motion.div
              key={toast.id}
              className={`absolute left-0 right-0 rounded-2xl border px-4 py-3 text-sm shadow-lg shadow-slate-200/60 backdrop-blur ${toneClasses(toast.tone)}`}
              style={{ transform: `translateY(${index * 78}px)` }}
              initial={{ opacity: 0, x: 18, scale: 0.98 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 18, scale: 0.98 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
            >
              <div className="flex items-start gap-3">
                <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${toneDotClass(toast.tone)}`} />
                <p className="font-medium leading-6">{toast.message}</p>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
