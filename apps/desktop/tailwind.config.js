/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Composed and dark, matching the persona: this is a tool that sits quietly on a
        // desk all day, not a dashboard competing for attention.
        // `void` is darker than ink-950 and exists for the full-screen HUD: the graph's glow
        // needs somewhere to fall off to, and #0a0b0d is already a shade of grey.
        ink: {
          void: "#04060a",
          950: "#0a0b0d", 900: "#111318", 800: "#181b22", 700: "#232733", 600: "#333849",
        },
        thursday: { DEFAULT: "#6ea8fe", dim: "#3d6bb5", glow: "#9dc4ff" },
        state: {
          idle: "#4b5563", listening: "#6ea8fe", thinking: "#a78bfa",
          working: "#38bdf8", speaking: "#34d399", warning: "#fbbf24", error: "#f87171",
        },
      },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "monospace"] },
      animation: { "pulse-slow": "pulse 3s cubic-bezier(0.4,0,0.6,1) infinite", breathe: "breathe 4s ease-in-out infinite" },
      keyframes: {
        breathe: { "0%,100%": { transform: "scale(1)", opacity: "0.85" }, "50%": { transform: "scale(1.06)", opacity: "1" } },
      },
    },
  },
  plugins: [],
};
