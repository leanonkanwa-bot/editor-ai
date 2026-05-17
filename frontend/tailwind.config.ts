import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans:    ["Poppins", "system-ui", "sans-serif"],
        heading: ["Poppins", "system-ui", "sans-serif"],
      },
      colors: {
        // Override slate with pure neutral blacks (no blue tint)
        slate: {
          50:  "#fafafa",
          100: "#f0f0f0",
          200: "#d4d4d4",
          300: "#aaaaaa",
          400: "#888888",
          500: "#555555",
          600: "#3a3a3a",
          700: "#262626",
          800: "#1a1a1a",
          900: "#111111",
          950: "#0a0a0a",
        },
        brand: {
          50:  "#fff4ee",
          100: "#ffe2cc",
          200: "#ffc59a",
          300: "#ffaa70",
          400: "#ff8c47",
          500: "#ff751f",
          600: "#e5600e",
          700: "#b84a08",
          800: "#8a3606",
          900: "#3d1a08",
          950: "#1e0d04",
        },
      },
      boxShadow: {
        "glow-brand":   "0 0 24px rgba(255,117,31,0.18)",
        "glow-brand-lg":"0 0 48px rgba(255,117,31,0.22)",
        "glow-sm":      "0 0 12px rgba(255,117,31,0.12)",
      },
      animation: {
        "fade-in":  "fadeIn .15s ease-out",
        "slide-up": "slideUp .2s ease-out",
      },
      keyframes: {
        fadeIn:  { from: { opacity: "0", transform: "translateY(4px)" },  to: { opacity: "1", transform: "none" } },
        slideUp: { from: { opacity: "0", transform: "translateY(12px)" }, to: { opacity: "1", transform: "none" } },
      },
      backgroundImage: {
        "grid-dark": "linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px)",
      },
      backgroundSize: {
        "grid": "32px 32px",
      },
    },
  },
  plugins: [],
} satisfies Config;
