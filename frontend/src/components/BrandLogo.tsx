type BrandLogoProps = {
  compact?: boolean;
  theme?: "light" | "dark";
  iconSizeClassName?: string;
};

export default function BrandLogo({
  compact = false,
  theme = "dark",
  iconSizeClassName = "h-11 w-11",
}: BrandLogoProps) {
  const dark = theme === "dark";

  return (
    <div className="flex items-center gap-3">
      <div
        className={`flex ${iconSizeClassName} items-center justify-center rounded-2xl bg-gradient-to-br from-teal-400 to-blue-700 shadow-lg ${
          dark ? "shadow-blue-950/50" : "shadow-blue-900/20"
        }`}
      >
        <svg className={`${compact ? "h-5 w-5" : "h-6 w-6"} text-white`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m6-6H6" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M7 4h6.586a1 1 0 01.707.293l3.414 3.414A1 1 0 0118 8.414V18a2 2 0 01-2 2H8a2 2 0 01-2-2V6a2 2 0 012-2z" />
        </svg>
      </div>

      {!compact && (
        <div>
          <p className={`text-lg font-semibold tracking-wide ${dark ? "text-white" : "text-slate-950"}`}>Medical Intelligence</p>
          <p className={`text-xs uppercase tracking-[0.28em] ${dark ? "text-slate-400" : "text-slate-500"}`}>
            Medical | Legal | Intelligence
          </p>
        </div>
      )}
    </div>
  );
}
