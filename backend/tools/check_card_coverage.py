#!/usr/bin/env python3
"""
Card handler coverage guard.

Verifies that every contentHints.style value has a handler in BOTH
_build_graphic_card_html() and _build_timeline_js() in compose.py.

Styles in _HTML_GENERIC_FALLTHROUGH are handled by the else-clause in HTML
(renders .title + .detail) — intentional, not a gap.

Catalogue styles in _CATALOGUE_REQUIRED MUST appear in both functions as
explicit elif branches before any primitive merge is accepted.

Usage:
    python backend/tools/check_card_coverage.py

Exit 0 = all covered.
Exit 1 = gap found — print all gaps before exiting.
"""
import re
import sys
from pathlib import Path

COMPOSE = Path(__file__).parent.parent / "app" / "engine" / "compose.py"

# Styles handled by the generic HTML else-clause (render .title/.detail).
# They have dedicated GSAP branches but no explicit HTML elif block.
_HTML_GENERIC_FALLTHROUGH: frozenset[str] = frozenset({
    "key_phrase",
    "quote",
    "question",
    "stat",
})

# Catalogue primitives: must be registered in BOTH functions before any merge.
_CATALOGUE_REQUIRED: frozenset[str] = frozenset({
    "prim_stat_counter",
    "prim_numbered_rule",
    "prim_anecdote_frame",
    "prim_split_compare",
})


def _extract_styles(block: str) -> set[str]:
    """Extract all explicit content_style branch values from a source block."""
    styles: set[str] = set()
    # content_style == "foo" or content_style == 'foo'
    styles |= set(re.findall(r'content_style\s*==\s*["\'](\w+)["\']', block))
    # content_style in ("foo", "bar") or content_style in {"foo"}
    for m in re.findall(r'content_style\s+in\s+[\(\{]([^\)\}]+)[\)\}]', block):
        styles |= set(re.findall(r'["\'](\w+)["\']', m))
    return styles


def _slice_fn(src: str, fn_name: str, end_marker: str) -> str:
    start = src.index(f"def {fn_name}(")
    end = src.index(end_marker, start)
    return src[start:end]


def main() -> int:
    if not COMPOSE.exists():
        print(f"ERROR: compose.py not found at {COMPOSE}", file=sys.stderr)
        return 1

    src = COMPOSE.read_text(encoding="utf-8")

    html_block = _slice_fn(src, "_build_graphic_card_html", "def _build_caption_card_html(")
    gsap_block = _slice_fn(src, "_build_timeline_js", "def _esc(")

    html_styles = _extract_styles(html_block)
    gsap_styles = _extract_styles(gsap_block)

    errors: list[str] = []

    # Rule 1: Every HTML-explicit style must have a GSAP handler.
    for s in sorted(html_styles):
        if s not in gsap_styles:
            errors.append(
                f"MISSING GSAP handler: '{s}' is in _build_graphic_card_html() "
                f"but has no branch in _build_timeline_js()"
            )

    # Rule 2: Every GSAP style must be in HTML (explicit or known generic-fallthrough).
    html_all = html_styles | _HTML_GENERIC_FALLTHROUGH
    for s in sorted(gsap_styles):
        if s not in html_all:
            errors.append(
                f"MISSING HTML handler: '{s}' is in _build_timeline_js() but has "
                f"no elif in _build_graphic_card_html() and is not in _HTML_GENERIC_FALLTHROUGH"
            )

    # Rule 3: All catalogue-required styles must appear explicitly in BOTH.
    for s in sorted(_CATALOGUE_REQUIRED):
        if s not in html_styles:
            errors.append(
                f"MISSING HTML handler: catalogue style '{s}' not registered "
                f"in _build_graphic_card_html()"
            )
        if s not in gsap_styles:
            errors.append(
                f"MISSING GSAP handler: catalogue style '{s}' not registered "
                f"in _build_timeline_js()"
            )

    if errors:
        print(f"FAIL — {len(errors)} coverage gap(s) in {COMPOSE.name}:")
        for e in errors:
            print(f"  {e}")
        return 1

    total = len(html_styles | gsap_styles | _CATALOGUE_REQUIRED)
    print(
        f"OK — {total} styles verified:\n"
        f"  HTML explicit={len(html_styles)}, "
        f"GSAP={len(gsap_styles)}, "
        f"generic-fallthrough={len(_HTML_GENERIC_FALLTHROUGH)} (intentional), "
        f"catalogue-required={len(_CATALOGUE_REQUIRED)} (all present)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
