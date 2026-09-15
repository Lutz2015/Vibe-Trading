import { useCallback, useEffect, useRef, useState } from "react";
import { safeGet, safeSet } from "@/lib/storage";
import { publishThemeChange } from "@/lib/theme-store";

const STORAGE_KEY = "qa-theme";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

function readCookieTheme(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(/(?:^|;\s*)qa-theme=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function writeCookieTheme(value: "dark" | "light"): void {
  if (typeof document === "undefined") return;
  // Cookies ignore the port, so theme survives desktop backend port changes.
  document.cookie = `qa-theme=${value};path=/;max-age=${COOKIE_MAX_AGE};SameSite=Lax`;
}

function getSystemPreference(): boolean {
  if (typeof window.matchMedia !== "function") return false;
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

function getPreferredTheme(): boolean {
  const saved = safeGet(STORAGE_KEY) ?? readCookieTheme();
  if (saved === "dark") return true;
  if (saved === "light") return false;
  return getSystemPreference();
}

export function useDarkMode() {
  const [dark, setDark] = useState(getPreferredTheme);
  const darkRef = useRef(dark);

  useEffect(() => {
    darkRef.current = dark;
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
    publishThemeChange();
  }, [dark]);

  useEffect(() => {
    const syncTheme = (nextDark: boolean) => {
      const changed = darkRef.current !== nextDark;
      darkRef.current = nextDark;
      setDark(nextDark);
      if (!changed) publishThemeChange();
    };

    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY && event.key !== null) return;
      syncTheme(getPreferredTheme());
    };
    window.addEventListener("storage", onStorage);

    if (typeof window.matchMedia !== "function") {
      return () => window.removeEventListener("storage", onStorage);
    }

    let mediaQuery: MediaQueryList;
    try {
      mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    } catch {
      return () => window.removeEventListener("storage", onStorage);
    }

    const onSystemChange = (event: MediaQueryListEvent) => {
      if (safeGet(STORAGE_KEY) !== null) return;
      syncTheme(event.matches);
    };
    mediaQuery.addEventListener("change", onSystemChange);

    return () => {
      window.removeEventListener("storage", onStorage);
      mediaQuery.removeEventListener("change", onSystemChange);
    };
  }, []);

  const toggle = useCallback(() => {
    const nextDark = !darkRef.current;
    darkRef.current = nextDark;
    const value = nextDark ? "dark" : "light";
    safeSet(STORAGE_KEY, value);
    writeCookieTheme(value);
    setDark(nextDark);
  }, []);

  return { dark, toggle };
}
