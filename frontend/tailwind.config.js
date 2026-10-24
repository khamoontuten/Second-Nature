/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface-1)",
        plane: "var(--page-plane)",
        ink: { DEFAULT: "var(--text-primary)", 2: "var(--text-secondary)", 3: "var(--text-muted)" },
        line: "var(--gridline)",
        axis: "var(--baseline)",
        accent: "var(--series-1)",
        good: "var(--status-good)",
        warn: "var(--status-warning)",
        serious: "var(--status-serious)",
        critical: "var(--status-critical)",
      },
      fontFamily: { sans: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"] },
    },
  },
  plugins: [],
};
