"""
scaling_moment — horizontal FROM → TO milestone scene for scaling events.

Triggers on:
  FR: "mis à l'échelle", "passé à grande échelle", "de solo à une équipe de N",
      "de local à international", "de 0 à des milliers de clients",
      "scaled up", "passé de freelance à agence"
  EN: "scaled from X to Y", "went from solo to a team", "from local to global",
      "from 0 to thousands of clients", "scaled up from X"

Visual: start_label (left) → animated expanding arrow → end_label (right, highlighted).
Distinct from growth_curve (which traces an SVG path for numeric growth);
scaling_moment shows two named states with an expanding arrow transition.
"""
from __future__ import annotations

import re
from app.engine.broll_registry import BRollType, register


# ── Patterns ──────────────────────────────────────────────────────────────────

_SCALE_FR_RE = re.compile(
    r"\b(?:"
    r"mis\s+à\s+l'échelle|"
    r"passé\s+à\s+(?:grande\s+)?l'échelle|"
    r"de\s+solo\s+à\s+(?:une\s+)?(?:équipe|agence|entreprise)|"
    r"de\s+(?:local|freelance|indépendant|autoentrepreneur)\s+à\s+(?:\w+\s+){0,3}(?:agence|international|national|entreprise)|"
    r"scalé\s+(?:notre|mon|le)\s+business|"
    r"passé\s+de\s+freelance\s+à|"
    r"de\s+(?:\d+\s+)?clients?\s+à\s+(?:des\s+)?(?:milliers|centaines|dizaines)\s+de"
    r")\b",
    re.IGNORECASE,
)

_SCALE_EN_RE = re.compile(
    r"\b(?:"
    r"scaled?\s+(?:up\s+)?from\s+(?P<sc_from>[\w\s]{1,30?})\s+to\s+(?P<sc_to>[\w\s]{1,30})|"
    r"went\s+from\s+(?:solo|one\s+person|just\s+me|freelance)\s+to\s+"
    r"(?:a\s+team|an?\s+agency|(?:\d+\s+)?(?:employees?|people|clients?))|"
    r"from\s+(?:local|one\s+city|one\s+country)\s+to\s+(?:global|international|worldwide)|"
    r"from\s+0\s+to\s+(?:thousands?|hundreds?|millions?)\s+of\s+(?:clients?|customers?|users?)|"
    r"scaled?\s+(?:the\s+business|our\s+operations|our\s+team)\s+(?:from|up)"
    r")\b",
    re.IGNORECASE,
)

_ALL_PATTERNS = [_SCALE_FR_RE, _SCALE_EN_RE]


def _ctx_words(words, idx: int, radius: int = 8) -> str:
    n = len(words)
    return " ".join(
        getattr(words[i], "text", "")
        for i in range(max(0, idx - radius), min(n, idx + radius + 1))
    )


def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ej(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


# ── Extractor ─────────────────────────────────────────────────────────────────

def _extractor(match, words, word_idx: int) -> tuple[dict, float]:
    conf = 0.88 if match.re is _SCALE_FR_RE else 0.84

    gd = match.groupdict()
    sc_from = (gd.get("sc_from") or "").strip()[:40]
    sc_to   = (gd.get("sc_to")  or "").strip()[:40]

    # fallback: extract context words
    if not sc_from:
        ctx = _ctx_words(words, word_idx, 5)
        sc_from = ctx[:40].strip()

    return {
        "start_label": sc_from,
        "end_label":   sc_to,
    }, conf


# ── Render HTML ───────────────────────────────────────────────────────────────

def _render_html(params: dict, pack: dict, card_id: str) -> str:
    p = pack or {}
    bg       = p.get("bg",             "#1a1a1a")
    text_c   = p.get("text",           "#f1f1f1")
    text_s   = p.get("text_secondary", "rgba(255,255,255,0.45)")
    accent   = p.get("accent",         "#4cc9f0")
    font     = p.get("font",           '"Inter", sans-serif')
    fw       = p.get("font_weight",    "800")
    radius   = p.get("radius",         "20px")
    shadow   = p.get("shadow",         "0 8px 32px rgba(0,0,0,0.4)")
    shadow_i = p.get("shadow_inset",   "")
    shadow_v = f"{shadow}, {shadow_i}" if shadow_i else shadow
    glow     = p.get("title_glow",     "")
    glow_i   = p.get("title_glow_intense", "")
    pack_id  = p.get("id",             "")

    start_raw = params.get("start_label", "")
    end_raw   = params.get("end_label",   "")

    glow_css   = f" text-shadow:{_e(glow)};"   if glow   else ""
    glow_i_css = f" text-shadow:{_e(glow_i)};" if glow_i else ""

    # Fallback display values
    start_text = _e(start_raw) if start_raw else "Avant"
    end_text   = _e(end_raw)   if end_raw   else "À l'échelle"

    # Pack-specific headline
    if pack_id == "lean_ledger":
        kicker = "SCALE EVENT"
    elif pack_id in ("lean_craft", "lean_cinema"):
        kicker = "La mise à l'échelle"
    else:
        kicker = "MISE À L'ÉCHELLE"

    # Arrow style per pack
    if pack_id == "lean_glass":
        arrow_css  = f"color:{accent}; font-size:28px; opacity:0; transform:scaleX(0.3); text-shadow:{glow_i};" if glow_i else f"color:{accent}; font-size:28px; opacity:0; transform:scaleX(0.3);"
    elif pack_id == "lean_vibe":
        arrow_css  = f"color:{accent}; font-size:32px; opacity:0; transform:scale(0.2);"
    elif pack_id == "lean_ledger":
        arrow_css  = f"color:{accent}; font-size:22px; opacity:0; font-family:monospace;"
    else:
        arrow_css  = f"color:{accent}; font-size:28px; opacity:0;"

    # Start/end label sizing
    label_from_color = text_s
    label_to_color   = accent

    css = f"""\
.card[data-card-id="{card_id}"] .root{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;}}
.card[data-card-id="{card_id}"] .sm-wrap{{background:{bg};border-radius:{radius};padding:32px 36px;
  display:flex;flex-direction:column;gap:20px;box-shadow:{shadow_v};width:90%;max-width:460px;}}
.card[data-card-id="{card_id}"] .sm-kicker{{font-family:{font};font-size:11px;font-weight:700;
  letter-spacing:0.18em;text-transform:uppercase;color:{text_s};opacity:0;margin-bottom:2px;}}
.card[data-card-id="{card_id}"] .sm-row{{display:flex;align-items:center;gap:16px;justify-content:space-between;}}
.card[data-card-id="{card_id}"] .sm-from{{font-family:{font};font-size:24px;font-weight:{fw};
  color:{label_from_color};line-height:1.2;opacity:0;flex:1;{glow_css}}}
.card[data-card-id="{card_id}"] .sm-arrow{{{arrow_css}flex:0 0 auto;}}
.card[data-card-id="{card_id}"] .sm-to{{font-family:{font};font-size:26px;font-weight:{fw};
  color:{label_to_color};line-height:1.2;opacity:0;flex:1;text-align:right;{glow_i_css}}}
.card[data-card-id="{card_id}"] .sm-line{{width:0;height:2px;background:{accent};border-radius:2px;{glow_css}}}"""

    arrow_char = "→→→" if pack_id == "lean_ledger" else "→"

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="sm-wrap">
    <div class="sm-kicker" id="{card_id}-sm-kicker">{_e(kicker)}</div>
    <div class="sm-row">
      <div class="sm-from" id="{card_id}-sm-from">{start_text}</div>
      <div class="sm-arrow" id="{card_id}-sm-arrow">{_e(arrow_char)}</div>
      <div class="sm-to" id="{card_id}-sm-to">{end_text}</div>
    </div>
    <div class="sm-line" id="{card_id}-sm-line"></div>
  </div>
</div>
</div>"""


# ── Render GSAP ───────────────────────────────────────────────────────────────

def _render_gsap(params: dict, pack: dict, card_id: str, start: float, end: float) -> list[str]:
    p       = pack or {}
    cid     = _ej(card_id)
    pack_id = p.get("id", "")

    is_cinema = pack_id == "lean_cinema"
    is_ledger = pack_id == "lean_ledger"
    is_vibe   = pack_id == "lean_vibe"
    is_glass  = pack_id == "lean_glass"

    t_in    = round(start + 0.18, 4)
    t_kick  = t_in
    t_from  = round(t_in + 0.22, 4)
    t_arrow = round(t_from + 0.28, 4)
    t_to    = round(t_arrow + 0.22, 4)
    t_line  = round(t_to + 0.20, 4)

    d_kick = 0.60 if is_cinema else 0.22
    ease   = "none" if is_ledger else "power2.out"

    lines: list[str] = []

    # Kicker
    lines.append(f"  tl.to('#{cid}-sm-kicker',{{opacity:1,duration:{d_kick},ease:'{ease}'}},{t_kick:.4f});")

    # From label
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-sm-from',{{opacity:1,duration:0.70,ease:'power1.in'}},{t_from:.4f});")
    elif is_ledger:
        lines.append(f"  tl.to('#{cid}-sm-from',{{opacity:1,duration:0.15,ease:'none'}},{t_from:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-sm-from',{{opacity:0,x:-10}},{{opacity:1,x:0,duration:0.28,ease:'power2.out'}},{t_from:.4f});")

    # Arrow expands
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-sm-arrow',{{opacity:1,duration:0.60,ease:'power1.in'}},{t_arrow:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-sm-arrow',{{opacity:0,scale:0.2}},{{opacity:1,scale:1,duration:0.35,ease:'back.out(2.0)'}},{t_arrow:.4f});")
    elif is_glass:
        lines.append(f"  tl.fromTo('#{cid}-sm-arrow',{{opacity:0,scaleX:0.3}},{{opacity:1,scaleX:1,duration:0.35,ease:'power2.out',transformOrigin:'left center'}},{t_arrow:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-sm-arrow',{{opacity:0,scaleX:0.2}},{{opacity:1,scaleX:1,duration:0.30,ease:'power2.out',transformOrigin:'left center'}},{t_arrow:.4f});")

    # To label (highlighted)
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-sm-to',{{opacity:1,duration:0.70,ease:'power2.in'}},{t_to:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-sm-to',{{opacity:0,scale:0.85}},{{opacity:1,scale:1,duration:0.35,ease:'back.out(1.5)'}},{t_to:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-sm-to',{{opacity:0,x:10}},{{opacity:1,x:0,duration:0.28,ease:'power2.out'}},{t_to:.4f});")

    # Accent line
    line_w = "60px" if is_cinema else ("48px" if is_ledger else "80px")
    lines.append(f"  tl.to('#{cid}-sm-line',{{width:'{line_w}',duration:0.40,ease:'power2.out'}},{t_line:.4f});")

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

register(BRollType(
    name="scaling_moment",
    patterns=_ALL_PATTERNS,
    extractor=_extractor,
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=5.0,
    preferred_zone="upper-data",
    min_confidence=0.82,
))
