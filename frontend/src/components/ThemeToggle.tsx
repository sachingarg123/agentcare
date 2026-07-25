import { useTheme } from "../theme/ThemeContext";

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === "dark";

  return (
    <label className={`theme-switch${compact ? " theme-switch-compact" : ""}`}>
      {!compact && <span className="theme-switch-label">Light</span>}
      <button
        type="button"
        className={`theme-switch-track${isDark ? " on" : ""}`}
        role="switch"
        aria-checked={isDark}
        aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
        onClick={toggleTheme}
      >
        <span className="theme-switch-thumb" />
      </button>
      {!compact && <span className="theme-switch-label">Dark</span>}
      {compact && (
        <span className="theme-switch-hint" aria-hidden="true">
          {isDark ? "Dark" : "Light"}
        </span>
      )}
    </label>
  );
}
