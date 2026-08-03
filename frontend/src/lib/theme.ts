export type Theme = "dark" | "light";

export const DEFAULT_THEME: Theme = "dark";
export const THEME_STORAGE_KEY = "arthasignal-theme";

export function isTheme(value: string | null): value is Theme {
  return value === "dark" || value === "light";
}

export function getStoredTheme(): Theme {
  if (typeof window === "undefined") {
    return DEFAULT_THEME;
  }
  const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  return isTheme(stored) ? stored : DEFAULT_THEME;
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  window.localStorage.setItem(THEME_STORAGE_KEY, theme);
}

export const THEME_INIT_SCRIPT = `
(function() {
  try {
    var stored = localStorage.getItem("${THEME_STORAGE_KEY}");
    var theme = stored === "light" || stored === "dark" ? stored : "${DEFAULT_THEME}";
    document.documentElement.dataset.theme = theme;
  } catch (e) {
    document.documentElement.dataset.theme = "${DEFAULT_THEME}";
  }
})();
`;
