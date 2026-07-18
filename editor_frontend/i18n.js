/**
 * LeanRetention i18n — Commit 1: infrastructure only.
 * TRANSLATIONS dicts are empty; strings will be filled in Commit 2.
 * Exposes: window.t(key), window.setLang(lang), window.getLang(), window.initLang()
 */
(function (root) {
  'use strict';

  // ── Translation dictionaries (populated in Commit 2) ────────────────────────
  var TRANSLATIONS = {
    fr: {},
    en: {}
  };

  // ── Core helpers ─────────────────────────────────────────────────────────────

  function _detect() {
    var nav = ((navigator.language || navigator.userLanguage) || 'fr').toLowerCase();
    return nav.startsWith('fr') ? 'fr' : 'en';
  }

  function getLang() {
    return localStorage.getItem('lle_lang') || _detect();
  }

  /** Translate key → string in current language, fall back to fr, then to key itself. */
  function t(key) {
    var lang = getLang();
    var dict = TRANSLATIONS[lang] || TRANSLATIONS['fr'];
    if (key in dict) return dict[key];
    var fr = TRANSLATIONS['fr'];
    if (lang !== 'fr' && key in fr) return fr[key];
    return key;
  }

  // ── DOM update ───────────────────────────────────────────────────────────────

  function _apply() {
    var lang = getLang();
    var dict = TRANSLATIONS[lang] || TRANSLATIONS['fr'];
    var fr   = TRANSLATIONS['fr'];

    function _val(key) {
      return (key in dict) ? dict[key] : ((key in fr) ? fr[key] : null);
    }

    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var v = _val(el.getAttribute('data-i18n'));
      if (v !== null) el.textContent = v;
    });
    document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      var v = _val(el.getAttribute('data-i18n-html'));
      if (v !== null) el.innerHTML = v;
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      var v = _val(el.getAttribute('data-i18n-placeholder'));
      if (v !== null) el.placeholder = v;
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      var v = _val(el.getAttribute('data-i18n-title'));
      if (v !== null) el.title = v;
    });

    // Reflect active language on <html> and on toggle buttons
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-lang-btn]').forEach(function (btn) {
      btn.classList.toggle('lle-lang-active', btn.getAttribute('data-lang-btn') === lang);
    });
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  function setLang(lang) {
    if (lang !== 'fr' && lang !== 'en') return;
    localStorage.setItem('lle_lang', lang);
    _apply();
    // Persist to server profile (fire-and-forget; fails silently for non-OAuth users)
    if (localStorage.getItem('profile_id')) {
      fetch('/api/profile/language', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: lang })
      }).catch(function () {});
    }
  }

  /**
   * Call once at page load. Detects language from navigator if nothing is
   * stored, then applies translations to the DOM (deferred until DOMContentLoaded
   * if called from <head> before the document is parsed).
   */
  function initLang() {
    if (!localStorage.getItem('lle_lang')) {
      localStorage.setItem('lle_lang', _detect());
    }
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', _apply);
    } else {
      _apply();
    }
  }

  // Expose
  root.t            = t;
  root.setLang      = setLang;
  root.getLang      = getLang;
  root.initLang     = initLang;
  root.TRANSLATIONS = TRANSLATIONS;

}(typeof window !== 'undefined' ? window : this));
