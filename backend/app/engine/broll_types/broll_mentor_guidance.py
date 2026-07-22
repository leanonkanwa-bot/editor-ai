"""
mentor_guidance — two-bubble layout showing mentor → mentee teaching moment.

Triggers on:
  FR: "mon mentor X m'a dit", "ma coach m'a appris", "grâce à mon mentor",
      "ma coach me répétait sans cesse", "mon prof m'a montré",
      "il/elle m'a enseigné que", "j'ai eu un mentor qui"
  EN: "my mentor told me", "my coach taught me", "thanks to my mentor",
      "she/he showed me", "my mentor said", "guided by my mentor"

Visual: mentor bubble (top) → animated connecting line → mentee bubble (bottom).
mentor_label: who guided; mentee_label: what was learned / outcome.
"""
from __future__ import annotations

import re
from app.engine.broll_registry import BRollType, register


# ── Patterns ──────────────────────────────────────────────────────────────────

_MENTOR_FR_RE = re.compile(
    r"\b(?:"
    r"(?:mon|ma)\s+(?:mentor|coach|formateur|prof(?:esseur)?|mentor(?:e)?)\s+"
    r"(?:m'a\s+(?:dit|appris|enseigné|montré|conseillé|aidé)|"
    r"(?:me|nous)\s+(?:répétait|disait|enseignait|a\s+(?:dit|appris|montré|conseillé)))|"
    r"grâce\s+à\s+(?:mon|ma)\s+(?:mentor|coach|formateur)|"
    r"j'ai\s+eu\s+(?:un|une)\s+(?:mentor|coach|formateur)\s+(?:qui|que)|"
    r"(?:il|elle)\s+m'a\s+(?:appris|enseigné|montré|dit)\s+(?:que|comment|à)"
    r")\b",
    re.IGNORECASE,
)

_MENTOR_EN_RE = re.compile(
    r"\b(?:"
    r"my\s+(?:mentor|coach|trainer|teacher|guide)\s+"
    r"(?:told|taught|showed|said|helped|guided|advised)\s+me|"
    r"thanks?\s+to\s+(?:my\s+)?(?:mentor|coach)|"
    r"(?:she|he|they)\s+(?:taught|showed|told)\s+me\s+(?:that|how|to)|"
    r"mentored\s+by\s+(?:\w+\s+){0,3}(?:who|that)|"
    r"had\s+a\s+(?:mentor|coach)\s+who\s+(?:told|showed|taught)"
    r")\b",
    re.IGNORECASE,
)

_ALL_PATTERNS = [_MENTOR_FR_RE, _MENTOR_EN_RE]


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
    conf = 0.88 if match.re is _MENTOR_FR_RE else 0.84

    # mentor_label: extract a word or two after the keyword
    n = len(words)
    end = min(n, word_idx + 6)
    mentor_fragment = " ".join(getattr(words[i], "text", "") for i in range(word_idx, end)).strip()

    # mentee_label: short window from context
    ctx = _ctx_words(words, word_idx, 8).strip()

    return {
        "mentor_label": mentor_fragment[:60],
        "mentee_label": ctx[:60],
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
    pack_id  = p.get("id",             "")

    mentor_raw = params.get("mentor_label", "")
    mentee_raw = params.get("mentee_label", "")

    glow_css = f" text-shadow:{_e(glow)};" if glow else ""

    # Pack-specific bubble styles
    if pack_id == "lean_glass":
        bubble_mentor_bg = "rgba(76,201,240,0.12)"
        bubble_mentee_bg = "rgba(255,255,255,0.05)"
        connector_style  = f"border-left:2px solid {accent}; margin-left:20px; height:32px; opacity:0;"
    elif pack_id == "lean_paper":
        bubble_mentor_bg = "rgba(79,107,255,0.08)"
        bubble_mentee_bg = "rgba(0,0,0,0.04)"
        connector_style  = f"border-left:2px solid {accent}; margin-left:20px; height:32px; opacity:0;"
    elif pack_id == "lean_vibe":
        bubble_mentor_bg = "rgba(255,255,255,0.20)"
        bubble_mentee_bg = "rgba(255,255,255,0.12)"
        connector_style  = f"border-left:3px solid {accent}; margin-left:20px; height:32px; opacity:0; border-radius:2px;"
    elif pack_id == "lean_ledger":
        bubble_mentor_bg = "rgba(0,200,150,0.12)"
        bubble_mentee_bg = "rgba(0,200,150,0.06)"
        connector_style  = f"border-left:1px solid {accent}; margin-left:16px; height:28px; opacity:0;"
    elif pack_id == "lean_craft":
        bubble_mentor_bg = "rgba(217,119,87,0.12)"
        bubble_mentee_bg = "rgba(61,43,31,0.06)"
        connector_style  = f"border-left:2px dashed {accent}; margin-left:18px; height:30px; opacity:0;"
    else:  # cinema
        bubble_mentor_bg = "rgba(201,168,106,0.10)"
        bubble_mentee_bg = "rgba(245,240,232,0.04)"
        connector_style  = f"border-left:1px solid {accent}; margin-left:16px; height:28px; opacity:0;"

    bubble_radius = "8px" if pack_id == "lean_ledger" else ("0px" if pack_id == "lean_cinema" else "12px")

    # Header labels
    if pack_id == "lean_ledger":
        lbl_mentor = "MENTOR"
        lbl_mentee = "CE QUE J'AI APPRIS"
    elif pack_id in ("lean_craft", "lean_cinema"):
        lbl_mentor = "Mon mentor"
        lbl_mentee = "Ce qu'il m'a appris"
    else:
        lbl_mentor = "MON MENTOR"
        lbl_mentee = "CE QU'IL M'A APPRIS"

    mentor_text = _e(mentor_raw) if mentor_raw else "Mon mentor me disait…"
    mentee_text = _e(mentee_raw) if mentee_raw else "Et ça a tout changé."

    css = f"""\
.card[data-card-id="{card_id}"] .root{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;}}
.card[data-card-id="{card_id}"] .mg-wrap{{background:{bg};border-radius:{radius};padding:28px 36px;
  display:flex;flex-direction:column;gap:0;box-shadow:{shadow_v};width:90%;max-width:440px;}}
.card[data-card-id="{card_id}"] .mg-section-label{{font-family:{font};font-size:10px;font-weight:700;
  letter-spacing:0.18em;text-transform:uppercase;color:{text_s};margin-bottom:6px;opacity:0;}}
.card[data-card-id="{card_id}"] .mg-bubble{{background:{bubble_mentor_bg};border-radius:{bubble_radius};
  padding:14px 18px;opacity:0;}}
.card[data-card-id="{card_id}"] .mg-bubble-mentee{{background:{bubble_mentee_bg};border-radius:{bubble_radius};
  padding:14px 18px;opacity:0;}}
.card[data-card-id="{card_id}"] .mg-text{{font-family:{font};font-size:18px;font-weight:{fw};
  color:{text_c};line-height:1.35;{glow_css}}}
.card[data-card-id="{card_id}"] .mg-text-mentee{{font-family:{font};font-size:18px;font-weight:600;
  color:{accent};line-height:1.35;}}
.card[data-card-id="{card_id}"] .mg-connector{{{connector_style}}}"""

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="mg-wrap">
    <div class="mg-section-label" id="{card_id}-mg-mentor-label">{_e(lbl_mentor)}</div>
    <div class="mg-bubble" id="{card_id}-mg-mentor">
      <div class="mg-text">{mentor_text}</div>
    </div>
    <div class="mg-connector" id="{card_id}-mg-connector"></div>
    <div class="mg-section-label" id="{card_id}-mg-mentee-label">{_e(lbl_mentee)}</div>
    <div class="mg-bubble-mentee" id="{card_id}-mg-mentee">
      <div class="mg-text-mentee">{mentee_text}</div>
    </div>
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

    t_in   = round(start + 0.18, 4)
    t_m1   = t_in
    t_m2   = round(t_in + 0.15, 4)
    t_conn = round(t_m2 + 0.30, 4)
    t_a1   = round(t_conn + 0.28, 4)
    t_a2   = round(t_a1 + 0.15, 4)

    d_label = 0.60 if is_cinema else 0.22
    ease    = "none" if is_ledger else "power2.out"

    lines: list[str] = []

    # Mentor section label
    lines.append(f"  tl.to('#{cid}-mg-mentor-label',{{opacity:1,duration:{d_label},ease:'{ease}'}},{t_m1:.4f});")

    # Mentor bubble
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-mg-mentor',{{opacity:1,duration:0.70,ease:'power1.in'}},{t_m2:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-mg-mentor',{{opacity:0,x:-12}},{{opacity:1,x:0,duration:0.30,ease:'back.out(1.4)'}},{t_m2:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-mg-mentor',{{opacity:0,y:6}},{{opacity:1,y:0,duration:0.25,ease:'power2.out'}},{t_m2:.4f});")

    # Connector line grows downward
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-mg-connector',{{opacity:1,height:'28px',duration:0.60,ease:'power1.in'}},{t_conn:.4f});")
    elif is_ledger:
        lines.append(f"  tl.to('#{cid}-mg-connector',{{opacity:1,duration:0.15,ease:'none'}},{t_conn:.4f});")
    else:
        lines.append(f"  tl.to('#{cid}-mg-connector',{{opacity:1,height:'32px',duration:0.30,ease:'power1.out'}},{t_conn:.4f});")

    # Mentee section label
    lines.append(f"  tl.to('#{cid}-mg-mentee-label',{{opacity:1,duration:{d_label},ease:'{ease}'}},{t_a1:.4f});")

    # Mentee bubble
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-mg-mentee',{{opacity:1,duration:0.70,ease:'power2.in'}},{t_a2:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-mg-mentee',{{opacity:0,x:12}},{{opacity:1,x:0,duration:0.30,ease:'back.out(1.4)'}},{t_a2:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-mg-mentee',{{opacity:0,y:6}},{{opacity:1,y:0,duration:0.28,ease:'power2.out'}},{t_a2:.4f});")

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

register(BRollType(
    name="mentor_guidance",
    patterns=_ALL_PATTERNS,
    extractor=_extractor,
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=5.0,
    preferred_zone="upper-data",
    min_confidence=0.82,
))
