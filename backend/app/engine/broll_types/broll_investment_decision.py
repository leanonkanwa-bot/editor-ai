"""
investment_decision — bold commitment card for investment/bet moments.

Triggers on explicit investment-decision language (NOT bare amounts → money_counter):
  FR: "j'ai investi 10 000€ dans", "j'ai misé tout sur", "j'ai risqué X€ pour",
      "le pari de ma vie", "j'ai tout mis sur cette formation"
  EN: "I invested $10k in", "I bet everything on", "I put all my money into",
      "best investment I ever made", "biggest bet of my career"

Visual: large bold investment_label centered with decision aesthetic.
Distinct from money_counter which shows any bare amount with animation.
"""
from __future__ import annotations

import re
from app.engine.broll_registry import BRollType, register


# ── Patterns ──────────────────────────────────────────────────────────────────

_INVEST_FR_RE = re.compile(
    r"\b(?:"
    r"(?:j'ai|on\s+a|nous\s+avons)\s+(?:investi|misé|engagé|risqué|mis)\s+"
    r"(?:[\d\s.,]+[kKmM]?\s*(?:€|\$|£|euros?|dollars?)|tout\s+(?:mon|notre|mes)|"
    r"[\d\s.,]+[kKmM]?)\s*(?:dans|sur|pour|en)|"
    r"le\s+pari\s+(?:de\s+ma\s+vie|le\s+plus\s+(?:grand|important|risqué)|que\s+j'ai\s+(?:fait|pris))|"
    r"(?:meilleur|pire)\s+investissement\s+(?:de\s+ma\s+(?:vie|carrière))|"
    r"tout\s+(?:mis|parié|risqué)\s+sur"
    r")\b",
    re.IGNORECASE,
)

_INVEST_EN_RE = re.compile(
    r"\b(?:"
    r"(?:I|we)\s+(?:invested|bet|put|risked|committed)\s+"
    r"(?:[\d\s.,]+[kKmM]?\s*(?:\$|€|£|dollars?|euros?)|everything|all\s+(?:my|our)\s+(?:money|savings))\s*"
    r"(?:in(?:to)?|on|for)|"
    r"(?:best|biggest|worst)\s+investment\s+(?:I|we)\s+(?:ever\s+)?(?:made|took)|"
    r"bet\s+(?:everything|it\s+all)\s+on|"
    r"all\s+in\s+on"
    r")\b",
    re.IGNORECASE,
)

_ALL_PATTERNS = [_INVEST_FR_RE, _INVEST_EN_RE]


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
    conf = 0.88 if match.re is _INVEST_FR_RE else 0.84
    investment_label = _ctx_words(words, word_idx, 6).strip()[:80]
    return {"investment_label": investment_label}, conf


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

    inv_label = _e(params.get("investment_label", ""))

    glow_css   = f" text-shadow:{_e(glow)};"   if glow   else ""
    glow_i_css = f" text-shadow:{_e(glow_i)};" if glow_i else ""

    # Pack-specific headline
    if pack_id == "lean_ledger":
        kicker = "INVESTMENT DECISION"
        icon   = "[ Ψ ]"
    elif pack_id == "lean_cinema":
        kicker = "Le pari"
        icon   = "◈"
    elif pack_id == "lean_vibe":
        kicker = "LE PARI !"
        icon   = "💸"
    elif pack_id == "lean_craft":
        kicker = "Mon investissement"
        icon   = "✦"
    else:
        kicker = "J'AI INVESTI"
        icon   = "→"

    # Accent bar / pulse style per pack
    if pack_id == "lean_glass":
        pulse_css = f"background:{accent}; border-radius:50%; width:48px; height:48px; display:flex; align-items:center; justify-content:center; box-shadow:0 0 0 0 {accent};"
    elif pack_id == "lean_vibe":
        pulse_css = "background:rgba(255,255,255,0.20); border-radius:50%; width:48px; height:48px; display:flex; align-items:center; justify-content:center;"
    elif pack_id == "lean_ledger":
        pulse_css = f"border:1px solid {accent}; border-radius:4px; padding:6px 12px; display:inline-block; background:transparent;"
    else:
        pulse_css = f"background:{accent}; border-radius:8px; width:48px; height:48px; display:flex; align-items:center; justify-content:center;"

    css = f"""\
.card[data-card-id="{card_id}"] .root{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;}}
.card[data-card-id="{card_id}"] .inv-wrap{{background:{bg};border-radius:{radius};padding:36px 40px;
  display:flex;flex-direction:column;align-items:center;gap:16px;box-shadow:{shadow_v};width:90%;max-width:440px;}}
.card[data-card-id="{card_id}"] .inv-pulse{{{pulse_css}opacity:0;}}
.card[data-card-id="{card_id}"] .inv-icon{{font-size:22px;color:{text_c};}}
.card[data-card-id="{card_id}"] .inv-kicker{{font-family:{font};font-size:12px;font-weight:700;
  letter-spacing:0.18em;text-transform:uppercase;color:{text_s};opacity:0;}}
.card[data-card-id="{card_id}"] .inv-amount{{font-family:{font};font-size:30px;font-weight:{fw};
  color:{accent};text-align:center;line-height:1.3;opacity:0;{glow_i_css}}}
.card[data-card-id="{card_id}"] .inv-line{{width:0;height:3px;background:{accent};border-radius:2px;{glow_css}}}"""

    label_html = f'<div class="inv-amount" id="{card_id}-inv-amount">{inv_label}</div>' if inv_label else ""

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="inv-wrap">
    <div class="inv-pulse" id="{card_id}-inv-pulse">
      <span class="inv-icon">{_e(icon)}</span>
    </div>
    <div class="inv-kicker" id="{card_id}-inv-kicker">{_e(kicker)}</div>
    {label_html}
    <div class="inv-line" id="{card_id}-inv-line"></div>
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

    t_in    = round(start + 0.18, 4)
    t_kick  = round(t_in + 0.25, 4)
    t_amt   = round(t_kick + 0.22, 4)
    t_ln    = round(t_amt + 0.35, 4)

    lines: list[str] = []

    # Pulse icon
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-inv-pulse',{{opacity:1,duration:0.80,ease:'power1.in'}},{t_in:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-inv-pulse',{{opacity:0,scale:0.5}},{{opacity:1,scale:1,duration:0.40,ease:'back.out(2.0)'}},{t_in:.4f});")
    elif is_ledger:
        lines.append(f"  tl.to('#{cid}-inv-pulse',{{opacity:1,duration:0.15,ease:'none'}},{t_in:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-inv-pulse',{{opacity:0,scale:0.7}},{{opacity:1,scale:1,duration:0.30,ease:'power2.out'}},{t_in:.4f});")

    # Glass: glow ring pulse
    if is_glass:
        t_burst = round(t_in + 0.28, 4)
        lines.append(f"  tl.to('#{cid}-inv-pulse',{{boxShadow:'0 0 0 20px {accent}33',duration:0.22,ease:'power2.out'}},{t_burst:.4f});")
        lines.append(f"  tl.to('#{cid}-inv-pulse',{{boxShadow:'0 0 0 0 {accent}00',duration:0.38,ease:'power2.in'}},{round(t_burst+0.22,4):.4f});")

    # Kicker label
    if is_ledger:
        lines.append(f"  tl.to('#{cid}-inv-kicker',{{opacity:1,duration:0.15,ease:'none'}},{t_kick:.4f});")
    else:
        d_k = 0.70 if is_cinema else 0.25
        lines.append(f"  tl.to('#{cid}-inv-kicker',{{opacity:1,duration:{d_k},ease:'power1.out'}},{t_kick:.4f});")

    # Amount label
    has_amount = bool(params.get("investment_label", "").strip())
    if has_amount:
        if is_cinema:
            lines.append(f"  tl.to('#{cid}-inv-amount',{{opacity:1,duration:0.70,ease:'power2.in'}},{t_amt:.4f});")
        elif is_vibe:
            lines.append(f"  tl.fromTo('#{cid}-inv-amount',{{opacity:0,scale:0.85}},{{opacity:1,scale:1,duration:0.35,ease:'back.out(1.4)'}},{t_amt:.4f});")
        else:
            lines.append(f"  tl.fromTo('#{cid}-inv-amount',{{opacity:0,y:6}},{{opacity:1,y:0,duration:0.30,ease:'power2.out'}},{t_amt:.4f});")

    # Line
    line_w = "56px" if is_cinema else ("40px" if is_ledger else "72px")
    lines.append(f"  tl.to('#{cid}-inv-line',{{width:'{line_w}',duration:0.40,ease:'power2.out'}},{t_ln:.4f});")

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

register(BRollType(
    name="investment_decision",
    patterns=_ALL_PATTERNS,
    extractor=_extractor,
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=4.5,
    preferred_zone="upper-data",
    min_confidence=0.82,
))
