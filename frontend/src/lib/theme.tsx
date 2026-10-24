import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

type Theme = "light" | "dark" | "system";
const Ctx = createContext<{ theme: Theme; setTheme: (t: Theme) => void; resolved: "light" | "dark" }>({
  theme: "system",
  setTheme: () => {},
  resolved: "light",
});

function readStored(): Theme {
  try {
    const v = localStorage.getItem("sn-theme");
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

/** Apply the theme attribute synchronously so components reading CSS tokens during render see the new values. */
function apply(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
  try {
    if (theme === "system") localStorage.removeItem("sn-theme");
    else localStorage.setItem("sn-theme", theme);
  } catch {
    /* storage unavailable: the theme still applies for this session */
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const t = readStored();
    apply(t);
    return t;
  });
  const [systemDark, setSystemDark] = useState(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const h = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mq?.addEventListener("change", h);
    return () => mq?.removeEventListener("change", h);
  }, []);
  const setTheme = (t: Theme) => {
    apply(t);
    setThemeState(t);
  };
  const resolved = theme === "system" ? (systemDark ? "dark" : "light") : theme;
  return <Ctx.Provider value={{ theme, setTheme, resolved }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);

/** Read a CSS custom property's current value (tokens switch with the theme). */
export const token = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
