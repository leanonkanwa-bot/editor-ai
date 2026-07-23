#!/usr/bin/env python3
"""
Wave 1 Scene Primitives — structural coverage sweep (42 combos: 7 types × 6 packs).

Checks per combo:
  1. render_html executes without exception (smoke render)
  2. render_gsap executes without exception (smoke render)
  3. JS selector integrity: all #card_id-xxx refs in GSAP exist as HTML ids
  4. Wrapper CSS class present in HTML

Run:  python -X utf8 backend/test_scene_primitives_coverage.py
Exit 0 = all 42 combos pass. Exit 1 = failures.
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

# Force broll_types to load before importing
import app.engine.broll_types  # noqa: F401

from app.engine.compose import (
    _LEAN_GLASS, _LEAN_PAPER, _LEAN_VIBE,
    _LEAN_LEDGER, _LEAN_CRAFT, _LEAN_CINEMA,
    _build_card_host, _build_timeline_js,
)
from app.engine import broll_registry as _br

PACKS = [
    ("lean_glass",  _LEAN_GLASS),
    ("lean_paper",  _LEAN_PAPER),
    ("lean_vibe",   _LEAN_VIBE),
    ("lean_ledger", _LEAN_LEDGER),
    ("lean_craft",  _LEAN_CRAFT),
    ("lean_cinema", _LEAN_CINEMA),
]

CARD_ID = "c1"

# ── Scene primitive specs ──────────────────────────────────────────────────────
# (broll_type_name, sample_params, required_wrapper_class, required_content_snippet)

SCENE_TYPES = [
    (
        "client_transformation",
        {"before_state": "j'étais épuisé et sans revenus", "after_state": "10k€/mois en travaillant 4h/jour"},
        "ct-wrap",
        "AVANT",
    ),
    (
        "first_sale_moment",
        {"sale_context": "ma première vente de formation à 497€"},
        "fs-wrap",
        "PREMIÈRE VENTE",
    ),
    (
        "team_growth",
        {"start_count": 2, "end_count": 8},
        "tg-wrap",
        "MEMBRES",
    ),
    (
        "investment_decision",
        {"investment_label": "j'ai investi 15 000€ dans un accompagnement"},
        "inv-wrap",
        "J'AI INVESTI",
    ),
    (
        "breaking_point",
        {"breaking_context": "j'en pouvais plus de travailler 70 heures par semaine"},
        "bp-wrap",
        "POINT DE RUPTURE",
    ),
    (
        "mentor_guidance",
        {"mentor_label": "mon mentor m'a dit de viser plus haut", "mentee_label": "ce que j'ai appris ce jour-là"},
        "mg-wrap",
        "MON MENTOR",
    ),
    (
        "scaling_moment",
        {"start_label": "solo freelance", "end_label": "agence de 12 personnes"},
        "sm-wrap",
        "MISE À L'ÉCHELLE",
    ),
]


def html_ids(html: str) -> set[str]:
    return set(re.findall(rf'id="{re.escape(CARD_ID)}-([^"]+)"', html))


def js_targets(js: str) -> set[str]:
    raw  = re.findall(rf"['\"]#{re.escape(CARD_ID)}-([^'\"]+)['\"]", js)
    raw += re.findall(rf"querySelector\(['\"]#{re.escape(CARD_ID)}-([^'\"]+)['\"]", js)
    return set(raw)


def make_broll_card(broll_type: str, params: dict) -> dict:
    return {
        "id": CARD_ID,
        "type": "graphic",
        "startSec": 2.0,
        "endSec": 7.5,
        "zone": "upper-data",
        "_broll_type": broll_type,
        "_broll_params": params,
        "contentHints": {"style": "__broll__"},
    }


# ── Registry membership check ─────────────────────────────────────────────────

print(f"\n{'='*72}")
print("  WAVE 1 SCENE PRIMITIVES — STRUCTURAL COVERAGE")
print(f"{'='*72}\n")

print("── Check 0: registry membership ────────────────────────────────────────\n")

missing_types = []
for btype, _, _, _ in SCENE_TYPES:
    in_reg = btype in _br.REGISTRY
    status = "OK  " if in_reg else "MISS"
    print(f"  {status}  {btype}")
    if not in_reg:
        missing_types.append(btype)

if missing_types:
    print(f"\n  FATAL: {len(missing_types)} type(s) not in REGISTRY: {missing_types}")
    sys.exit(1)

print()

# ── 42-combo sweep ────────────────────────────────────────────────────────────

W  = 28   # type name column width
PW = 12   # pack column width

pack_names = [p for p, _ in PACKS]

print(f"── Check 1+2+3+4: HTML/JS integrity + smoke render  "
      f"({len(SCENE_TYPES)} types × 6 packs = {len(SCENE_TYPES)*6} combos) ──\n")

header = f"{'TYPE':<{W}} " + "  ".join(f"{p[:10]:<{PW}}" for p in pack_names)
print(header)
print("-" * len(header))

total    = 0
failures: list[tuple[str, str, str]] = []

for btype, params, wrapper_cls, content_snippet in SCENE_TYPES:
    card = make_broll_card(btype, params)
    row  = f"{btype:<{W}}"
    ok_row = True

    for pack_name, pack in PACKS:
        total += 1
        cell_ok = True
        issues: list[str] = []

        try:
            # Use _build_card_host so the scrim sibling div is included in the HTML.
            html = _build_card_host(card, "portrait", 2, pack=pack)
        except Exception as exc:
            issues.append(f"HTML exception: {exc}")
            cell_ok = False

        try:
            js = _build_timeline_js([card], pack=pack, layout="portrait")
        except Exception as exc:
            issues.append(f"JS exception: {exc}")
            cell_ok = False

        if cell_ok:
            # a. non-empty
            if not (html or "").strip():
                issues.append("HTML empty")
            if not (js or "").strip():
                issues.append("JS empty")

            # b. wrapper class present
            if f'class="{wrapper_cls}"' not in html and f'class="{wrapper_cls} ' not in html:
                issues.append(f".{wrapper_cls} missing")

            # c. content snippet
            if content_snippet not in html:
                # Try pack-specific variants (e.g. ledger uses EN labels)
                alt_snippets = {
                    "lean_ledger": {
                        "AVANT": "BEFORE",
                        "PREMIÈRE VENTE": "FIRST SALE",
                        "MEMBRES": "TEAM:",
                        "J'AI INVESTI": "INVESTMENT",
                        "POINT DE RUPTURE": "BREAKING",
                        "MON MENTOR": "MENTOR",
                        "MISE À L'ÉCHELLE": "SCALE",
                    },
                    "lean_cinema": {
                        "AVANT": "Avant",
                        "PREMIÈRE VENTE": "première vente",
                        "MEMBRES": "membres",
                        "J'AI INVESTI": "pari",
                        "POINT DE RUPTURE": "Point de rupture",
                        "MON MENTOR": "mentor",
                        "MISE À L'ÉCHELLE": "l'échelle",
                    },
                    "lean_craft": {
                        "AVANT": "Avant",
                        "PREMIÈRE VENTE": "Première vente",
                        "MEMBRES": "membres",
                        "J'AI INVESTI": "investissement",
                        "POINT DE RUPTURE": "Point de rupture",
                        "MON MENTOR": "mentor",
                        "MISE À L'ÉCHELLE": "l'échelle",
                    },
                    "lean_vibe": {
                        "J'AI INVESTI": "PARI",
                    },
                }
                alt = alt_snippets.get(pack_name, {}).get(content_snippet, "")
                if not alt or alt not in html:
                    issues.append(f"content '{content_snippet[:20]}' absent")

            # d. JS/HTML ID integrity
            ids  = html_ids(html)
            refs = js_targets(js)
            miss = refs - ids
            if miss:
                issues.append(f"drift:{sorted(miss)}")

        if issues:
            cell_ok = False
            ok_row  = False
            for iss in issues:
                failures.append((btype, pack_name, iss))

        row += f"  {'OK' if cell_ok else 'FAIL':<{PW}}"

    print(row)

print()

# ── Summary ───────────────────────────────────────────────────────────────────

print(f"── Results: {total - len(failures)}/{total} combos passed ──────────────────────────────────\n")

if failures:
    print("FAILURES:\n")
    for btype, pack_name, issue in failures:
        print(f"  [{pack_name}] {btype}: {issue}")
    print()
    sys.exit(1)
else:
    print("  All 42 combos passed. Zero drift. Scene primitives are production-ready.\n")
    sys.exit(0)
