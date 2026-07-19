/**
 * LeanRetention i18n — Commit 2: translations filled in.
 * Exposes: window.t(key), window.setLang(lang), window.getLang(), window.initLang()
 */
(function (root) {
  'use strict';

  // ── Translation dictionaries ─────────────────────────────────────────────────
  var TRANSLATIONS = {
    fr: {
      // ── NAV (landing) ────────────────────────────────────────────────────────
      nav_login: "Se connecter",
      nav_cta: "Commencer gratuitement",

      // ── HERO ─────────────────────────────────────────────────────────────────
      hero_title1: "Vos vidéos brutes.",
      hero_glow: "Éditées comme un pro.",
      hero_sub: "LeanRetention analyse votre contenu, réécrit votre hook, supprime les silences et publie automatiquement.",
      hero_cta1: "Éditer ma première vidéo →",
      hero_cta2: "Voir comment ça marche",
      trust_free: "Première vidéo gratuite",
      trust_no_card: "Sans carte bancaire",
      trust_time: "Résultats en 3 minutes",
      demo_before: "Avant",
      demo_after: "Après",

      // ── EMAIL CAPTURE ─────────────────────────────────────────────────────────
      email_title: "Obtenez votre première vidéo éditée gratuitement",
      email_sub: "Sans carte bancaire. Connexion en un clic.",
      google_btn: "Continuer avec Google",

      // ── HOW IT WORKS ─────────────────────────────────────────────────────────
      how_tag: "Comment ça marche",
      how_title: "De la vidéo brute au contenu viral<br>en 3 étapes",
      how_sub: "Aucune compétence en montage requise. L'IA gère tout de A à Z.",
      step1_title: "Uploadez votre vidéo brute",
      step1_desc: "Glissez-déposez votre fichier mp4, mov ou webm, jusqu'à 20 Go. Notre upload sécurisé par chunks gère les fichiers lourds sans problème.",
      step2_title: "L'IA analyse et édite automatiquement",
      step2_desc: "Transcription, réécriture du hook, suppression des silences, ajout de captions et de graphics IA : tout en quelques minutes, pas des heures.",
      step3_title: "Téléchargez ou publiez directement",
      step3_desc: "Récupérez votre MP4 prêt à poster, ou publiez en 1 clic sur YouTube, TikTok, Instagram et LinkedIn.",

      // ── FEATURES ─────────────────────────────────────────────────────────────
      feat_tag: "Fonctionnalités",
      feat_title: "Tout ce dont un créateur a besoin",
      feat_sub: "Un seul outil. Six fonctionnalités qui remplacent une équipe entière.",
      feat1_desc: "L'IA réécrit les 3 premières secondes de votre vidéo pour maximiser la rétention dès le départ.",
      feat2_title: "Suppression des silences",
      feat2_desc: "Détecte et supprime automatiquement les pauses, hésitations et silences qui font décrocher vos viewers.",
      feat3_title: "Captions automatiques",
      feat3_desc: "Sous-titres word-by-word précis en français, anglais, espagnol et plus encore.",
      feat4_title: "Graphics IA",
      feat4_desc: "16 types de cartes graphiques animées : stats, timelines, comparaisons, listes, générées et synchronisées avec votre discours.",
      feat5_title: "Export multi-formats",
      feat5_desc: "Téléchargez votre vidéo prête en vertical 9:16 (TikTok/Reels) et horizontal 16:9 (YouTube), sans ré-éditer.",
      feat6_title: "6 styles visuels",
      feat6_desc: "Choisissez parmi 6 identités visuelles, du premium dark au manuscrit authentique, chacune avec ses propres animations et transitions.",

      // ── PRICING ──────────────────────────────────────────────────────────────
      pricing_tag: "Tarifs",
      pricing_title: "Simple. Transparent. Sans surprise.",
      pricing_sub: "Commencez gratuitement. Évoluez quand vous en avez besoin.",
      pricing_per_month: "/ mois",
      pricing_popular: "POPULAIRE",
      plan_free_name: "Essai gratuit",
      plan_free_desc: "Testez avec 1 vidéo, sans engagement.",
      plan_free_f1: "1 vidéo (unique)",
      plan_free_f2: "Tous les styles visuels",
      plan_free_f3: "Captions IA",
      plan_free_btn: "Essayer gratuitement",
      plan_starter_desc: "Pour le coach solo qui démarre sa présence vidéo.",
      plan_f_6styles: "6 styles visuels",
      plan_f_captions_hook: "Captions + Hook Rewriter",
      plan_starter_btn: "Choisir Starter",
      plan_pro_desc: "Pour le créateur établi qui poste quotidiennement.",
      plan_f_graphics: "Graphics IA",
      plan_f_priority_support: "Support prioritaire",
      plan_pro_btn: "Choisir Pro →",
      plan_agency_desc: "Pour les agences gérant plusieurs créateurs.",
      plan_f_multi_accounts: "Multi-comptes",
      plan_agency_btn: "Contacter l'équipe →",

      // ── FAQ ───────────────────────────────────────────────────────────────────
      faq_tag: "FAQ",
      faq_title: "Questions fréquentes",
      faq_sub: "Tout ce que vous devez savoir avant de commencer.",
      faq1_q: "Mes vidéos sont-elles stockées sur vos serveurs ?",
      faq1_answer: "Vos vidéos sont traitées de façon sécurisée et supprimées automatiquement après 24h. Nous ne partageons jamais votre contenu avec des tiers.",

      // ── FINAL CTA ────────────────────────────────────────────────────────────
      cta_title: "Prêt à arrêter de perdre<br>des heures à éditer&nbsp;?",
      cta_sub: "Première vidéo offerte. Aucune carte requise.",

      // ── FOOTER ───────────────────────────────────────────────────────────────
      footer_features: "Fonctionnalités",
      footer_pricing: "Tarifs",
      footer_contact: "Contact",
      footer_copy: "© 2026 LeanRetention. Tous droits réservés.",

      // ── INDEX — SIDEBAR ───────────────────────────────────────────────────────
      tab_editor: "Éditeur",
      tab_profile: "Profil",

      // ── INDEX — NOTIFICATIONS ─────────────────────────────────────────────────
      notif_empty: "Aucune notification",
      notif_clear: "Tout effacer",

      // ── INDEX — LOGIN ─────────────────────────────────────────────────────────
      login_title: "Connexion requise",
      login_sub: "Entrez votre mot de passe pour accéder à l'éditeur.",
      login_pwd_ph: "Mot de passe",
      login_btn: "Se connecter",

      // ── INDEX — ONBOARDING CARD ───────────────────────────────────────────────
      ob_card_title: "Démarrage rapide",
      ob_step1: "Créer votre profil coach",
      ob_step2: "Éditer votre 1ère vidéo",
      ob_step3: "Configurer votre identité de marque",
      ob_step4: "Atteindre 5 vidéos éditées",
      ob_step5: "Compléter votre profil ICP",

      // ── INDEX — DROP ZONE ─────────────────────────────────────────────────────
      drop_hint: "MP4, MOV, MKV · Jusqu'à 4 Go",

      // ── INDEX — FORMAT PILLS ──────────────────────────────────────────────────
      fmt_short: "Court · Reels",
      fmt_long: "Long · YouTube",

      // ── INDEX — STYLE PACK ────────────────────────────────────────────────────
      style_label: "Style visuel",
      pack_glass_desc: "Pour paraître l'autorité incontestée de ton secteur",
      pack_paper_desc: "Pour qu'on te prenne au sérieux dès la première seconde",
      pack_vibe_desc: "Pour arrêter le scroll et faire exploser ton reach",
      pack_ledger_desc: "Pour que les investisseurs et clients premium te fassent confiance",
      pack_craft_desc: "Pour qu'on sente que c'est VRAIMENT toi qui parles",
      pack_cinema_desc: "Pour transformer ton histoire en moment inoubliable",

      // ── INDEX — EDITOR SUBMIT ─────────────────────────────────────────────────
      editor_submit: "Éditer ma vidéo",

      // ── INDEX — RESULT ────────────────────────────────────────────────────────
      result_download: "Télécharger",
      ctr_label: "Titres optimisés CTR",
      details_more: "Voir plus",
      caption_editor_label: "Éditeur de captions",
      reburn_btn: "Re-brûler les captions",
      reburn_msg: "Captions mis à jour !",
      chapters_label: "Chapitres YouTube",
      copy_chapters_btn: "Copier les chapitres",
      desc_gen_btn: "Générer les descriptions",
      copy_btn: "Copier",
      publish_title: "Publier",
      publish_btn: "Publier sur les plateformes sélectionnées",

      // ── INDEX — DASHBOARD ─────────────────────────────────────────────────────
      stat_videos: "Vidéos éditées",
      stat_time: "Temps économisé",
      stat_month: "Ce mois",
      dash_new_video: "Nouvelle vidéo",
      dash_lib_label: "Bibliothèque de vidéos",
      lib_tab_active: "Bibliothèque",
      lib_tab_trash: "Corbeille",
      lib_empty: "Aucune vidéo éditée pour l'instant.<br>Déposez votre première vidéo dans l'éditeur.",

      // ── INDEX — PROFILE ───────────────────────────────────────────────────────
      profile_plan: "Mon plan",
      profile_icp: "Client idéal (ICP)",
      profile_pillars: "Piliers de contenu",
      profile_icp_ph: "Décrivez votre audience cible…",
      profile_change_plan: "Changer de plan",
      profile_save_btn: "Enregistrer",
      profile_edit_btn: "Modifier ICP & piliers",
      profile_saved_msg: "Profil enregistré."
    },

    en: {
      // ── NAV (landing) ────────────────────────────────────────────────────────
      nav_login: "Sign in",
      nav_cta: "Start for free",

      // ── HERO ─────────────────────────────────────────────────────────────────
      hero_title1: "Your raw footage.",
      hero_glow: "Edited like a pro.",
      hero_sub: "LeanRetention analyzes your content, rewrites your hook, removes silences, and publishes automatically.",
      hero_cta1: "Edit my first video →",
      hero_cta2: "See how it works",
      trust_free: "First video free",
      trust_no_card: "No credit card required",
      trust_time: "Results in 3 minutes",
      demo_before: "Before",
      demo_after: "After",

      // ── EMAIL CAPTURE ─────────────────────────────────────────────────────────
      email_title: "Get your first video edited for free",
      email_sub: "No credit card. One-click sign in.",
      google_btn: "Continue with Google",

      // ── HOW IT WORKS ─────────────────────────────────────────────────────────
      how_tag: "How it works",
      how_title: "From raw footage to viral content<br>in 3 steps",
      how_sub: "No editing skills required. AI handles everything from start to finish.",
      step1_title: "Upload your raw video",
      step1_desc: "Drag and drop your mp4, mov, or webm file - up to 20 GB. Our secure chunked upload handles heavy files without a hitch.",
      step2_title: "AI analyzes and edits automatically",
      step2_desc: "Transcription, hook rewrite, silence removal, AI captions and graphics - done in minutes, not hours.",
      step3_title: "Download or publish directly",
      step3_desc: "Get your MP4 ready to post, or publish in one click to YouTube, TikTok, Instagram, and LinkedIn.",

      // ── FEATURES ─────────────────────────────────────────────────────────────
      feat_tag: "Features",
      feat_title: "Everything a creator needs",
      feat_sub: "One tool. Six features that replace an entire team.",
      feat1_desc: "AI rewrites the first 3 seconds of your video to maximize retention from the very first frame.",
      feat2_title: "Silence removal",
      feat2_desc: "Automatically detects and removes pauses, filler words, and silences that cause viewers to drop off.",
      feat3_title: "Auto captions",
      feat3_desc: "Word-by-word accurate subtitles in French, English, Spanish, and more.",
      feat4_title: "AI graphics",
      feat4_desc: "16 types of animated graphic cards - stats, timelines, comparisons, lists - generated and synced to your speech.",
      feat5_title: "Multi-format export",
      feat5_desc: "Download your video ready in vertical 9:16 (TikTok/Reels) and horizontal 16:9 (YouTube), without re-editing.",
      feat6_title: "6 visual styles",
      feat6_desc: "Choose from 6 visual identities, from premium dark to authentic handwritten, each with its own animations and transitions.",

      // ── PRICING ──────────────────────────────────────────────────────────────
      pricing_tag: "Pricing",
      pricing_title: "Simple. Transparent. No surprises.",
      pricing_sub: "Start for free. Scale when you need to.",
      pricing_per_month: "/ mo",
      pricing_popular: "POPULAR",
      plan_free_name: "Free trial",
      plan_free_desc: "Try with 1 video, no commitment.",
      plan_free_f1: "1 video (one-time)",
      plan_free_f2: "All visual styles",
      plan_free_f3: "AI captions",
      plan_free_btn: "Try for free",
      plan_starter_desc: "For the solo coach starting their video presence.",
      plan_f_6styles: "6 visual styles",
      plan_f_captions_hook: "Captions + Hook Rewriter",
      plan_starter_btn: "Choose Starter",
      plan_pro_desc: "For the established creator posting daily.",
      plan_f_graphics: "AI graphics",
      plan_f_priority_support: "Priority support",
      plan_pro_btn: "Choose Pro →",
      plan_agency_desc: "For agencies managing multiple creators.",
      plan_f_multi_accounts: "Multi-accounts",
      plan_agency_btn: "Contact the team →",

      // ── FAQ ───────────────────────────────────────────────────────────────────
      faq_tag: "FAQ",
      faq_title: "Frequently asked questions",
      faq_sub: "Everything you need to know before getting started.",
      faq1_q: "Are my videos stored on your servers?",
      faq1_answer: "Your videos are processed securely and automatically deleted after 24 hours. We never share your content with third parties.",

      // ── FINAL CTA ────────────────────────────────────────────────────────────
      cta_title: "Ready to stop spending<br>hours editing?",
      cta_sub: "First video free. No card required.",

      // ── FOOTER ───────────────────────────────────────────────────────────────
      footer_features: "Features",
      footer_pricing: "Pricing",
      footer_contact: "Contact",
      footer_copy: "© 2026 LeanRetention. All rights reserved.",

      // ── INDEX — SIDEBAR ───────────────────────────────────────────────────────
      tab_editor: "Editor",
      tab_profile: "Profile",

      // ── INDEX — NOTIFICATIONS ─────────────────────────────────────────────────
      notif_empty: "No notifications",
      notif_clear: "Clear all",

      // ── INDEX — LOGIN ─────────────────────────────────────────────────────────
      login_title: "Sign in required",
      login_sub: "Enter your password to access the editor.",
      login_pwd_ph: "Password",
      login_btn: "Sign in",

      // ── INDEX — ONBOARDING CARD ───────────────────────────────────────────────
      ob_card_title: "Quick start",
      ob_step1: "Create your coach profile",
      ob_step2: "Edit your 1st video",
      ob_step3: "Set up your brand identity",
      ob_step4: "Reach 5 edited videos",
      ob_step5: "Complete your ICP profile",

      // ── INDEX — DROP ZONE ─────────────────────────────────────────────────────
      drop_hint: "MP4, MOV, MKV - Up to 4 GB",

      // ── INDEX — FORMAT PILLS ──────────────────────────────────────────────────
      fmt_short: "Short · Reels",
      fmt_long: "Long · YouTube",

      // ── INDEX — STYLE PACK ────────────────────────────────────────────────────
      style_label: "Visual style",
      pack_glass_desc: "To appear as the undisputed authority in your field",
      pack_paper_desc: "To be taken seriously from the very first second",
      pack_vibe_desc: "To stop the scroll and blow up your reach",
      pack_ledger_desc: "To earn the trust of investors and premium clients",
      pack_craft_desc: "To make it feel like it's truly YOU speaking",
      pack_cinema_desc: "To turn your story into an unforgettable moment",

      // ── INDEX — EDITOR SUBMIT ─────────────────────────────────────────────────
      editor_submit: "Edit my video",

      // ── INDEX — RESULT ────────────────────────────────────────────────────────
      result_download: "Download",
      ctr_label: "CTR-optimized titles",
      details_more: "See more",
      caption_editor_label: "Caption editor",
      reburn_btn: "Re-burn captions",
      reburn_msg: "Captions updated!",
      chapters_label: "YouTube chapters",
      copy_chapters_btn: "Copy chapters",
      desc_gen_btn: "Generate descriptions",
      copy_btn: "Copy",
      publish_title: "Publish",
      publish_btn: "Publish to selected platforms",

      // ── INDEX — DASHBOARD ─────────────────────────────────────────────────────
      stat_videos: "Edited videos",
      stat_time: "Time saved",
      stat_month: "This month",
      dash_new_video: "New video",
      dash_lib_label: "Video library",
      lib_tab_active: "Library",
      lib_tab_trash: "Trash",
      lib_empty: "No edited videos yet.<br>Drop your first video in the editor.",

      // ── INDEX — PROFILE ───────────────────────────────────────────────────────
      profile_plan: "My plan",
      profile_icp: "Ideal client (ICP)",
      profile_pillars: "Content pillars",
      profile_icp_ph: "Describe your target audience...",
      profile_change_plan: "Change plan",
      profile_save_btn: "Save",
      profile_edit_btn: "Edit ICP & pillars",
      profile_saved_msg: "Profile saved."
    }
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
