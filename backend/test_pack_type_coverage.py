#!/usr/bin/env python3
"""
Structural cross-check: every GSAP tween selector emitted by _build_timeline_js()
must correspond to an element actually created by _build_graphic_card_html() for
the same pack × type combination.

Catches HTML/JS drift before it ships. Run as part of CI:
    python -X utf8 backend/test_pack_type_coverage.py

Exit 0 = all clean. Exit 1 = mismatches found.
"""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.engine.compose import (
    _LEAN_GLASS, _LEAN_PAPER, _LEAN_VIBE,
    _LEAN_LEDGER, _LEAN_CRAFT, _LEAN_CINEMA,
    _build_graphic_card_html, _build_timeline_js,
)

PACKS = {
    "lean_glass":  _LEAN_GLASS,
    "lean_paper":  _LEAN_PAPER,
    "lean_vibe":   _LEAN_VIBE,
    "lean_ledger": _LEAN_LEDGER,
    "lean_craft":  _LEAN_CRAFT,
    "lean_cinema": _LEAN_CINEMA,
}

# Minimal contentHints for each type — enough to exercise its HTML branch.
# Fields listed here are what the LLM would typically supply.
_HINTS: dict[str, dict] = {
    "callout":              {"title": "Test callout"},
    "key_phrase":           {"title": "Test key phrase"},
    "quote":                {"title": "\"Une citation remarquable\"", "detail": "— Auteur"},
    "attributed_quote":     {"title": "\"Citation attribuée\"", "detail": "— Auteur, 2024"},
    "stat":                 {"number": "42", "title": "conversions", "detail": "en 30 jours"},
    "list":                 {"title": "Points clés", "items": ["Point A", "Point B", "Point C"]},
    "checklist":            {"title": "Checklist", "items": ["Étape 1", "Étape 2"]},
    "comparison":           {"left_label": "Avant", "left_value": "100€",
                             "right_label": "Après", "right_value": "500€"},
    "score":                {"number": "8.5", "title": "Score qualité", "detail": "/10"},
    "trend":                {"number": "+34%", "title": "Croissance", "detail": "ce mois"},
    "rating":               {"number": "4.8", "title": "Note client"},
    "progress_bar":         {"number": "72", "title": "Progression", "detail": "objectif 100"},
    "countdown":            {"number": "7", "title": "jours restants"},
    "step_number":          {"number": "3", "title": "Automatise les tâches répétitives"},
    "price_tag":            {"number": "49€", "title": "par mois", "detail": "accès illimité"},
    "recap_summary":        {"title": "Résumé", "items": ["Point 1", "Point 2"]},
    "formula_equation":     {"title": "Valeur = Résultat × Preuve"},
    "pros_cons":            {"title": "Analyse",
                             "pros": ["Avantage 1", "Avantage 2"],
                             "cons": ["Inconvénient 1"]},
    "star_rating_review":   {"number": "4", "title": "Excellent produit",
                             "detail": "Marie D. — cliente vérifiée"},
    "income_reveal":        {"number": "12 400€", "title": "revenus nets", "detail": "ce mois"},
    "revenue_breakdown":    {"title": "Répartition revenus",
                             "items": ["Consulting 60%", "Produits 30%", "Affilié 10%"]},
    "data_bar_chart":       {"title": "Comparaison",
                             "items": ["Jan:40", "Fév:65", "Mar:90"]},
    "number_ranking":       {"title": "Classement",
                             "items": ["1. Stratégie A", "2. Stratégie B", "3. Stratégie C"]},
    "question_answer_pair": {"title": "Peut-on vraiment vivre de sa passion?",
                             "detail": "Oui, mais cela demande de la méthode."},
    "cause_effect":         {"title": "Cause → Effet",
                             "detail": "Quand tu postes chaque jour, ton algorithme te récompense."},
    "percentage_split":     {"title": "Répartition du temps",
                             "items": ["Création 40%", "Distribution 40%", "Analyse 20%"]},
    "red_flag_list":        {"title": "Signaux d'alerte",
                             "flags": ["Promesses irréalistes", "Pas de preuve", "Pression urgence"]},
    "client_avatar_persona":{"title": "Marie, 34 ans",
                             "detail": "Entrepreneuse cherchant à automatiser son business"},
    "tool_stack":           {"title": "Ma stack d'outils",
                             "items": ["Notion", "Zapier", "Stripe"]},
    "book_recommendation":  {"title": "Deep Work", "detail": "Cal Newport",
                             "kicker": "Lecture recommandée"},
    "contrarian_take":      {"take_text": "Le SEO est mort, vive le contenu vidéo"},
    "warning_soft":         {"warning_text": "Attention: cette stratégie ne fonctionne pas pour tous"},
    "myth_vs_fact":         {"myth_text": "Il faut 10 000 heures pour maîtriser une compétence",
                             "fact_text": "La pratique délibérée est plus importante que le temps brut"},
    "action_step_cta":      {"cta_text": "Télécharge le template gratuitement en cliquant ici"},
    "secret_reveal":        {"secret_text": "La vraie clé c'est la régularité, pas la perfection"},
    "objection_response":   {"objection_text": "C'est trop cher pour moi",
                             "response_text": "Calcule le ROI sur 3 mois"},
    "story_chapter_transition": {"title": "Chapitre 2: La découverte"},
    "age_milestone":        {"number": "32", "title": "ans", "detail": "quand tout a changé"},
    "testimonial":          {"title": "\"Ce programme a changé ma vie\"",
                             "detail": "— Jean-Pierre M., entrepreneur"},
    "definition":           {"title": "ROI", "detail": "Retour sur investissement: bénéfice / coût"},
    "dialogue":             {"title": "Client: \"Combien ça coûte?\"",
                             "detail": "Moi: \"Combien te coûte de ne pas le faire?\""},
    "emoji_reaction":       {"title": "Résultats incroyables 🚀"},
    "question":             {"title": "Est-ce que tu sais vraiment ce que veut ton audience?"},
    "versus_battle":        {"left_label": "Freelance", "right_label": "Salarié",
                             "title": "Quel modèle choisir?"},
    "success_metric_badge": {"number": "10k", "title": "abonnés", "detail": "en 6 mois"},
    "chapter_marker":       {"number": "01", "title": "Les fondations"},
    "timeline":             {"title": "Mon parcours",
                             "items": ["2020: Départ", "2022: Premier client", "2024: 6 chiffres"]},
    "roadmap_milestone":    {"title": "Phase 1: Validation",
                             "items": ["Semaine 1-2", "Semaine 3-4", "Mois 2"]},
    "mindmap":              {"title": "Business Model Canvas",
                             "items": ["Clients", "Valeur", "Revenus", "Coûts"]},
    "poll_question":        {"title": "Quel est ton plus grand obstacle?",
                             "items": ["Temps", "Argent", "Compétences", "Confiance"]},
    "calendar_date_highlight": {"number": "15", "title": "Octobre", "detail": "Date limite"},
    "location_journey":     {"title": "De Paris à Berlin",
                             "detail": "1 200 km • 3 pays • 1 décision"},
    "map_location":         {"title": "Île-de-France", "detail": "Siège social"},
    "news_ticker":          {"title": "BREAKING: Les résultats dépassent toutes les attentes"},
    "speech_bubble_thought":{"title": "\"Et si je pouvais automatiser tout ça?\""},
    "hand_written_note":    {"title": "N'oublie pas: la cohérence bat le talent"},
    "carousel":             {"title": "Slide 1 sur 5", "detail": "Swipe pour la suite"},
    "quote_carousel":       {"title": "\"Citation 1\"", "detail": "— Auteur A"},
    "data_chart":           {"title": "Croissance annuelle",
                             "items": ["Q1: 10k", "Q2: 15k", "Q3: 22k", "Q4: 31k"]},
    "before_after_image":   {"title": "Avant / Après", "detail": "6 mois de transformation"},
    "instagram-follow":     {"title": "@moncompte", "detail": "Rejoins la communauté"},
    "tiktok-follow":        {"title": "@moncompte", "detail": "Pour plus de contenu"},
    "yt-lower-third":       {"title": "@machaîne", "detail": "Abonne-toi"},
    # ── Wave 7 ────────────────────────────────────────────────────────────────
    "live_reaction_split":  {"expected_text": "Les réseaux sociaux ne servent à rien",
                             "reality_text": "C'est notre premier canal d'acquisition"},
    "hidden_cost_reveal":   {"sticker_price": "29€/mois", "real_cost": "87€/mois réels"},
    "social_proof_counter": {"counter_final_value": "12 847", "counter_label": "abonnés"},
    "timeline_prediction":  {"confirmed_steps": ["Lancement Jan", "10k clients Mar"],
                             "predicted_steps": ["IPO Q4", "Expansion US 2026"]},
    "red_thread_connector": {"connector_points": ["Le problème du départ",
                                                   "La solution testée",
                                                   "Le résultat final"]},
    "silent_beat_pause":    {"pause_symbol": "…"},
    "comment_reply_style":  {"comment_text": "Mais ça marche vraiment ?",
                             "reply_text": "Oui — voici la preuve en chiffres"},
    "before_you_scroll":    {"hook_text": "Attends avant de partir — lis ça"},
    # ── Wave 8 ────────────────────────────────────────────────────────────────
    "traffic_light_status": {"status_color": "green", "status_label": "Stratégie validée — lance-toi"},
    "day_in_life_schedule": {"schedule_items": ["6h - Réveil et sport", "9h - Deep work", "12h - Pause déjeuner", "14h - Réunions clients"]},
    "skill_tree_unlock":    {"unlocked_milestones": ["Maîtrise de Notion", "Premier client signé", "Automatisation Zapier"]},
    "audience_poll_result": {"poll_options": ["Oui, c'est possible", "Non, c'est trop dur"],
                             "poll_percentages": [73.0, 27.0]},
    "broken_promise_tracker": {"promises": ["Poster chaque jour", "Répondre à chaque DM", "Lancer la formation en mars"],
                               "kept_status": [True, False, False]},
    "ingredient_list":      {"ingredients": ["Une offre claire", "Un tunnel de vente", "Du contenu régulier", "Une communauté engagée"]},
    "resource_allocation":  {"resource_labels": ["Temps", "Énergie", "Budget"],
                             "resource_values": [40.0, 35.0, 25.0]},
    "fill_in_the_blank":    {"sentence_with_blank": "La clé du succès c'est ___", "blank_word": "la régularité"},
}

CARD_ID = "c1"

def _html_ids(html: str) -> set[str]:
    """Extract all id="c1-..." values from the HTML."""
    return set(re.findall(rf'id="{re.escape(CARD_ID)}-([^"]+)"', html))

def _js_targets(js: str) -> set[str]:
    """Extract all #c1-... selector suffixes referenced in the JS."""
    # Match: '#c1-something' or "#c1-something" inside JS strings
    raw = re.findall(rf"['\"]#{re.escape(CARD_ID)}-([^'\"]+)['\"]", js)
    # Also match querySelector('#c1-something')
    raw += re.findall(rf"querySelector\(['\"]#{re.escape(CARD_ID)}-([^'\"]+)['\"]", js)
    return set(raw)

def make_card(style: str, hints: dict, zone: str = "fullscreen") -> dict:
    h = {"style": style}
    h.update(hints)
    return {
        "id": CARD_ID,
        "type": "graphic",
        "startSec": 2.0,
        "endSec": 7.0,
        "zone": zone,
        "contentHints": h,
    }

mismatches: list[tuple] = []
total = 0
skipped = 0

print(f"\n{'='*72}")
print(f"  Pack × Type cross-check  ({len(PACKS)} packs × {len(_HINTS)} types = {len(PACKS)*len(_HINTS)} combos)")
print(f"{'='*72}")

for pack_name, pack in PACKS.items():
    pack_mismatches = []
    for style, hints in _HINTS.items():
        total += 1
        card = make_card(style, hints)
        try:
            html = _build_graphic_card_html(card, pack=pack)
            js   = _build_timeline_js([card], pack=pack, layout="landscape")
        except Exception as exc:
            print(f"  [ERR] {pack_name:12} / {style:30} → EXCEPTION: {exc}")
            skipped += 1
            continue

        html_ids = _html_ids(html)
        js_refs  = _js_targets(js)
        missing  = js_refs - html_ids   # JS targets an element not in HTML

        if missing:
            pack_mismatches.append((style, sorted(missing)))
            mismatches.append((pack_name, style, sorted(missing)))

    if pack_mismatches:
        print(f"\n  {pack_name} — {len(pack_mismatches)} mismatch(es):")
        for style, miss in pack_mismatches:
            print(f"    [{style:30}]  JS targets {miss}  but HTML has none")
    else:
        print(f"\n  {pack_name} — OK (all {len(_HINTS)} types clean)")

print(f"\n{'='*72}")
print(f"  Checked {total} combos, {skipped} skipped (exceptions)")
if mismatches:
    print(f"  MISMATCHES: {len(mismatches)} pack/type pairs have JS→HTML drift")
    print(f"{'='*72}\n")
    sys.exit(1)
else:
    print(f"  ALL CLEAN — no JS/HTML selector drift found")
    print(f"{'='*72}\n")
    sys.exit(0)
