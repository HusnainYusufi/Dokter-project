"use client";

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

export default function ToastStack({ toasts }: Props) {
  if (!toasts.length) return null;

  return (
    <div className="pointer-events-none fixed right-4 top-20 z-50 flex w-full max-w-sm flex-col gap-3">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`rounded-2xl border px-4 py-3 text-sm font-medium shadow-lg shadow-slate-200/60 backdrop-blur ${toneClasses(toast.tone)}`}
        >
          {toast.message}
        </div>
      ))}
    </div>
  );
}
