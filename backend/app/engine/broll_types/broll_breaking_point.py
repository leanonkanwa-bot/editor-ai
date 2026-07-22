"""
breaking_point — stress-and-snap scene for rock-bottom / breaking-point moments.

Triggers on:
  FR: "j'en pouvais plus", "point de rupture", "touché le fond", "moment de rupture",
      "j'ai tout arrêté", "tout s'est effondré", "à bout", "épuisé / burnout"
  EN: "breaking point", "hit rock bottom", "burned out completely",
      "couldn't take it anymore", "everything fell apart", "rock bottom"

Visual: progress bar fills to 100% (red), then "snaps" → resets to accent color
indicating the turning point. breaking_context text below.
"""
from __future__ import annotations

import re
from app.engine.broll_registry import BRollType, register


# ── Patterns ──────────────────────────────────────────────────────────────────

_RUPTURE_FR_RE = re.compile(
    r"\b(?:"
    r"(?:j[''])?en\s+(?:pouvais|pouvait|pouvions)\s+plus|"
    r"point\s+de\s+rupture|"
    r"touché\s+le\s+fond|"
    r"moment\s+(?:de\s+)?(?:rupture|bascule|tout\s+a\s+basculé)|"
    r"tout\s+s'est\s+(?:effondré|écroulé|arrêté)|"
    r"(?:épuisement|burnout)\s+(?:total|complet)|"
    r"(?:j['']ai|on\s+a)\s+tout\s+(?:arrêté|lâché|abandonné)\s+(?:du\s+jour\s+au\s+lendemain|subitement)|"
    r"à\s+bout\s+(?:de\s+(?:souffle|forces?|nerfs?)|totalement)"
    r")\b",
    re.IGNORECASE,
)

_RUPTURE_EN_RE = re.compile(
    r"\b(?:"
    r"breaking\s+point|"
    r"hit\s+(?:my\s+|our\s+)?rock\s+bottom|"
    r"burned?\s+out\s+(?:completely|totally|badly)|"
    r"couldn'?t\s+take\s+it\s+(?:anymore|any\s+longer)|"
    r"everything\s+(?:fell?\s+apart|collapsed|broke\s+down)|"
    r"rock\s+bottom\s+(?:moment|point|experience)|"
    r"completely\s+(?:burned?\s+out|exhausted|overwhelmed)"
    r")\b",
    re.IGNORECASE,
)

_ALL_PATTERNS = [_RUPTURE_FR_RE, _RUPTURE_EN_RE]


def _ctx_words(words, idx: int, radius: int = 7) -> str:
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
    conf = 0.90 if match.re is _RUPTURE_FR_RE else 0.86
    breaking_context = _ctx_words(words, word_idx, 6).strip()[:80]
    return {"breaking_context": breaking_context}, conf


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
    glow_i   = p.get("title_glow_intense", "")
    pack_id  = p.get("id",             "")

    ctx = _e(params.get("breaking_context", ""))

    # Pack-specific stress / recovery color
    if pack_id == "lean_paper":
        stress_color   = "#e53e3e"
        recovery_color = accent
        track_bg       = "rgba(0,0,0,0.08)"
    elif pack_id == "lean_vibe":
        stress_color   = "#ff3b3b"
        recovery_color = accent
        track_bg       = "rgba(255,255,255,0.20)"
    elif pack_id == "lean_ledger":
        stress_color   = "#ff4444"
        recovery_color = accent
        track_bg       = "rgba(0,200,150,0.10)"
    elif pack_id == "lean_craft":
        stress_color   = "#c0392b"
        recovery_color = accent
        track_bg       = "rgba(61,43,31,0.12)"
    elif pack_id == "lean_cinema":
        stress_color   = "#8b0000"
        recovery_color = accent
        track_bg       = "rgba(245,240,232,0.08)"
    else:  # glass
        stress_color   = "#ff4d4d"
        recovery_color = accent
        track_bg       = "rgba(255,255,255,0.08)"

    glow_i_css = f" text-shadow:{_e(glow_i)};" if glow_i else ""

    if pack_id == "lean_ledger":
        headline = "BREAKING POINT"
        recovery = "RECOVERY"
    elif pack_id in ("lean_craft", "lean_cinema"):
        headline = "Point de rupture"
        recovery = "Puis tout a changé."
    else:
        headline = "POINT DE RUPTURE"
        recovery = "PUIS TOUT A CHANGÉ."

    css = f"""\
.card[data-card-id="{card_id}"] .root{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;}}
.card[data-card-id="{card_id}"] .bp-wrap{{background:{bg};border-radius:{radius};padding:32px 40px;
  display:flex;flex-direction:column;gap:18px;box-shadow:{shadow_v};width:90%;max-width:440px;}}
.card[data-card-id="{card_id}"] .bp-headline{{font-family:{font};font-size:26px;font-weight:{fw};
  color:{text_c};letter-spacing:0.02em;opacity:0;{glow_i_css}}}
.card[data-card-id="{card_id}"] .bp-context{{font-family:{font};font-size:17px;font-weight:500;
  color:{text_s};line-height:1.4;opacity:0;}}
.card[data-card-id="{card_id}"] .bp-track{{width:100%;height:8px;background:{track_bg};border-radius:99px;overflow:hidden;}}
.card[data-card-id="{card_id}"] .bp-bar{{height:100%;width:0%;background:{stress_color};border-radius:99px;
  transform-origin:left center;}}
.card[data-card-id="{card_id}"] .bp-recovery{{font-family:{font};font-size:16px;font-weight:{fw};
  color:{accent};letter-spacing:0.06em;text-transform:uppercase;opacity:0;{glow_i_css}}}"""

    recovery_color_val = recovery_color  # used in GSAP

    ctx_html = f'<div class="bp-context" id="{card_id}-bp-context">{ctx}</div>' if ctx else ""

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="bp-wrap">
    <div class="bp-headline" id="{card_id}-bp-headline">{_e(headline)}</div>
    {ctx_html}
    <div class="bp-track">
      <div class="bp-bar" id="{card_id}-bp-bar"></div>
    </div>
    <div class="bp-recovery" id="{card_id}-bp-recovery">{_e(recovery)}</div>
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

    # Stress color per pack
    stress = "#8b0000" if is_cinema else ("#ff4d4d" if pack_id == "lean_glass" else "#ff3b3b")

    t_in     = round(start + 0.18, 4)
    t_ctx    = round(t_in + 0.22, 4)
    t_fill   = round(t_ctx + 0.25, 4)
    t_snap   = round(t_fill + 1.20, 4)  # bar fills over ~1.2s then snaps
    t_reset  = round(t_snap + 0.12, 4)
    t_recov  = round(t_reset + 0.30, 4)

    dur_total = max(0.5, end - start)
    fill_dur  = round(min(1.40, max(0.80, dur_total * 0.35)), 3)
    t_snap    = round(t_fill + fill_dur, 4)
    t_reset   = round(t_snap + 0.12, 4)
    t_recov   = round(t_reset + 0.25, 4)

    lines: list[str] = []

    # Headline
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-bp-headline',{{opacity:1,duration:0.80,ease:'power1.in'}},{t_in:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-bp-headline',{{opacity:0,x:-10}},{{opacity:1,x:0,duration:0.30,ease:'power2.out'}},{t_in:.4f});")
    elif is_ledger:
        lines.append(f"  tl.to('#{cid}-bp-headline',{{opacity:1,duration:0.15,ease:'none'}},{t_in:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-bp-headline',{{opacity:0,y:-6}},{{opacity:1,y:0,duration:0.28,ease:'power2.out'}},{t_in:.4f});")

    # Context
    has_context = bool(params.get("breaking_context", "").strip())
    if has_context:
        lines.append(f"  tl.to('#{cid}-bp-context',{{opacity:1,duration:0.25,ease:'power1.out'}},{t_ctx:.4f});")

    # Bar fills to 100% (stress color) — accent/ease varies by pack
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-bp-bar',{{width:'100%',duration:{fill_dur:.3f},ease:'power1.in'}},{t_fill:.4f});")
    elif is_vibe:
        lines.append(f"  tl.to('#{cid}-bp-bar',{{width:'100%',duration:{fill_dur:.3f},ease:'power2.in'}},{t_fill:.4f});")
    elif is_ledger:
        lines.append(f"  tl.to('#{cid}-bp-bar',{{width:'100%',duration:{fill_dur:.3f},ease:'none'}},{t_fill:.4f});")
    else:
        lines.append(f"  tl.to('#{cid}-bp-bar',{{width:'100%',duration:{fill_dur:.3f},ease:'power2.in'}},{t_fill:.4f});")

    # Snap: quick width reset to 0, color changes to accent
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-bp-bar',{{width:'0%',duration:0.08,ease:'none'}},{t_snap:.4f});")
        lines.append(f"  tl.to('#{cid}-bp-bar',{{width:'35%',background:'{accent}',duration:0.50,ease:'power2.out'}},{t_reset:.4f});")
    else:
        lines.append(f"  tl.to('#{cid}-bp-bar',{{width:'0%',duration:0.05,ease:'none'}},{t_snap:.4f});")
        lines.append(f"  tl.to('#{cid}-bp-bar',{{width:'35%',background:'{accent}',duration:0.35,ease:'power2.out'}},{t_reset:.4f});")

    # Recovery label
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-bp-recovery',{{opacity:1,duration:0.60,ease:'power1.in'}},{t_recov:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-bp-recovery',{{opacity:0,scale:0.85}},{{opacity:1,scale:1,duration:0.35,ease:'back.out(1.5)'}},{t_recov:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-bp-recovery',{{opacity:0,y:6}},{{opacity:1,y:0,duration:0.28,ease:'power2.out'}},{t_recov:.4f});")

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

register(BRollType(
    name="breaking_point",
    patterns=_ALL_PATTERNS,
    extractor=_extractor,
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=5.5,
    preferred_zone="upper-data",
    min_confidence=0.84,
))
