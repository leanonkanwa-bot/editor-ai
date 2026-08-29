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
      hero_title1: "Monteur vidéo. Stratège rétention. Motion designer.",
      hero_glow: "Le tout en un : LeanRetention.",
      hero_sub: "Glisse ta vidéo brute. Reçois en moins de 10 minutes une version montée, capée, visualisée — prête à poster.",
      demo_context: "La même vidéo. Avant LeanRetention. Après.",
      demo_before_caption: "Vidéo brute · hésitations · silences non coupés",
      demo_after_caption: "Hook réécrit · silences coupés · graphics synchronisés",
      hero_cta1: "Éditer ma première vidéo →",
      hero_cta2: "Voir comment ça marche",
      trust_free: "Première vidéo gratuite",
      trust_no_card: "Sans carte bancaire",
      trust_time: "Résultats en 3 minutes",
      demo_before: "Avant",
      demo_after: "Après",

      // ── EMAIL CAPTURE ─────────────────────────────────────────────────────────
      email_title: "Obtiens ta première vidéo éditée gratuitement",
      email_sub: "Sans carte bancaire. Connexion en un clic.",
      google_btn: "Continuer avec Google",

      // ── HOW IT WORKS ─────────────────────────────────────────────────────────
      how_tag: "Comment ça marche",
      how_title: "De la vidéo brute au contenu viral<br>en 3 étapes",
      how_sub: "Aucune compétence en montage requise. L'IA gère tout de A à Z.",
      step1_title: "Upload ta vidéo brute",
      step1_desc: "Glisse-dépose jusqu'à 30 Go. L'upload tourne en arrière-plan — continue à travailler, on t'envoie un email quand c'est prêt.",
      step2_title: "L'IA analyse et édite automatiquement",
      step2_desc: "L’IA transcrit, réécrit le hook, coupe les silences, ajoute captions et graphics. Tout en 3 à 8 minutes — pendant que tu fais autre chose.",
      step3_title: "Téléchargez ou publiez directement",
      step3_desc: "Récupère ton MP4 prêt à poster. Vertical pour TikTok, horizontal pour YouTube — les deux disponibles sans remonter.",

      // ── FEATURES ─────────────────────────────────────────────────────────────
      feat_tag: "Fonctionnalités",
      feat_title: "Tout ce dont un créateur a besoin",
      feat_sub: "Un seul outil. Tout ce que tu aurais demandé à ton monteur.",
      feat1_desc: "70% des spectateurs décident dans les 3 premières secondes. L'IA optimise ton hook pour que les bonnes personnes restent — automatiquement.",
      feat2_title: "Suppression des silences",
      feat2_desc: "Les silences, hésitations et tics de langage disparaissent sans que tu touches la timeline. Ce qui prenait 2h de montage manuel prend maintenant moins d’une minute.",
      feat3_title: "Captions automatiques",
      feat3_desc: "85% des vidéos mobiles sont regardées sans son. Des captions précises mot par mot, en 50+ langues — pour garder l’attention même en silence.",
      feat4_title: "Graphics IA",
      feat4_desc: "Chaque argument fort devient une carte visuelle qui force le viewer à rester. Stats, timelines, listes : générées et synchronisées avec ton discours, automatiquement.",
      feat5_title: "Export multi-formats",
      feat5_desc: "Une seule vidéo traitée, deux formats prêts. 9:16 pour TikTok et Reels, 16:9 pour YouTube — sans remonter, sans recadrer.",
      feat6_title: "6 styles visuels",
      feat6_desc: "Ton identité visuelle cohérente sur chaque vidéo, sans brief créatif, sans designer. 6 univers avec leurs propres animations et transitions.",

      feat7_title: "Export 4K",
      feat7_desc: "YouTube and TikTok favor high-resolution videos in their algorithm. Export in native 4K - let the algorithm do the rest.",
      pricing_anchor: "A freelance video editor charges between $50 and $200 per hour. LeanRetention costs less than a single session - and edits your video in 8 minutes.",
      founder_quote: "I built LeanRetention because I was looking for something that didn't exist: a tool that actually understands video retention, not just editing. I still use it every week for my own videos.",
      founder_name: "KAN - Founder of LeanRetention",
      founder_role: "Content creator · Retention strategist",
      // ── PRICING ──────────────────────────────────────────────────────────────
      pricing_tag: "Tarifs",
      pricing_title: "Simple. Transparent. Sans surprise.",
      pricing_sub: "Commence gratuitement. Évolue quand tu en as besoin.",
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
      faq_sub: "Tout ce que tu dois savoir avant de commencer.",
      faq1_q: "Mes vidéos sont-elles en sécurité ?",
      faq1_answer: "Tes vidéos sont hébergées sur des serveurs sécurisés, supprimées après traitement et jamais partagées avec des tiers. Nous n'utilisons jamais ton contenu pour entraîner des modèles d'IA.",
      faq2_q: "Quelles langues sont supportées ?",
      faq2_answer: "Nous utilisons Whisper d'OpenAI, qui supporte plus de 50 langues. Les captions, le découpage et la synchronisation fonctionnent pour toutes les langues détectées automatiquement.",
      faq3_q: "Combien de temps prend le montage ?",
      faq3_answer: "En général entre 3 et 8 minutes selon la durée de ta vidéo. Tu reçois une notification par email dès que c'est prêt — tu n'as pas besoin de rester sur la page.",
      faq4_q: "Puis-je modifier le résultat ?",
      faq4_answer: "Oui. Tu peux télécharger la vidéo en MP4 et la retravailler dans l'éditeur de ton choix : CapCut, Premiere Pro, DaVinci Resolve, Final Cut... LeanRetention te donne une base montée, pas un produit fini imposé.",
      faq5_q: "Mon abonnement se renouvelle quand ?",
      faq5_answer: "Le renouvellement a lieu le même jour chaque mois. Tu peux annuler à tout moment depuis ton profil — ton accès reste actif jusqu'à la fin de la période en cours.",

      // ── FINAL CTA ────────────────────────────────────────────────────────────
      cta_title: "Le monteur que tu n'as jamais pu te payer.<br>Maintenant tu peux.",
      cta_sub: "Poste ce soir. Pas dans 3 jours.",

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

      // ── INDEX — ONBOARDING CARD ───────────────────────────────────────────────
      ob_card_title: "Démarrage rapide",
      ob_step1: "Créer votre profil coach",
      ob_step2: "Éditer votre 1ère vidéo",
      ob_step3: "Configurer votre identité de marque",
      ob_step4: "Atteindre 5 vidéos éditées",
      ob_step5: "Compléter votre profil ICP",

      // ── INDEX — DROP ZONE ─────────────────────────────────────────────────────
      drop_hint: "MP4, MOV, MKV · Jusqu'à 30 Go",

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

      // ── INDEX — QUALITY SELECTOR ──────────────────────────────────────────────
      quality_4k_label:  "4K",
      quality_4k_note:   "Le rendu 4K prend 2–3 min supplémentaires.",
      quality_4k_locked: "4K disponible sur Starter et plus.",

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
      usage_videos: "vidéos",
      usage_period_monthly: "ce mois",
      usage_period_trial: "(essai)",

      // ── INDEX — QUOTA NOTICE (lifetime trial exhausted) ──────────────────────
      quota_trial_used: "Tu as utilisé ta vidéo d'essai.",
      quota_upgrade_cta: "Passe à Starter",
      quota_trial_continue: "pour continuer, 15 vidéos par mois et l'export 4K.",

      // ── INDEX — PROFILE ───────────────────────────────────────────────────────
      profile_plan: "Mon plan",
      profile_icp: "Client idéal (ICP)",
      profile_pillars: "Piliers de contenu",
      profile_icp_ph: "Décrivez votre audience cible…",
      profile_change_plan: "Changer de plan",
      profile_save_btn: "Enregistrer",
      profile_edit_btn: "Modifier ICP & piliers",
      profile_saved_msg: "Profil enregistré.",

      // ── INDEX — LEANBRIEF ─────────────────────────────────────────────────────
      lb_card_label: "Cours : les bases du montage qui retient",
      lb1_title: "La structure qui retient l'attention",
      lb1_body: "Une vidéo qui retient suit toujours la même architecture, dans cet ordre : <strong>Hook</strong> (0–3s, la promesse ou le choc qui arrête le scroll) → <strong>Contraste</strong> (ce qui rend la situation intéressante) → <strong>Conséquence</strong> (l'enjeu réel) → <strong>Loop</strong> (une question ouverte qui pousse à continuer) → <strong>Histoire</strong> → <strong>Réalisation</strong> → <strong>Principe</strong> → <strong>Reframe</strong> → <strong>Payoff</strong> → <strong>Closing</strong>. Sauter une étape, c'est perdre un point d'accroche.",
      lb2_title: "Pourquoi on coupe les pauses et les répétitions",
      lb2_body: "Une pause de plus de 0,3–0,5s sans intention fait perdre du rythme et invite au scroll. Une répétition ralentit la compréhension sans ajouter de sens. La règle : on coupe ce qui n'ajoute ni clarté ni émotion, on garde tout ce qui en ajoute.",
      lb3_title: "Le zoom et le rythme",
      lb3_body: "Le zoom lent avec punch-in ponctuel (100 % → 130 %) crée un mouvement continu qui empêche l'œil de se lasser. Trop de zoom = fatigue visuelle. Pas assez = statique. La cadence idéale suit les moments d'emphase du discours.",
      lb4_title: "Le B-roll au service du propos",
      lb4_body: "Un B-roll ne doit apparaître que quand il illustre vraiment ce qui est dit à l'instant précis, jamais pour remplir un silence. Trop de B-roll noie le message ; pas assez, le discours reste abstrait.",
      lb5_title: "Construire une marque personnelle reconnaissable",
      lb5_body: "Un spectateur qui voit 3 de tes vidéos doit pouvoir reconnaître ton style avant même de lire ton nom. La marque personnelle en vidéo, ce n'est pas ton logo ou ta couleur : c'est ta façon de te positionner dans le cadre, ton rythme de parole, tes expressions récurrentes, et le ton émotionnel que tu maintiens d'une vidéo à l'autre. Plus tu es cohérent, plus l'algorithme et ton audience t'associent à un territoire précis. Choisir un territoire, c'est renoncer à tout le reste ; et c'est exactement ce qui crée de la reconnaissance.",
      lb6_title: "Garder le même pack visuel",
      lb6_body: "Changer de style à chaque vidéo, c'est recommencer à zéro à chaque fois dans l'esprit du spectateur. Un pack visuel cohérent (même police, même couleur d'accent, même ambiance) crée une signature visuelle que ton audience reconnaît en 0,3 seconde au scroll, avant même d'entendre ta voix. LeanRetention te propose 6 packs, chacun avec une identité précise. Choisis celui qui correspond à ton positionnement, garde-le sur la durée, et laisse le contenu changer, pas l'habillage."
    },

    en: {
      // ── NAV (landing) ────────────────────────────────────────────────────────
      nav_login: "Sign in",
      nav_cta: "Start for free",

      // ── HERO ─────────────────────────────────────────────────────────────────
      hero_title1: "Video editor. Retention strategist. Motion designer.",
      hero_glow: "All in one: LeanRetention.",
      hero_sub: "Drop your raw footage. Get a fully edited, captioned, visualized video - ready to post in under 10 minutes.",
      hero_cta1: "Edit my first video →",
      hero_cta2: "See how it works",
      trust_free: "First video free",
      trust_no_card: "No credit card required",
      trust_time: "Results in 3 minutes",
      demo_before: "Before",
      demo_after: "After",
      demo_context: "The same video. Before LeanRetention. After.",
      demo_before_caption: "Raw footage - hesitations - uncut silences",
      demo_after_caption: "Hook rewritten - silences removed - graphics synced",

      // ── EMAIL CAPTURE ─────────────────────────────────────────────────────────
      email_title: "Get your first video edited for free",
      email_sub: "No credit card. One-click sign in.",
      google_btn: "Continue with Google",

      // ── HOW IT WORKS ─────────────────────────────────────────────────────────
      how_tag: "How it works",
      how_title: "From raw footage to viral content<br>in 3 steps",
      how_sub: "No editing skills required. AI handles everything from start to finish.",
      step1_title: "Upload your raw video",
      step1_desc: "Drop your file - up to 30 GB. The upload runs in the background while you keep working. You get an email the moment your video is ready.",
      step2_title: "AI analyzes and edits automatically",
      step2_desc: "AI transcribes, rewrites the hook, cuts silences, adds captions and graphics. Everything in 3 to 8 minutes - while you do something else.",
      step3_title: "Download or publish directly",
      step3_desc: "Download your MP4 ready to post. Vertical for TikTok, horizontal for YouTube - both available without re-editing.",

      // ── FEATURES ─────────────────────────────────────────────────────────────
      feat_tag: "Features",
      feat_title: "Everything a creator needs",
      feat_sub: "One tool. Everything you'd ask your editor to do.",
      feat1_desc: "70% of viewers decide in the first 3 seconds. AI optimizes your hook so the right people stay - automatically.",
      feat2_title: "Silence removal",
      feat2_desc: "Silences, filler words, and hesitations disappear without touching the timeline. What used to take 2 hours of editing now takes less than a minute.",
      feat3_title: "Auto captions",
      feat3_desc: "85% of mobile videos are watched without sound. Word-by-word accurate captions in 50+ languages - to keep attention even on mute.",
      feat4_title: "AI graphics",
      feat4_desc: "Every strong argument becomes a visual card that forces viewers to stay. Stats, timelines, lists - generated and synced to your speech, automatically.",
      feat5_title: "Multi-format export",
      feat5_desc: "One video processed, two formats ready. 9:16 for TikTok and Reels, 16:9 for YouTube - no re-editing, no recropping.",
      feat6_title: "6 visual styles",
      feat6_desc: "Your consistent visual identity on every video - no creative brief, no designer. 6 styles with their own animations and transitions.",

      feat7_title: "Export 4K",
      feat7_desc: "YouTube and TikTok favor high-resolution videos in their algorithm. Export in native 4K - let the algorithm do the rest.",
      pricing_anchor: "A freelance video editor charges between $50 and $200 per hour. LeanRetention costs less than a single session - and edits your video in 8 minutes.",
      founder_quote: "I built LeanRetention because I was looking for something that didn't exist: a tool that actually understands video retention, not just editing. I still use it every week for my own videos.",
      founder_name: "KAN - Founder of LeanRetention",
      founder_role: "Content creator · Retention strategist",
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
      faq1_q: "Are my videos safe?",
      faq1_answer: "Your videos are stored on secure servers, deleted after processing, and never shared with third parties. We never use your content to train AI models.",
      faq2_q: "Which languages are supported?",
      faq2_answer: "We use OpenAI's Whisper, which supports over 50 languages. Captions, cuts, and synced graphics work for all languages detected automatically.",
      faq3_q: "How long does editing take?",
      faq3_answer: "Usually between 3 and 8 minutes depending on your video length. You get an email notification as soon as it is ready so you do not have to stay on the page.",
      faq4_q: "Can I edit the result?",
      faq4_answer: "Yes. You can download the video as MP4 and refine it in any editor you like: CapCut, Premiere Pro, DaVinci Resolve, Final Cut... EDITOR AI gives you a solid starting cut, not a locked final product.",
      faq5_q: "When does my subscription renew?",
      faq5_answer: "Your subscription renews on the same date each month. You can cancel at any time from your profile - your access stays active until the end of the current period.",

      // ── FINAL CTA ────────────────────────────────────────────────────────────
      cta_title: "The editor you never could afford.<br>Now you can.",
      cta_sub: "Post tonight. Not in 3 days.",

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

      // ── INDEX — ONBOARDING CARD ───────────────────────────────────────────────
      ob_card_title: "Quick start",
      ob_step1: "Create your coach profile",
      ob_step2: "Edit your 1st video",
      ob_step3: "Set up your brand identity",
      ob_step4: "Reach 5 edited videos",
      ob_step5: "Complete your ICP profile",

      // ── INDEX — DROP ZONE ─────────────────────────────────────────────────────
      drop_hint: "MP4, MOV, MKV · Up to 30 GB",

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

      // ── INDEX — QUALITY SELECTOR ──────────────────────────────────────────────
      quality_4k_label:  "4K",
      quality_4k_note:   "4K rendering takes 2–3 extra minutes.",
      quality_4k_locked: "4K available on Starter and above.",

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
      usage_videos: "videos",
      usage_period_monthly: "this month",
      usage_period_trial: "(trial)",

      // ── INDEX — QUOTA NOTICE (lifetime trial exhausted) ──────────────────────
      quota_trial_used: "You have used your free trial video.",
      quota_upgrade_cta: "Upgrade to Starter",
      quota_trial_continue: "to continue, 15 videos per month and 4K export.",

      // ── INDEX — PROFILE ───────────────────────────────────────────────────────
      profile_plan: "My plan",
      profile_icp: "Ideal client (ICP)",
      profile_pillars: "Content pillars",
      profile_icp_ph: "Describe your target audience...",
      profile_change_plan: "Change plan",
      profile_save_btn: "Save",
      profile_edit_btn: "Edit ICP & pillars",
      profile_saved_msg: "Profile saved.",

      // ── INDEX — LEANBRIEF ─────────────────────────────────────────────────────
      lb_card_label: "Course: the foundations of retention editing",
      lb1_title: "The Structure That Holds Attention",
      lb1_body: "A video that retains viewers always follows the same architecture, in this order: <strong>Hook</strong> (0–3s, the promise or shock that stops the scroll) → <strong>Contrast</strong> (what makes the situation interesting) → <strong>Consequence</strong> (the real stakes) → <strong>Loop</strong> (an open question that pushes viewers to keep watching) → <strong>Story</strong> → <strong>Realization</strong> → <strong>Principle</strong> → <strong>Reframe</strong> → <strong>Payoff</strong> → <strong>Closing</strong>. Skip a step and you lose a retention anchor.",
      lb2_title: "Why We Cut Pauses and Repetitions",
      lb2_body: "A pause longer than 0.3–0.5s without intent kills the rhythm and invites a swipe. A repetition slows comprehension without adding meaning. The rule: cut what adds neither clarity nor emotion, keep everything that does.",
      lb3_title: "Zoom and Pacing",
      lb3_body: "A slow zoom with a periodic punch-in (100% to 130%) creates continuous movement that keeps the eye engaged. Too much zoom causes visual fatigue. Too little feels static. The ideal cadence follows the emphasis points of the speech.",
      lb4_title: "B-Roll in Service of the Message",
      lb4_body: "B-roll should only appear when it genuinely illustrates what is being said at that exact moment, never to fill a silence. Too much B-roll drowns the message; too little leaves the content abstract.",
      lb5_title: "Building a Recognizable Personal Brand",
      lb5_body: "A viewer who watches three of your videos should recognize your style before even reading your name. Your personal brand in video is not your logo or your color: it is how you position yourself in the frame, your speaking rhythm, your recurring expressions, and the emotional tone you maintain from one video to the next. The more consistent you are, the more the algorithm and your audience associate you with a specific niche. Choosing a niche means letting go of everything else, and that is exactly what creates recognition.",
      lb6_title: "Sticking to the Same Visual Pack",
      lb6_body: "Changing style with every video means starting from scratch in the viewer's mind each time. A consistent visual pack (same font, same accent color, same atmosphere) creates a visual signature your audience recognizes in 0.3 seconds while scrolling, before they even hear your voice. LeanRetention offers 6 packs, each with a distinct identity. Choose the one that fits your positioning, stick with it over time, and let the content change, not the look."
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
