import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { safeRemove } from "@/lib/storage";
import en from "./locales/en.json";

// Language registry — keep in sync with the Layout switcher and README_xx.md.
// `dir` flags whether the language is right-to-left so the app can mirror the
// layout (sidebar on the right, etc.) when needed.
export const SUPPORTED_LANGUAGES = [
  { code: "en", label: "English", dir: "ltr" as const },
  { code: "zh-CN", label: "中文", dir: "ltr" as const },
  { code: "ja", label: "日本語", dir: "ltr" as const },
  { code: "ko", label: "한국어", dir: "ltr" as const },
  { code: "ar", label: "العربية", dir: "rtl" as const },
  { code: "es", label: "Español", dir: "ltr" as const },
  { code: "de", label: "Deutsch", dir: "ltr" as const },
  { code: "pt-BR", label: "Português (Brasil)", dir: "ltr" as const },
] as const;

export type SupportedLanguageCode = (typeof SUPPORTED_LANGUAGES)[number]["code"];
type LazyLanguageCode = Exclude<SupportedLanguageCode, "en">;

const localeLoaders = {
  "zh-CN": () => import("./locales/zh-CN.json"),
  ja: () => import("./locales/ja.json"),
  ko: () => import("./locales/ko.json"),
  ar: () => import("./locales/ar.json"),
  es: () => import("./locales/es.json"),
  de: () => import("./locales/de.json"),
  "pt-BR": () => import("./locales/pt-BR.json"),
} satisfies Record<LazyLanguageCode, () => Promise<{ default: typeof en }>>;

const LANGUAGE_STORAGE_KEY = "i18nextLng";
const LOCALE_LOAD_ATTEMPTS = 2;
const loadedLanguages = new Set<SupportedLanguageCode>(["en"]);
const loadingLanguages = new Map<LazyLanguageCode, Promise<void>>();
let lastStableLanguage: SupportedLanguageCode = "en";

function getLazyLanguage(code: string): LazyLanguageCode | undefined {
  return (Object.keys(localeLoaders) as LazyLanguageCode[]).find(
    (supportedCode) => code === supportedCode || code.startsWith(`${supportedCode}-`),
  );
}

async function loadLanguage(code: LazyLanguageCode): Promise<void> {
  if (loadedLanguages.has(code)) return;

  const inFlight = loadingLanguages.get(code);
  if (inFlight) return inFlight;

  const load = (async () => {
    let lastError: unknown;
    for (let attempt = 0; attempt < LOCALE_LOAD_ATTEMPTS; attempt += 1) {
      try {
        const { default: resources } = await localeLoaders[code]();
        i18n.addResourceBundle(code, "translation", resources, true, true);
        loadedLanguages.add(code);
        return;
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError;
  })().finally(() => loadingLanguages.delete(code));

  loadingLanguages.set(code, load);
  return load;
}

const RTL_CODES = new Set<SupportedLanguageCode>(
  SUPPORTED_LANGUAGES.filter((l) => l.dir === "rtl").map((l) => l.code),
);

export function isRtl(code: string): boolean {
  if (RTL_CODES.has(code as SupportedLanguageCode)) return true;
  // Handle regional variants: "ar-EG" → match "ar", "he-IL" → match "he" (if added).
  return [...RTL_CODES].some((rtl) => code.startsWith(rtl + "-"));
}

export function applyDocumentDirection(code: string): void {
  if (typeof document === "undefined") return;
  const dir = isRtl(code) ? "rtl" : "ltr";
  document.documentElement.setAttribute("dir", dir);
  document.documentElement.setAttribute("lang", code);
}

// Keep the <html dir/lang> attributes in sync with the active language and
// lazily register non-English resources. English remains available as the
// fallback while a requested locale is loading, so the UI never renders keys.
i18n.on("languageChanged", (lng) => {
  applyDocumentDirection(lng);

  const lazyLanguage = getLazyLanguage(lng);
  if (!lazyLanguage) {
    lastStableLanguage = "en";
    return;
  }
  if (loadedLanguages.has(lazyLanguage)) {
    lastStableLanguage = lazyLanguage;
    return;
  }

  const previousLanguage = lastStableLanguage;

  void loadLanguage(lazyLanguage)
    .then(() => {
      // Do not let a slow import undo a newer language selection.
      if (getLazyLanguage(i18n.language) === lazyLanguage) {
        return i18n.changeLanguage(lazyLanguage);
      }
    })
    .catch(async (error: unknown) => {
      // A later selection owns the document now; do not roll it back.
      if (getLazyLanguage(i18n.language) !== lazyLanguage) return;

      console.warn(
        `[i18n] Failed to load locale "${lazyLanguage}" after ${LOCALE_LOAD_ATTEMPTS} attempts; reverting to "${previousLanguage}".`,
        error,
      );
      safeRemove(LANGUAGE_STORAGE_KEY);
      await i18n.changeLanguage(previousLanguage);
      // LanguageDetector caches every change, including the rollback. Keep the
      // failed explicit selection cleared so the next load starts from a safe default.
      safeRemove(LANGUAGE_STORAGE_KEY);
    });
});

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
    },
    initAsync: false,
    // Default UI language is Chinese. An explicit toggle (cookie/localStorage)
    // overrides this; navigator is intentionally not used so a fresh install
    // on an English OS still opens in Chinese unless the user switches.
    fallbackLng: "zh-CN",
    supportedLngs: SUPPORTED_LANGUAGES.map((l) => l.code),
    // NOTE: Intentionally NOT using nonExplicitSupportedLngs — it strips
    // region codes from compound language keys like "zh-CN" which causes
    // isSupportedCode to reject them ("zh-CN" → "zh", not in supportedLngs).
    interpolation: { escapeValue: false },
    detection: {
      // Desktop backend restarts pick a new 127.0.0.1 port, which changes the
      // origin and isolates localStorage. Cookies ignore the port, so cache
      // the explicit choice there first and keep localStorage as a secondary.
      // No navigator step: default remains zh-CN.
      order: typeof window !== "undefined"
        ? ["cookie", "localStorage"]
        : ["cookie", "localStorage"],
      caches: ["cookie", "localStorage"],
      lookupCookie: "i18nextLng",
      lookupLocalStorage: "i18nextLng",
      cookieMinutes: 60 * 24 * 365,
      cookieOptions: { path: "/", sameSite: "lax" },
    },
  });

applyDocumentDirection(i18n.language || "zh-CN");
i18n.on("initialized", () => {
  if (i18n.language) applyDocumentDirection(i18n.language);
});

// Desktop backend restarts change the loopback port (origin). Migrate any
// language choice saved under the previous origin's localStorage onto a
// host-wide cookie so the next boot keeps the user's language.
try {
  const saved = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  if (saved && !document.cookie.includes(`${LANGUAGE_STORAGE_KEY}=`)) {
    document.cookie = `${LANGUAGE_STORAGE_KEY}=${encodeURIComponent(saved)};path=/;max-age=${60 * 60 * 24 * 365};SameSite=Lax`;
  }
} catch {
  // Storage unavailable — detector falls back to navigator.
}

export default i18n;
