import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Light palette. Component class names (bg-bg, bg-panel, border-border,
        // text-muted, text-accent, text-fg) stay the same so swapping themes
        // later only requires editing this file.
        bg: "#ffffff",
        panel: "#f5f7fa",
        border: "#e5e7eb",
        muted: "#6b7280",
        accent: "#2563eb",
        fg: "#111827",
      },
    },
  },
  plugins: [],
};

export default config;
