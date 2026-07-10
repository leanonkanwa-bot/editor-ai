export type Lang = "fr" | "en";

export const translations = {
  fr: {
    // Nav
    nav_how: "Comment ça marche",
    nav_features: "Fonctionnalités",
    nav_pricing: "Tarifs",
    nav_login: "Connexion",
    nav_cta: "Commencer →",

    // Hero
    hero_badge: "Propulsé par l'IA Claude d'Anthropic",
    hero_title_1: "Arrête de perdre 2 heures",
    hero_title_2: "à monter chaque short.",
    hero_subtitle: "LeanRetention analyse ta vidéo, réécrit ton hook, coupe les silences, ajoute captions et graphics — et te dit comment faire mieux la prochaine fois. Prêt à poster en 3 minutes.",
    hero_diff: "Le seul éditeur qui te montre pourquoi chaque coupe augmente ta rétention.",
    hero_cta_primary: "Commencer gratuitement →",
    hero_cta_secondary: "Voir un avant/après",
    hero_no_card: "Sans carte bancaire · Configuration 2 min · Annulable à tout moment",

    // Before/after
    before_label: "Vidéo brute",
    after_label: "Montée par LeanRetention",
    ba_tag_before: "Avant",
    ba_tag_after: "Après",
    ba_drag_hint: "glisser",
    ba_raw_hook: "Donc euh... aujourd'hui je vais vous parler de, hm, la rétention sur TikTok. Alors voilà c'est un sujet qui, bah, qui est important...",
    ba_raw_tag: "❌ Hook faible — 63% drop à 3s",
    ba_edited_hook: "63% de tes viewers partent dans les 3 premières secondes. Voici pourquoi — et comment l'arrêter.",
    ba_edited_tag_1: "✓ Hook réécrit par IA",
    ba_edited_tag_2: "+47% rétention moy.",

    // Social proof
    social_proof_count: "500+ créateurs",
    social_proof_label: "utilisent déjà LeanRetention",

    // Stats
    stat_1_v: "500+",
    stat_1_l: "Créateurs actifs",
    stat_2_v: "3 min",
    stat_2_l: "Temps de montage moyen",
    stat_3_v: "+47%",
    stat_3_l: "Rétention moyenne",
    stat_4_v: "4.9★",
    stat_4_l: "Note moyenne",

    // HowItWorks
    how_eyebrow: "Comment ça marche",
    how_title_1: "De la vidéo brute au short qui retient,",
    how_title_2: "en 3 étapes.",
    how_subtitle: "Configuration en 2 minutes. Premier montage prêt à poster en 3 minutes.",
    step_1_n: "01", step_1_icon: "✦",
    step_1_title: "Dépose ta vidéo brute",
    step_1_desc: "MP4, MOV, peu importe. LeanRetention transcrit, analyse le rythme et identifie les moments forts en 30 secondes.",
    step_1_detail: "Whisper AI pour la transcription, détection automatique des fillers et silences.",
    step_2_n: "02", step_2_icon: "◎",
    step_2_title: "L'IA monte, réécrit et décore",
    step_2_desc: "Coupe les silences, réécrit le hook pour maximiser la rétention, ajoute captions animées et graphics percutants.",
    step_2_detail: "Claude AI pour le hook rewrite, captions synchronisées mot à mot, graphic overlays dynamiques.",
    step_3_n: "03", step_3_icon: "→",
    step_3_title: "Exporte et reçois ton débriefing coach",
    step_3_desc: "Ton short est prêt en 9:16 ou 16:9. Et ton coach de rétention t'explique chaque décision de montage.",
    step_3_detail: "Export direct pour TikTok, Reels, YouTube Shorts — analyse de rétention incluse.",

    // Features
    feat_eyebrow: "Fonctionnalités",
    feat_title: "Tout pour créer des shorts\nqui retiennent.",
    feat_subtitle: "Une seule plateforme, de l'upload au post.",
    feat_1_title: "Transcription automatique",
    feat_1_desc: "Whisper AI transcrit ta vidéo en 30 secondes. Timestamps mot à mot, détection de langue, segmentation intelligente.",
    feat_2_title: "Hook Rewrite IA",
    feat_2_desc: "Claude réécrit ton introduction pour maximiser la rétention dans les 3 premières secondes — le point de bascule de toute vidéo courte.",
    feat_3_title: "Montage automatique",
    feat_3_desc: "Fillers, silences, hésitations — automatiquement détectés et coupés. Transitions fluides, rythme cinématique.",
    feat_4_title: "Captions cinématiques",
    feat_4_desc: "Captions animées synchronisées mot à mot, en 9:16 et 16:9. 30+ styles visuels, de l'épuré au dramatique.",
    feat_5_title: "Graphics dynamiques",
    feat_5_desc: "Cartes stats, timelines, checklists, scores — générées automatiquement depuis ta transcription et superposées au bon moment.",
    feat_6_title: "Coach de rétention",
    feat_6_desc: "Après chaque montage, ton coach IA t'explique chaque décision, analyse tes performances et te suggère tes prochains angles.",

    // Coach section
    coach_eyebrow: "TON COACH, PAS JUSTE TON MONTEUR",
    coach_title_1: "Il ne monte pas seulement.",
    coach_title_2: "Il t'apprend à percer.",
    coach_1_title: "Il t'explique chaque décision.",
    coach_1_desc: "Après chaque montage, il te dit ce qu'il a coupé et pourquoi.",
    coach_2_title: "Il lit tes performances.",
    coach_2_desc: "Envoie une capture de ta rétention, il repère où ton audience décroche et te dit quoi changer.",
    coach_3_title: "Il te dit quoi poster ensuite.",
    coach_3_desc: "À partir de tes meilleures vidéos, il te suggère tes prochains sujets, hooks et angles.",

    // Pricing
    pricing_eyebrow: "Tarifs",
    pricing_title: "Simple. Transparent.\nRentable dès le premier short.",
    pricing_subtitle: "Commence gratuitement. Upgrade quand tu postes.",
    pricing_footer: "Tous les plans · SSL · Données sécurisées · Annulable à tout moment",
    plan_popular: "Le plus populaire",
    plan_per_month: "/mois",

    plan_free_name: "Gratuit",
    plan_free_price: "0€",
    plan_free_sub: "Pour tester sans engagement.",
    plan_free_cta: "Commencer gratuitement",
    plan_free_f1: "5 montages / mois",
    plan_free_f2: "Transcription automatique",
    plan_free_f3: "Captions animées",
    plan_free_f4: "Export 9:16 et 16:9",

    plan_pro_name: "Pro",
    plan_pro_price: "79€",
    plan_pro_sub: "Pour les créateurs qui veulent percer.",
    plan_pro_cta: "Démarrer le plan Pro",
    plan_pro_f1: "Montages illimités",
    plan_pro_f2: "Hook Rewrite IA (Claude)",
    plan_pro_f3: "Captions + 30 styles visuels",
    plan_pro_f4: "Graphics dynamiques auto",
    plan_pro_f5: "Coach de rétention + analyse de perfs",
    plan_pro_f6: "Support prioritaire",

    plan_agency_name: "Agency",
    plan_agency_price: "199€",
    plan_agency_sub: "Pour les agences et multi-créateurs.",
    plan_agency_cta: "Démarrer le plan Agency",
    plan_agency_f1: "Tout le plan Pro",
    plan_agency_f2: "5 comptes créateurs inclus",
    plan_agency_f3: "Accès API",
    plan_agency_f4: "Branding personnalisé",
    plan_agency_f5: "Support Slack dédié",

    // Final CTA
    cta_eyebrow: "Prêt à scaler ?",
    cta_title_1: "Arrête de monter.",
    cta_title_2: "Commence à percer.",
    cta_subtitle: "Rejoins 500+ créateurs qui postent mieux en 3 minutes chrono — sans passer leur journée sur CapCut.",
    cta_button: "Commencer gratuitement →",
    cta_micro: "Sans carte bancaire · Configuration en 2 minutes",

    // Testimonials
    testi_eyebrow: "Témoignages",
    testi_title_1: "De vrais créateurs.",
    testi_title_2: "De vrais résultats.",

    // Footer
    footer_tagline: "Conçu pour les créateurs qui veulent percer.",
  },
  en: {
    // Nav
    nav_how: "How it works",
    nav_features: "Features",
    nav_pricing: "Pricing",
    nav_login: "Sign in",
    nav_cta: "Get started →",

    // Hero
    hero_badge: "Powered by Anthropic's Claude AI",
    hero_title_1: "Stop spending 2 hours",
    hero_title_2: "editing every short.",
    hero_subtitle: "LeanRetention analyzes your video, rewrites your hook, cuts the dead air, adds captions and graphics — then tells you how to do better next time. Ready to post in 3 minutes.",
    hero_diff: "The only editor that shows you why every cut boosts your retention.",
    hero_cta_primary: "Start for free →",
    hero_cta_secondary: "See a before/after",
    hero_no_card: "No credit card · 2-min setup · Cancel anytime",

    // Before/after
    before_label: "Raw footage",
    after_label: "Edited by LeanRetention",
    ba_tag_before: "Before",
    ba_tag_after: "After",
    ba_drag_hint: "drag",
    ba_raw_hook: "So uh... today I'm going to talk about, hmm, retention on TikTok. So like that's a topic that, you know, is pretty important...",
    ba_raw_tag: "❌ Weak hook — 63% drop at 3s",
    ba_edited_hook: "63% of your viewers leave in the first 3 seconds. Here's why — and how to stop it.",
    ba_edited_tag_1: "✓ AI hook rewrite",
    ba_edited_tag_2: "+47% avg retention",

    // Social proof
    social_proof_count: "500+ creators",
    social_proof_label: "already use LeanRetention",

    // Stats
    stat_1_v: "500+",
    stat_1_l: "Active creators",
    stat_2_v: "3 min",
    stat_2_l: "Average edit time",
    stat_3_v: "+47%",
    stat_3_l: "Average retention boost",
    stat_4_v: "4.9★",
    stat_4_l: "Average rating",

    // HowItWorks
    how_eyebrow: "How it works",
    how_title_1: "From raw footage to a short that holds attention,",
    how_title_2: "in 3 steps.",
    how_subtitle: "2-minute setup. First edit ready to post in 3 minutes.",
    step_1_n: "01", step_1_icon: "✦",
    step_1_title: "Drop your raw footage",
    step_1_desc: "MP4, MOV, anything goes. LeanRetention transcribes, analyzes the pacing, and spots the best moments in 30 seconds.",
    step_1_detail: "Whisper AI for transcription, automatic filler and silence detection.",
    step_2_n: "02", step_2_icon: "◎",
    step_2_title: "AI edits, rewrites, and decorates",
    step_2_desc: "Cuts silence, rewrites your hook for maximum retention, adds animated captions and bold graphics — automatically.",
    step_2_detail: "Claude AI for hook rewriting, word-synced captions, dynamic graphic overlays.",
    step_3_n: "03", step_3_icon: "→",
    step_3_title: "Export and get your coach debrief",
    step_3_desc: "Your short is ready in 9:16 or 16:9. And your retention coach explains every edit decision.",
    step_3_detail: "Direct export for TikTok, Reels, YouTube Shorts — with retention analysis included.",

    // Features
    feat_eyebrow: "Features",
    feat_title: "Everything to create shorts\nthat hold attention.",
    feat_subtitle: "One platform, from upload to post.",
    feat_1_title: "Automatic transcription",
    feat_1_desc: "Whisper AI transcribes your video in 30 seconds. Word-level timestamps, language detection, smart segmentation.",
    feat_2_title: "AI Hook Rewrite",
    feat_2_desc: "Claude rewrites your intro to maximize retention in the first 3 seconds — the make-or-break moment of every short.",
    feat_3_title: "Automatic editing",
    feat_3_desc: "Fillers, silence, hesitations — automatically detected and cut. Smooth transitions, cinematic pacing.",
    feat_4_title: "Cinematic captions",
    feat_4_desc: "Animated captions synced word by word, in 9:16 and 16:9. 30+ visual styles, from minimal to dramatic.",
    feat_5_title: "Dynamic graphics",
    feat_5_desc: "Stat cards, timelines, checklists, scores — auto-generated from your transcript and overlaid at the right moment.",
    feat_6_title: "Retention coach",
    feat_6_desc: "After every edit, your AI coach explains each decision, analyzes your performance, and suggests your next content angles.",

    // Coach section
    coach_eyebrow: "YOUR COACH, NOT JUST YOUR EDITOR",
    coach_title_1: "It doesn't just edit.",
    coach_title_2: "It teaches you to grow.",
    coach_1_title: "It explains every call.",
    coach_1_desc: "After each edit, it tells you what it cut and why.",
    coach_2_title: "It reads your performance.",
    coach_2_desc: "Drop a screenshot of your retention graph, it spots where viewers drop off and tells you what to fix.",
    coach_3_title: "It tells you what to post next.",
    coach_3_desc: "From your best videos, it suggests your next topics, hooks and angles.",

    // Pricing
    pricing_eyebrow: "Pricing",
    pricing_title: "Simple. Transparent.\nProfitable from your first short.",
    pricing_subtitle: "Start free. Upgrade when you post.",
    pricing_footer: "All plans · SSL · Secure data · Cancel anytime",
    plan_popular: "Most popular",
    plan_per_month: "/mo",

    plan_free_name: "Free",
    plan_free_price: "$0",
    plan_free_sub: "Try it with no commitment.",
    plan_free_cta: "Start for free",
    plan_free_f1: "5 edits / month",
    plan_free_f2: "Automatic transcription",
    plan_free_f3: "Animated captions",
    plan_free_f4: "9:16 and 16:9 export",

    plan_pro_name: "Pro",
    plan_pro_price: "$79",
    plan_pro_sub: "For creators who want to break through.",
    plan_pro_cta: "Start Pro plan",
    plan_pro_f1: "Unlimited edits",
    plan_pro_f2: "AI Hook Rewrite (Claude)",
    plan_pro_f3: "Captions + 30 visual styles",
    plan_pro_f4: "Auto dynamic graphics",
    plan_pro_f5: "Retention coach + performance analysis",
    plan_pro_f6: "Priority support",

    plan_agency_name: "Agency",
    plan_agency_price: "$199",
    plan_agency_sub: "For agencies and multi-creators.",
    plan_agency_cta: "Start Agency plan",
    plan_agency_f1: "Everything in Pro",
    plan_agency_f2: "5 creator accounts included",
    plan_agency_f3: "API access",
    plan_agency_f4: "Custom branding",
    plan_agency_f5: "Dedicated Slack support",

    // Final CTA
    cta_eyebrow: "Ready to scale?",
    cta_title_1: "Stop editing.",
    cta_title_2: "Start breaking through.",
    cta_subtitle: "Join 500+ creators who post better in 3 minutes flat — without spending their day on CapCut.",
    cta_button: "Start for free →",
    cta_micro: "No credit card · 2-minute setup",

    // Testimonials
    testi_eyebrow: "Testimonials",
    testi_title_1: "Real creators.",
    testi_title_2: "Real results.",

    // Footer
    footer_tagline: "Built for creators who want to break through.",
  },
} as const;

export type TranslationKey = keyof typeof translations["fr"];

export function t(lang: Lang, key: TranslationKey): string {
  return translations[lang][key] as string;
}
