"""
client_transformation — split AVANT/APRÈS narrative scene.

Triggers on personal-journey "before/after" language (FR + EN):
  "avant d'être burnout, maintenant je fais 10k/mois"
  "j'étais encore salarié il y a 6 mois"
  "transformed my life / changed my business"
  "I used to work 80h/week, now I work 20h"
  "before I started coaching I was..."

Does NOT trigger on numeric growth (→ growth_curve) or money amounts (→ money_counter).
Confidence is below growth_curve's 0.88–0.92 for pure-number from/to, so
_merge_cards() will prefer growth_curve when both fire on the same window.
"""
from __future__ import annotations

import re
from app.engine.broll_registry import BRollType, register


# ── Patterns ──────────────────────────────────────────────────────────────────

_AVANT_RE = re.compile(
    r"\b(?:"
    r"avant\s+(?:d'être|d'avoir|ça|cela|tout|de\s+(?:tout|commencer|lancer|découvrir))|"
    r"j'étais\s+(?:encore|juste|seulement|en\s+train\s+de|incapable\s+de)|"
    r"je\s+me\s+souviens\s+(?:quand|de\s+l'époque)|"
    r"à\s+l'époque\s+(?:où\s+je|c'était)|"
    r"quand\s+j'ai\s+(?:commencé|démarré|lancé\s+mon)|"
    r"il\s+y\s+a\s+\d+\s+(?:ans?|mois)\s+j'étais"
    r")\b",
    re.IGNORECASE,
)

_TRANSFORM_RE = re.compile(
    r"\b(?:"
    r"transform(?:ation|é|ée|er)\s+(?:de\s+(?:ma|mon|sa|leur|notre)\s+)?(?:vie|business|mindset|vision|façon)|"
    r"chang(?:é|er)\s+(?:ma|mon|sa|leur|notre)\s+(?:vie|business|façon\s+de)|"
    r"changed?\s+(?:my|their|our|her|his)\s+(?:life|business|mindset|approach)|"
    r"(?:I\s+)?used\s+to\s+(?:be|think|believe|work|struggle)|"
    r"before\s+I\s+(?:started|began|discovered|learned|found|built)"
    r")\b",
    re.IGNORECASE,
)

_MAINTENANT_RE = re.compile(
    r"\b(?:"
    r"maintenant\s+(?:je|c'est|mon|ma|tout)|"
    r"aujourd'hui\s+(?:je|c'est|mon|ma|tout)|"
    r"désormais\s+(?:je|c'est|mon|ma)|"
    r"now\s+(?:I(?:\s+am|\s+have|\s+run|\s+make|\s+earn)|my\s+business|everything)"
    r")\b",
    re.IGNORECASE,
)


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
    ctx = _ctx_words(words, word_idx, 8)

    if match.re is _AVANT_RE:
        conf = 0.85
        # Extract a few words after match start as "before" context
        end_w = min(len(words), word_idx + 8)
        before_state = " ".join(
            getattr(words[i], "text", "")
            for i in range(word_idx, end_w)
        ).strip()
        after_state = ""
    elif match.re is _MAINTENANT_RE:
        conf = 0.80
        before_state = ""
        end_w = min(len(words), word_idx + 8)
        after_state = " ".join(
            getattr(words[i], "text", "")
            for i in range(word_idx, end_w)
        ).strip()
    else:  # _TRANSFORM_RE
        conf = 0.82
        before_state = ctx[:50].strip()
        after_state = ""

    return {
        "before_state": before_state[:60],
        "after_state": after_state[:60],
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

    before_raw = params.get("before_state", "")
    after_raw  = params.get("after_state",  "")

    if pack_id == "lean_ledger":
        lbl_before, lbl_after = "BEFORE", "AFTER"
    elif pack_id in ("lean_craft", "lean_cinema"):
        lbl_before, lbl_after = "Avant", "Après"
    else:
        lbl_before, lbl_after = "AVANT", "APRÈS"

    if pack_id == "lean_vibe":
        after_bg = "background:rgba(255,255,255,0.18); border-radius:12px; padding:12px 16px;"
        divider_extra = "border-radius:99px; transform:scaleX(0);"
    elif pack_id == "lean_ledger":
        after_bg = "background:rgba(0,200,150,0.08); border-radius:4px; padding:10px 14px;"
        divider_extra = "border-radius:0; opacity:0;"
    elif pack_id == "lean_craft":
        after_bg = "background:rgba(217,119,87,0.10); border-radius:8px 6px 10px 7px; padding:12px 16px;"
        divider_extra = "transform:scaleX(0);"
    elif pack_id == "lean_cinema":
        after_bg = "background:rgba(201,168,106,0.06); border-radius:0px; padding:12px 16px;"
        divider_extra = "transform:scaleX(0);"
    elif pack_id == "lean_paper":
        after_bg = "background:rgba(79,107,255,0.06); border-radius:8px; padding:12px 16px;"
        divider_extra = "transform:scaleX(0);"
    else:  # glass
        after_bg = "background:rgba(76,201,240,0.08); border-radius:16px; padding:12px 16px;"
        divider_extra = "transform:scaleX(0);"

    glow_css = f" text-shadow:{_e(glow)};" if glow else ""

    css = f"""\
.card[data-card-id="{card_id}"] .root{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;}}
.card[data-card-id="{card_id}"] .ct-wrap{{background:{bg};border-radius:{radius};padding:32px 36px;
  display:flex;flex-direction:column;gap:14px;box-shadow:{shadow_v};width:90%;max-width:460px;}}
.card[data-card-id="{card_id}"] .ct-label{{font-family:{font};font-size:11px;font-weight:700;
  letter-spacing:0.16em;text-transform:uppercase;color:{text_s};opacity:0;margin-bottom:2px;}}
.card[data-card-id="{card_id}"] .ct-text{{font-family:{font};font-size:22px;font-weight:{fw};
  color:{text_c};line-height:1.3;opacity:0;{glow_css}}}
.card[data-card-id="{card_id}"] .ct-text-after{{font-family:{font};font-size:22px;font-weight:{fw};
  color:{accent};line-height:1.3;opacity:0;}}
.card[data-card-id="{card_id}"] .ct-divider{{width:100%;height:2px;background:{accent};{divider_extra}}}
.card[data-card-id="{card_id}"] .ct-after-block{{{after_bg}opacity:0;}}"""

    before_text = _e(before_raw) if before_raw else "L'époque où tout était flou…"
    after_text  = _e(after_raw)  if after_raw  else "Aujourd'hui, tout a changé."

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="ct-wrap">
    <div class="ct-label" id="{card_id}-ct-before-label">{lbl_before}</div>
    <div class="ct-text" id="{card_id}-ct-before">{before_text}</div>
    <div class="ct-divider" id="{card_id}-ct-divider"></div>
    <div class="ct-label" id="{card_id}-ct-after-label">{lbl_after}</div>
    <div class="ct-after-block" id="{card_id}-ct-after-block">
      <div class="ct-text-after" id="{card_id}-ct-after">{after_text}</div>
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
    t_b1   = t_in
    t_b2   = round(t_in + 0.15, 4)
    t_div  = round(t_in + 0.38, 4)
    t_a1   = round(t_div + 0.22, 4)
    t_a2   = round(t_a1 + 0.15, 4)

    d_label = 0.60 if is_cinema else 0.25
    ease_in = "none" if is_ledger else "power2.out"

    lines: list[str] = []

    # AVANT label
    lines.append(
        f"  tl.to('#{cid}-ct-before-label',{{opacity:1,duration:{d_label},ease:'{ease_in}'}},{t_b1:.4f});"
    )

    # AVANT text
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-ct-before',{{opacity:1,duration:0.70,ease:'power1.in'}},{t_b2:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-ct-before',{{opacity:0,y:8}},{{opacity:1,y:0,duration:0.30,ease:'back.out(1.4)'}},{t_b2:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-ct-before',{{opacity:0,y:6}},{{opacity:1,y:0,duration:0.25,ease:'power2.out'}},{t_b2:.4f});")

    # Divider
    if is_ledger:
        lines.append(f"  tl.to('#{cid}-ct-divider',{{opacity:1,duration:0.20,ease:'none'}},{t_div:.4f});")
    elif is_cinema:
        lines.append(f"  tl.to('#{cid}-ct-divider',{{scaleX:1,duration:0.80,ease:'power1.inOut',transformOrigin:'left center'}},{t_div:.4f});")
    else:
        lines.append(f"  tl.to('#{cid}-ct-divider',{{scaleX:1,duration:0.40,ease:'power2.inOut',transformOrigin:'left center'}},{t_div:.4f});")

    # APRÈS label
    lines.append(f"  tl.to('#{cid}-ct-after-label',{{opacity:1,duration:{d_label},ease:'{ease_in}'}},{t_a1:.4f});")

    # APRÈS block + text
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-ct-after-block',{{opacity:1,duration:0.80,ease:'power2.in'}},{t_a2:.4f});")
        lines.append(f"  tl.to('#{cid}-ct-after',{{opacity:1,duration:0.80,ease:'power2.in'}},{t_a2:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('#{cid}-ct-after-block',{{opacity:0,scale:0.92}},{{opacity:1,scale:1,duration:0.35,ease:'back.out(1.6)'}},{t_a2:.4f});")
        lines.append(f"  tl.to('#{cid}-ct-after',{{opacity:1,duration:0.25,ease:'power1.out'}},{round(t_a2+0.05,4):.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-ct-after-block',{{opacity:0,y:8}},{{opacity:1,y:0,duration:0.30,ease:'power2.out'}},{t_a2:.4f});")
        lines.append(f"  tl.to('#{cid}-ct-after',{{opacity:1,duration:0.25,ease:'power1.out'}},{round(t_a2+0.08,4):.4f});")

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

register(BRollType(
    name="client_transformation",
    patterns=[_AVANT_RE, _TRANSFORM_RE, _MAINTENANT_RE],
    extractor=_extractor,
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=5.0,
    preferred_zone="upper-data",
    min_confidence=0.80,
))
