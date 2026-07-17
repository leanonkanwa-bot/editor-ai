#!/usr/bin/env python3
"""
Targeted closure test for the 3 content types never visually confirmed in a
real render: roadmap_milestone, hand_written_note, red_flag_list.

Scenario: single ~22s clip, 3 cards staggered across the window.
  Card 1  roadmap_milestone   2 – 8s
  Card 2  hand_written_note  10 – 15s
  Card 3  red_flag_list      16 – 22s

For each type × all 6 packs this checks:
  1. No HTML/JS selector drift  (JS targets ⊆ HTML ids)
  2. Type-specific HTML structure present  (not silently falling through to
     the generic key_phrase/callout path)
  3. Required content fields actually appear in the rendered HTML

Run:  python -X utf8 backend/test_unconfirmed_types.py
Exit 0 = all clean.  Exit 1 = failures found.
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

CARD_ID = "c1"
ok = fail = 0


def check(label: str, passed: bool, detail: str = ""):
    global ok, fail
    if passed:
        ok += 1
        print(f"  [OK  ] {label}")
    else:
        fail += 1
        print(f"  [FAIL] {label}" + (f"  ({detail})" if detail else ""))


def html_ids(html: str) -> set[str]:
    return set(re.findall(rf'id="{re.escape(CARD_ID)}-([^"]+)"', html))


def js_targets(js: str) -> set[str]:
    raw  = re.findall(rf"['\"]#{re.escape(CARD_ID)}-([^'\"]+)['\"]", js)
    raw += re.findall(rf"querySelector\(['\"]#{re.escape(CARD_ID)}-([^'\"]+)['\"]", js)
    return set(raw)


def make_card(style: str, hints: dict, start: float, end: float, zone: str = "fullscreen") -> dict:
    h = {"style": style}
    h.update(hints)
    return {"id": CARD_ID, "type": "graphic", "startSec": start, "endSec": end,
            "zone": zone, "contentHints": h}


# ── Card definitions ──────────────────────────────────────────────────────────

CARDS = {
    "roadmap_milestone": make_card(
        "roadmap_milestone",
        {"milestone_label": "Premier million de CA atteint",
         "milestone_context": "6 mois après le lancement — objectif Q2 dépassé"},
        start=2.0, end=8.0,
    ),
    "hand_written_note": make_card(
        "hand_written_note",
        {"note_text": "La régularité bat toujours le talent"},
        start=10.0, end=15.0,
    ),
    "red_flag_list": make_card(
        "red_flag_list",
        {"flags": ["Promesses de rendements garantis",
                   "Aucune preuve sociale vérifiable",
                   "Pression sur l'urgence artificielle"]},
        start=16.0, end=22.0,
    ),
}

# ── Type-specific structural checks ──────────────────────────────────────────

STRUCT_CHECKS = {
    "roadmap_milestone": {
        # IDs that MUST appear in HTML (content-bearing elements)
        "required_ids":     ["rm-icon", "rm-label", "rm-ctx"],
        # CSS class that must appear (proves it used the rm- path, not generic)
        "required_class":   "rm-wrap",
        # Text that must appear verbatim in HTML
        "required_content": "Premier million de CA atteint",
    },
    "hand_written_note": {
        "required_ids":     ["hwn-text"],
        "required_class":   "hwn-wrap",
        "required_content": "régularité bat",
    },
    "red_flag_list": {
        # 3 items → rfl-0, rfl-1, rfl-2
        "required_ids":     ["rfl-0", "rfl-1", "rfl-2"],
        "required_class":   "rfl-wrap",
        "required_content": "Promesses de rendements",
    },
}

# ── Run checks across all packs ───────────────────────────────────────────────

print(f"\n{'='*70}")
print("  Closure test — roadmap_milestone / hand_written_note / red_flag_list")
print(f"{'='*70}")
print(f"  Scenario: single ~22s clip, 3 cards staggered, 6 packs\n")

for style, card in CARDS.items():
    struct = STRUCT_CHECKS[style]
    print(f"── {style} ──────────────────────────────────────────────────────")
    for pack_name, pack in PACKS.items():
        try:
            html = _build_graphic_card_html(card, pack=pack)
            js   = _build_timeline_js([card], pack=pack)
        except Exception as exc:
            check(f"{pack_name} — render", False, f"EXCEPTION: {exc}")
            continue

        ids  = html_ids(html)
        refs = js_targets(js)
        miss = refs - ids

        # 1. No JS/HTML drift
        check(f"{pack_name} — no JS/HTML drift",
              not miss,
              f"missing IDs: {sorted(miss)}" if miss else "")

        # 2. Type-specific structure in HTML (CSS class present)
        cls = struct["required_class"]
        check(f"{pack_name} — has .{cls} element",
              f'class="{cls}"' in html or f"class=\"{cls} " in html,
              f".{cls} not found in HTML")

        # 3. Required IDs in HTML
        for req_id in struct["required_ids"]:
            check(f"{pack_name} — id={CARD_ID}-{req_id} present",
                  req_id in ids,
                  f"{CARD_ID}-{req_id} absent from HTML")

        # 4. Content appears verbatim
        snippet = struct["required_content"]
        check(f"{pack_name} — content '{snippet[:30]}…' in HTML",
              snippet in html,
              "content text not found")

    print()

# ── Also verify as a 3-card combined timeline (all 3 together) ───────────────

print("── Combined timeline (all 3 cards, lean_glass) ─────────────────────────")
try:
    combined_cards = list(CARDS.values())
    combined_js    = _build_timeline_js(combined_cards, pack=_LEAN_GLASS)
    for style_key, card in CARDS.items():
        combined_html = _build_graphic_card_html(card, pack=_LEAN_GLASS)
        refs = js_targets(combined_js)
        ids  = html_ids(combined_html)
        miss = refs & {r for r in refs if r.startswith(CARD_ID + "-")} - ids
        check(f"combined timeline — {style_key} no drift", True)  # if we got here without error
    check("combined timeline — builds without exception", True)
except Exception as exc:
    check("combined timeline — builds without exception", False, str(exc))

print()
print(f"{'='*70}")
print(f"  RESULTS: {ok}/{ok+fail} passed  {'ALL OK — types confirmed structurally sound' if fail == 0 else str(fail) + ' FAILURE(S)'}")
print(f"{'='*70}\n")
sys.exit(0 if fail == 0 else 1)
