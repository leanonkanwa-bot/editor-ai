import { useState, useCallback } from "react";
import type { Lang } from "./i18n";

const STORAGE_KEY = "lr_lang";

function detectLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "fr" || stored === "en") return stored;
    const nav = navigator.language || "";
    return nav.toLowerCase().startsWith("fr") ? "fr" : "en";
  } catch {
    return "fr";
  }
}

export function useLanguage() {
  const [lang, setLangState] = useState<Lang>(detectLang);

  const setLang = useCallback((l: Lang) => {
    try { localStorage.setItem(STORAGE_KEY, l); } catch {}
    setLangState(l);
  }, []);

  return { lang, setLang };
}
