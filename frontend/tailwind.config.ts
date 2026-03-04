import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "#0a0d14",
        surface: "#111827",
        card: "#1a2236",
        border: "#1f2d45",
        accent: "#3b82f6",
        "accent-hover": "#2563eb",
        muted: "#6b7280",
        subtle: "#374151",
      },
    },
  },
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  plugins: [require("@tailwindcss/typography")],
};

export default config;
