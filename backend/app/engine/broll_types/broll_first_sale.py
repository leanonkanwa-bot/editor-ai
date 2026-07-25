"""
first_sale_moment — notification burst for the first payment / first sale milestone.

Triggers on:
  FR: "première vente", "premier client payant", "premier paiement reçu",
      "ma première commande", "j'ai eu mon premier client"
  EN: "first sale", "first paying customer", "first payment received",
      "made my first sale", "got my first client"

Visual: bold label + flash notification. Pack-specific glow / aesthetic.
Distinct from money_counter (amount-focused) — this is milestone-focused.
"""
from __future__ import annotations

import re
from app.engine.broll_registry import BRollType, register


# ── Patterns ──────────────────────────────────────────────────────────────────

_PREMIERE_VENTE_RE = re.compile(
    r"\b(?:"
    r"premi(?:ère|ers?|ère)\s+(?:vente|commande|transaction|paiement\s+reçu)|"
    r"premier\s+(?:client\s+payant|paiement\s+reçu|abonnement|achat)|"
    r"j'ai\s+(?:eu|fait|obtenu|reçu)\s+(?:mon|ma)\s+premier(?:e)?\s+(?:vente|client|commande|paiement)|"
    r"mon\s+premier\s+client\s+(?:payant|a\s+signé|a\s+payé)"
    r")\b",
    re.IGNORECASE,
)

_FIRST_SALE_EN_RE = re.compile(
    r"\b(?:"
    r"first\s+(?:sale|paying\s+customer|client|payment|order|purchase|transaction)|"
    r"made\s+(?:my\s+)?first\s+sale|"
    r"got\s+(?:my\s+)?first\s+(?:client|customer|sale)|"
    r"first\s+(?:paying\s+)?(?:client|customer)\s+(?:ever|signed|paid)"
    r")\b",
    re.IGNORECASE,
)

_ALL_PATTERNS = [_PREMIERE_VENTE_RE, _FIRST_SALE_EN_RE]

_APOS = ("\u2019", "'")  # right single quotation mark + straight apostrophe


def _ctx_words(words, idx: int, radius: int = 6) -> str:
    n = len(words)
    tokens = [getattr(words[i], "text", "") for i in range(max(0, idx - radius), min(n, idx + radius + 1))]
    merged: list[str] = []
    for w in tokens:
        if merged and any(w.startswith(a) for a in _APOS):
            merged[-1] += w
        else:
            merged.append(w)
    return " ".join(merged)


def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ej(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


# ── Extractor ─────────────────────────────────────────────────────────────────

def _extractor(match, words, word_idx: int) -> tuple[dict, float]:
    conf = 0.88 if match.re is _PREMIERE_VENTE_RE else 0.84
    sale_context = _ctx_words(words, word_idx, 4).strip()[:80]
    return {"sale_context": sale_context}, conf


# ── Render HTML ───────────────────────────────────────────────────────────────

def _render_html(params: dict, pack: dict, card_id: str, compact: bool = False, layout: str = "portrait") -> str:
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
    glow_i   = p.get("title_glow_intense", "")
    border   = p.get("border",         "")
    pack_id  = p.get("id",             "")

    sale_context = _e(params.get("sale_context", ""))

    pad        = "16px 20px" if compact else "32px 40px"
    ring_size  = "48px"      if compact else "64px"
    icon_size  = "22px"      if compact else "28px"
    head_size  = "26px"      if compact else "32px"
    ctx_size   = "15px"      if compact else "18px"
    gap        = "12px"      if compact else "16px"
    border_css = f"; border:{border}" if border else ""

    # Pack-specific label + icon (SVG only — emoji/special-char text fails in headless Chromium)
    if pack_id == "lean_ledger":
        headline  = "FIRST SALE"
        icon_html = (
            f'<div class="fs-icon" id="{card_id}-fs-icon">'
            f'<svg viewBox="0 0 24 24" width="{icon_size}" height="{icon_size}" xmlns="http://www.w3.org/2000/svg">'
            f'<line x1="12" y1="2" x2="12" y2="22" stroke="{accent}" stroke-width="2" stroke-linecap="round"/>'
            f'<path d="M16 5.5 Q7 5.5 7 9.5 Q7 13.5 12 13.5 Q17 13.5 17 17.5 Q17 21.5 8 21.5"'
            f' fill="none" stroke="{accent}" stroke-width="2" stroke-linecap="round"/>'
            f'</svg></div>'
        )
    elif pack_id == "lean_cinema":
        headline  = "La première vente"
        icon_html = (
            f'<div class="fs-icon" id="{card_id}-fs-icon">'
            f'<svg viewBox="0 0 24 24" width="{icon_size}" height="{icon_size}" xmlns="http://www.w3.org/2000/svg">'
            f'<polygon points="12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26"'
            f' fill="{accent}"/>'
            f'</svg></div>'
        )
    elif pack_id == "lean_vibe":
        headline  = "PREMIÈRE VENTE !"
        icon_html = (
            f'<div class="fs-icon" id="{card_id}-fs-icon">'
            f'<svg viewBox="0 0 24 24" width="{icon_size}" height="{icon_size}" xmlns="http://www.w3.org/2000/svg">'
            f'<polygon points="12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26"'
            f' fill="{accent}"/>'
            f'</svg></div>'
        )
    elif pack_id == "lean_craft":
        headline  = "Première vente"
        icon_html = (
            f'<div class="fs-icon" id="{card_id}-fs-icon">'
            f'<svg viewBox="0 0 24 24" width="{icon_size}" height="{icon_size}" xmlns="http://www.w3.org/2000/svg">'
            f'<path d="M12 2 L14.5 9.5 L22 12 L14.5 14.5 L12 22 L9.5 14.5 L2 12 L9.5 9.5 Z"'
            f' fill="{accent}"/>'
            f'</svg></div>'
        )
    else:
        headline  = "PREMIÈRE VENTE"
        icon_html = (
            f'<div class="fs-icon" id="{card_id}-fs-icon">'
            f'<svg viewBox="0 0 24 24" width="{icon_size}" height="{icon_size}" xmlns="http://www.w3.org/2000/svg">'
            f'<path d="M13 2 L4 13 L10.5 13 L8.5 22 L20 11 L13.5 11 Z" fill="{accent}"/>'
            f'</svg></div>'
        )

    glow_css = f" text-shadow:{_e(glow_i)};" if glow_i else ""

    # Pack-specific burst ring
    if pack_id in ("lean_glass",):
        ring_css = f"box-shadow:0 0 0 0 {accent}; border-radius:50%; width:{ring_size}; height:{ring_size}; display:flex; align-items:center; justify-content:center; background:rgba(76,201,240,0.12);"
    elif pack_id == "lean_vibe":
        ring_css = f"border-radius:50%; width:{ring_size}; height:{ring_size}; display:flex; align-items:center; justify-content:center; background:rgba(255,255,255,0.20);"
    elif pack_id == "lean_ledger":
        ring_css = f"border:1px solid {accent}; border-radius:4px; width:{ring_size}; height:{ring_size}; display:flex; align-items:center; justify-content:center; background:transparent;"
    else:
        ring_css = f"border-radius:50%; width:{ring_size}; height:{ring_size}; display:flex; align-items:center; justify-content:center; background:rgba(128,128,128,0.10);"

    css = f"""\
.card[data-card-id="{card_id}"] .root{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;}}
.card[data-card-id="{card_id}"] .fs-wrap{{background:{bg};border-radius:{radius};padding:{pad};
  display:flex;flex-direction:column;align-items:center;gap:{gap};box-shadow:{shadow_v};width:90%;max-width:420px{border_css};}}
.card[data-card-id="{card_id}"] .fs-ring{{
  {ring_css}opacity:0;
}}
.card[data-card-id="{card_id}"] .fs-icon{{display:flex;align-items:center;justify-content:center;}}
.card[data-card-id="{card_id}"] .fs-headline{{font-family:{font};font-size:{head_size};font-weight:{fw};
  color:{accent};letter-spacing:0.02em;opacity:0;text-align:center;{glow_css}}}
.card[data-card-id="{card_id}"] .fs-context{{font-family:{font};font-size:{ctx_size};font-weight:500;
  color:{text_s};line-height:1.4;text-align:center;opacity:0;}}
.card[data-card-id="{card_id}"] .fs-line{{width:0;height:2px;background:{accent};border-radius:2px;margin-top:4px;}}"""

    context_html = f'<div class="fs-context" id="{card_id}-fs-context">{sale_context}</div>' if sale_context else ""

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="fs-wrap">
    <div class="fs-ring" id="{card_id}-fs-ring">
      {icon_html}
    </div>
    <div class="fs-headline" id="{card_id}-fs-headline">{_e(headline)}</div>
    {context_html}
    <div class="fs-line" id="{card_id}-fs-line"></div>
  </div>
</div>
</div>"""


# ── Render GSAP ───────────────────────────────────────────────────────────────

def _render_gsap(params: dict, pack: dict, card_id: str, start: float, end: float) -> list[str]:
    p       = pack or {}
    cid     = _ej(card_id)
    pack_id = p.get("id", "")
    accent  = p.get("accent", "#4cc9f0")

    is_cinema = pack_id == "lean_cinema"
    is_ledger = pack_id == "lean_ledger"
    is_vibe   = pack_id == "lean_vibe"
    is_glass  = pack_id == "lean_glass"
    is_craft  = pack_id == "lean_craft"

    t_in  = round(start + 0.18, 4)
    t_hl  = round(t_in + 0.25, 4)
    t_ctx = round(t_hl + 0.30, 4)
    t_ln  = round(t_ctx + 0.20, 4)

    lines: list[str] = []

    # Ring appears
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-fs-ring',{{opacity:1,duration:0.80,ease:'power2.in'}},{t_in:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-fs-ring',{{opacity:0,scale:0.5}},{{opacity:1,scale:1,duration:0.40,ease:'back.out(2.0)'}},{t_in:.4f});")
    elif is_craft:
        lines.append(f"  tl.fromTo('#{cid}-fs-ring',{{opacity:0,scale:0.6,rotate:-10}},{{opacity:1,scale:1,rotate:0,duration:0.38,ease:'back.out(1.6)'}},{t_in:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-fs-ring',{{opacity:0,scale:0.7}},{{opacity:1,scale:1,duration:0.30,ease:'power2.out'}},{t_in:.4f});")

    # Glow burst on glass
    if is_glass:
        t_burst = round(t_in + 0.30, 4)
        safe_accent = _ej(accent)
        lines.append(
            f"  tl.to('#{cid}-fs-ring',{{boxShadow:'0 0 0 24px {safe_accent}33',duration:0.25,ease:'power2.out'}},{t_burst:.4f});"
        )
        lines.append(
            f"  tl.to('#{cid}-fs-ring',{{boxShadow:'0 0 0 0 {safe_accent}00',duration:0.40,ease:'power2.in'}},{round(t_burst+0.25,4):.4f});"
        )

    # Headline
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-fs-headline',{{opacity:1,duration:0.70,ease:'power1.in'}},{t_hl:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-fs-headline',{{opacity:0,scale:0.8}},{{opacity:1,scale:1,duration:0.35,ease:'back.out(1.5)'}},{t_hl:.4f});")
    elif is_ledger:
        lines.append(f"  tl.to('#{cid}-fs-headline',{{opacity:1,duration:0.15,ease:'none'}},{t_hl:.4f});")
    elif is_craft:
        lines.append(f"  tl.fromTo('#{cid}-fs-headline',{{opacity:0,y:-10}},{{opacity:1,y:0,duration:0.35,ease:'circ.out'}},{t_hl:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-fs-headline',{{opacity:0,y:-8}},{{opacity:1,y:0,duration:0.30,ease:'power2.out'}},{t_hl:.4f});")

    # Context (if present)
    has_context = bool(params.get("sale_context", "").strip())
    if has_context:
        lines.append(f"  tl.to('#{cid}-fs-context',{{opacity:1,duration:0.30,ease:'power1.out'}},{t_ctx:.4f});")

    # Accent line
    line_w = "60px" if is_cinema else ("48px" if is_ledger else "80px")
    lines.append(f"  tl.to('#{cid}-fs-line',{{width:'{line_w}',duration:0.40,ease:'power2.out'}},{t_ln:.4f});")

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

register(BRollType(
    name="first_sale_moment",
    patterns=_ALL_PATTERNS,
    extractor=_extractor,
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=4.5,
    preferred_zone="upper-data",
    min_confidence=0.82,
))
