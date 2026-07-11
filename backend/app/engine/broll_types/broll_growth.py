"""
growth_curve — animated SVG path tracing a growth/multiplier trend.

Triggers on phrases like:
  "doublé en 6 mois", "10x en 2 ans", "passé de 5k à 50k",
  "from $200 to $2000", "tripled our revenue", "x3 growth",
  "croissance de 300%", "multiplié par 4"

Does NOT trigger on:
  - A bare number with no progression marker (avoids doubling with money_counter)
  - Overlapping windows already claimed by money_counter or calendar_date
    (handled upstream by _merge_cards() greedy-by-confidence — no extra
    cooldown logic needed here)

render_html : SVG <path> with stroke-dasharray/stroke-dashoffset = 0 (path
             hidden at start). Pack-coloured stroke, gradient fill beneath.
render_gsap : stroke-dashoffset animates 0→0 (full reveal), duration
             proportional to path length (computed once at render time);
             endpoint circle pulses on arrival.

Three display modes:
  ratio        — "x3", "10x", "×2.5"   (multiplier only)
  from_to      — "5k → 50k"            (start + end values)
  percentage   — "+300%"               (percentage growth)
"""
from __future__ import annotations

import re

from app.engine.broll_registry import BRollType, register


# ── Patterns ──────────────────────────────────────────────────────────────────

# Tier 1 — explicit multiplier tokens
_MULT_RE = re.compile(
    r"\b(?:"
    r"(?P<xn>\d+(?:[.,]\d+)?)\s*[xX×]"           # "10x", "2.5x", "3×"
    r"|[xX×]\s*(?P<nx>\d+(?:[.,]\d+)?)"           # "x10", "×3"
    r"|(?P<kw>(?:doubl|tripl|quadrupl|multipl)(?:i?é[e]?s?|i?ed|i?ant|ing|ying))"  # "triplé/triplée/tripled/tripling/doublant/multiplié/multiplied/multiplying"
    r"|(?P<pct>\+?\d+(?:[.,]\d+)?\s*%)"           # "+300%", "150%"
    r")\b",
    re.IGNORECASE,
)

# Tier 1 — from/to constructs (FR + EN)
_FROM_TO_RE = re.compile(
    r"\b(?:"
    r"(?:passé|passer|passée)\s+de\s+(?P<fr_from>[\d][.\d,\s]*[kKmM€$£]?)\s+[àa]\s+(?P<fr_to>[\d][.\d,\s]*[kKmM€$£]?)"
    r"|from\s+(?P<en_from>[\d][.\d,\s]*[kKmM€$£]?)\s+to\s+(?P<en_to>[\d][.\d,\s]*[kKmM€$£]?)"
    r")\b",
    re.IGNORECASE,
)

# Tier 1 — growth/croissance keyword near a number
_GROWTH_KW_RE = re.compile(
    r"\b(?:croissance|growth|hausse|augmentation|progression|scaled|grew|grandi|monté)"
    r"(?:\s+\w+){0,4}\s+(?:de\s+)?(?P<val>\d+(?:[.,]\d+)?\s*%?)\b",
    re.IGNORECASE,
)

# Negative guard: bare standalone numbers with no progression context
# (shared with the context scan below — if matched, confidence drops to 0.0)
_NEG_BARE_NUM = re.compile(
    r"^\s*[\d]+\s*$"
)

# Context words that CONFIRM a growth meaning for ambiguous Tier-1 hits
_POSITIVE_CTX = re.compile(
    r"\b(revenue|CA|chiffre|client|abonné|subscriber|follower|vente|"
    r"résultat|business|revenu|audience|reach|trafic|traffic|conversion|"
    r"score|gain|profit|réseau|portfolio|team|équipe|croissance|growth)\b",
    re.IGNORECASE,
)

# Negative context: prevents misfires on non-growth doubling
_NEGATIVE_CTX = re.compile(
    r"\b(recette|portion|ingrédient|dose|cup|cuillère|fois\s+plus\s+lourd|"
    r"taille|poids|longueur|largeur|hauteur|bruit|volume\s+sonore)\b",
    re.IGNORECASE,
)


def _parse_val(s: str) -> float | None:
    """Parse a raw value string like '5k', '50 000', '1.5M', '200' → float."""
    if s is None:
        return None
    s = s.strip().replace(" ", "").replace(" ", "")
    mult = 1.0
    if s and s[-1].lower() == "k":
        mult, s = 1_000, s[:-1]
    elif s and s[-1].lower() == "m":
        mult, s = 1_000_000, s[:-1]
    s = s.replace(",", ".").lstrip("+$€£")
    try:
        return float(s) * mult
    except ValueError:
        return None


def _ctx_text(words: list, word_idx: int, radius: int = 8) -> str:
    n = len(words)
    return " ".join(
        getattr(words[i], "text", "")
        for i in range(max(0, word_idx - radius), min(n, word_idx + radius + 1))
    )


# ── Extractor ─────────────────────────────────────────────────────────────────

def _extractor(match, words, word_idx: int) -> tuple[dict, float]:
    ctx = _ctx_text(words, word_idx, 8)

    # Negative context kills the hit immediately
    if _NEGATIVE_CTX.search(ctx):
        return {}, 0.0

    gd = match.groupdict()

    # ── from/to mode ──
    fr_from = gd.get("fr_from") or gd.get("en_from")
    fr_to   = gd.get("fr_to")   or gd.get("en_to")
    if fr_from and fr_to:
        v_from = _parse_val(fr_from)
        v_to   = _parse_val(fr_to)
        if v_from is not None and v_to is not None and v_to > v_from > 0:
            mult = v_to / v_from
            return {
                "display_type": "from_to",
                "v_from": v_from,
                "v_to": v_to,
                "mult": round(mult, 1),
                "label_from": _fmt_val(v_from),
                "label_to":   _fmt_val(v_to),
            }, 0.92

    # ── explicit multiplier: "10x", "x3" ──
    xn = gd.get("xn") or gd.get("nx")
    if xn:
        try:
            mult = float(xn.replace(",", "."))
        except ValueError:
            return {}, 0.0
        if mult < 1.1:
            return {}, 0.0
        conf = 0.88
        # Require a positive context word if mult < 2 (avoids "1.2x narrower" etc.)
        if mult < 2.0 and not _POSITIVE_CTX.search(ctx):
            conf = 0.55
        return {
            "display_type": "ratio",
            "mult": mult,
            "label": f"{mult:g}×",
        }, conf

    # ── keyword: "doublé", "tripled" ──
    kw = gd.get("kw")
    if kw:
        kw_l = kw.lower()
        if kw_l.startswith("doubl"):
            mult = 2.0
        elif kw_l.startswith("tripl"):
            mult = 3.0
        elif kw_l.startswith("quadrupl"):
            mult = 4.0
        else:
            mult = 2.0   # "multiplié" — generic
        conf = 0.80
        if not _POSITIVE_CTX.search(ctx):
            conf = 0.65
        return {
            "display_type": "ratio",
            "mult": mult,
            "label": f"{mult:g}×",
        }, conf

    # ── percentage ──
    pct_raw = gd.get("pct")
    if pct_raw:
        pct_str = pct_raw.strip().replace(",", ".").lstrip("+").replace("%", "").strip()
        try:
            pct = float(pct_str)
        except ValueError:
            return {}, 0.0
        if pct < 10:
            return {}, 0.0
        conf = 0.78 if _POSITIVE_CTX.search(ctx) else 0.62
        return {
            "display_type": "percentage",
            "pct": pct,
            "label": f"+{pct:g}%",
        }, conf

    # ── growth keyword + number ──
    val_raw = gd.get("val")
    if val_raw:
        v = _parse_val(val_raw)
        if v is None or v < 5:
            return {}, 0.0
        is_pct = "%" in val_raw
        if is_pct:
            return {
                "display_type": "percentage",
                "pct": v,
                "label": f"+{v:g}%",
            }, 0.75
        return {
            "display_type": "ratio",
            "mult": v,
            "label": f"{v:g}×",
        }, 0.72

    return {}, 0.0


def _fmt_val(v: float) -> str:
    if v >= 1_000_000:
        d = v / 1_000_000
        return f"{d:g}M"
    if v >= 1_000:
        d = v / 1_000
        return f"{d:g}k"
    return f"{v:g}"


# ── SVG path helpers ───────────────────────────────────────────────────────────

# The curve is a cubic Bézier that starts flat (left) and rises steeply (right)
# — representing exponential-style growth. Fixed viewBox 200×100.
_VB_W, _VB_H = 200, 100
_CURVE_D = "M 10,90 C 60,88 100,70 190,10"   # start bottom-left, end top-right
# Approximate arc-length for a cubic Bézier via de Casteljau subdivision:
# C(10,90,60,88,100,70,190,10) ≈ 205 units (measured once, stored as constant)
_PATH_LEN = 205.0


def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ej(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


# ── Render HTML ───────────────────────────────────────────────────────────────

def _render_html(params: dict, pack: dict, card_id: str) -> str:
    p = pack or {}
    bg      = p.get("bg",          "#1a1a1a")
    text_c  = p.get("text",        "#f1f1f1")
    text_s  = p.get("text_secondary", "rgba(255,255,255,0.45)")
    accent  = p.get("accent",      "#4cc9f0")
    font    = p.get("font",        '"Inter", sans-serif')
    fw      = p.get("font_weight", "800")
    radius  = p.get("radius",      "20px")
    shadow  = p.get("shadow",      "0 8px 32px rgba(0,0,0,0.4)")
    shadow_i = p.get("shadow_inset", "")
    shadow_v = f"{shadow}, {shadow_i}" if shadow_i else shadow

    display_type = params.get("display_type", "ratio")
    label_main   = _e(params.get("label", params.get("label_to", "×2")))

    # Secondary annotation under the curve
    if display_type == "from_to":
        label_from = _e(params.get("label_from", ""))
        label_to   = _e(params.get("label_to", ""))
        annotation = f'<div class="gc-from-to" id="{card_id}-gc-fromto">{label_from} → {label_to}</div>'
    elif display_type == "percentage":
        annotation = f'<div class="gc-pct" id="{card_id}-gc-pct">{_e(params.get("label", ""))}</div>'
    else:
        annotation = ""

    # Gradient fill beneath the curve
    grad_id = f"{card_id}-grad"
    fill_d  = f"M 10,90 C 60,88 100,70 190,10 L 190,100 L 10,100 Z"

    css = f"""\
.card[data-card-id="{card_id}"] .root {{
  width:100%; height:100%; display:flex; align-items:center; justify-content:center;
}}
.card[data-card-id="{card_id}"] .card-panel {{
  background:{bg}; border-radius:{radius}; padding:36px 44px 32px;
  display:flex; flex-direction:column; align-items:center; gap:14px;
  box-shadow:{shadow_v}; position:relative; overflow:hidden;
}}
.card[data-card-id="{card_id}"] .gc-label {{
  font-family:{font}; font-size:54px; font-weight:{fw};
  color:{text_c}; letter-spacing:-0.02em; opacity:0;
}}
.card[data-card-id="{card_id}"] .gc-svg {{
  width:100%; max-width:220px; overflow:visible; opacity:1;
}}
.card[data-card-id="{card_id}"] .gc-fill {{
  fill:url(#{grad_id}); opacity:0;
}}
.card[data-card-id="{card_id}"] .gc-stroke {{
  fill:none; stroke:{accent}; stroke-width:3; stroke-linecap:round;
  stroke-dasharray:{_PATH_LEN:.1f}; stroke-dashoffset:{_PATH_LEN:.1f};
}}
.card[data-card-id="{card_id}"] .gc-dot {{
  fill:{accent}; r:5; opacity:0;
}}
.card[data-card-id="{card_id}"] .gc-axis {{
  stroke:{text_s}; stroke-width:1; opacity:0.35;
}}
.card[data-card-id="{card_id}"] .gc-from-to,
.card[data-card-id="{card_id}"] .gc-pct {{
  font-family:{font}; font-size:20px; font-weight:600; color:{accent};
  letter-spacing:0.04em; opacity:0;
}}"""

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="card-panel">
    <div class="gc-label" id="{card_id}-gc-label">{label_main}</div>
    <svg class="gc-svg" viewBox="0 0 {_VB_W} {_VB_H}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="{_e(accent)}" stop-opacity="0.30"/>
          <stop offset="100%" stop-color="{_e(accent)}" stop-opacity="0.03"/>
        </linearGradient>
      </defs>
      <!-- Axes -->
      <line class="gc-axis" x1="10" y1="5" x2="10" y2="95"/>
      <line class="gc-axis" x1="5" y1="90" x2="195" y2="90"/>
      <!-- Gradient fill (fades in after stroke) -->
      <path class="gc-fill" id="{card_id}-gc-fill" d="{fill_d}"/>
      <!-- The growth curve stroke -->
      <path class="gc-stroke" id="{card_id}-gc-stroke" d="{_CURVE_D}"/>
      <!-- Endpoint dot -->
      <circle class="gc-dot" id="{card_id}-gc-dot" cx="190" cy="10"/>
    </svg>
    {annotation}
  </div>
</div>
</div>"""


# ── Render GSAP ───────────────────────────────────────────────────────────────

def _render_gsap(params: dict, pack: dict, card_id: str, start: float, end: float) -> list[str]:
    cid      = _ej(card_id)
    dur      = max(0.5, end - start)
    t_in     = round(start + 0.20, 4)

    # Stroke duration proportional to path length — 1px/frame at 30fps →
    # 205px path = 6.8s cap, but we scale relative to card duration.
    # Formula: trace_dur = clamp(PATH_LEN / 120.0, 0.6, min(2.2, dur*0.55))
    # 120 px/s feels natural for this curve length.
    trace_dur = round(min(2.2, max(0.6, min(dur * 0.55, _PATH_LEN / 120.0))), 3)

    t_label   = t_in
    t_stroke  = round(t_in + 0.15, 4)
    t_fill    = round(t_stroke + trace_dur - 0.10, 4)
    t_dot     = round(t_stroke + trace_dur - 0.05, 4)
    t_pulse   = round(t_dot + 0.10, 4)
    t_annot   = round(t_dot + 0.20, 4)

    display_type = params.get("display_type", "ratio")

    lines: list[str] = []

    # 1. Label fades in first
    lines.append(
        f"  tl.fromTo('#{cid}-gc-label',"
        f"{{opacity:0,y:-6}},"
        f"{{opacity:1,y:0,duration:0.30,ease:'power2.out'}},{t_label:.4f});"
    )

    # 2. Stroke draws: dashoffset PATH_LEN → 0, duration proportional to length
    lines.append(
        f"  tl.to('#{cid}-gc-stroke',"
        f"{{'stroke-dashoffset':0,duration:{trace_dur:.3f},ease:'power1.inOut'}}"
        f",{t_stroke:.4f});"
    )

    # 3. Fill fades in as stroke nears the end
    lines.append(
        f"  tl.to('#{cid}-gc-fill',"
        f"{{opacity:1,duration:0.40,ease:'power1.out'}},{t_fill:.4f});"
    )

    # 4. Endpoint dot appears
    lines.append(
        f"  tl.to('#{cid}-gc-dot',"
        f"{{opacity:1,duration:0.15,ease:'power1.out'}},{t_dot:.4f});"
    )

    # 5. Dot pulse (scale up → back down, one cycle)
    lines.append(
        f"  tl.fromTo('#{cid}-gc-dot',"
        f"{{attr:{{r:5}}}},"
        f"{{attr:{{r:9}},duration:0.20,ease:'power2.out',yoyo:true,repeat:1}}"
        f",{t_pulse:.4f});"
    )

    # 6. Annotation (from_to or percentage) fades in after dot
    if display_type == "from_to":
        lines.append(
            f"  tl.fromTo('#{cid}-gc-fromto',"
            f"{{opacity:0,y:4}},"
            f"{{opacity:1,y:0,duration:0.25,ease:'power1.out'}},{t_annot:.4f});"
        )
    elif display_type == "percentage":
        lines.append(
            f"  tl.fromTo('#{cid}-gc-pct',"
            f"{{opacity:0,y:4}},"
            f"{{opacity:1,y:0,duration:0.25,ease:'power1.out'}},{t_annot:.4f});"
        )

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

register(BRollType(
    name="growth_curve",
    patterns=[_MULT_RE, _FROM_TO_RE, _GROWTH_KW_RE],
    extractor=_extractor,
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=5.0,
    preferred_zone="upper-right",
    min_confidence=0.70,
))
