"""
Lower third overlays — three per-pack style variants.

Style selection is automatic based on pack id:
  lean_glass / lean_vibe / lean_cinema → lt-kicker-name   (neon bar left + name)
  lean_paper / lean_ledger             → lt-accent-underline (bold name + animated underline)
  lean_craft                           → lt-soft-pill       (pill background around name)

Params expected:
  name    — speaker name or kicker text (required)
  title   — role/subtitle below the name (optional)
"""
from __future__ import annotations

from app.engine.broll_registry import BRollType, register


def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _ej(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


_PACK_STYLE_MAP = {
    "lean_glass":   "lt-kicker-name",
    "lean_vibe":    "lt-kicker-name",
    "lean_cinema":  "lt-kicker-name",
    "lean_paper":   "lt-accent-underline",
    "lean_ledger":  "lt-accent-underline",
    "lean_craft":   "lt-soft-pill",
}


def _render_html(params: dict, pack: dict, card_id: str) -> str:
    cid    = card_id
    name   = _e(params.get("name", params.get("label", "Speaker")))
    title  = _e(params.get("title", params.get("kicker", "")))
    accent = pack.get("accent", "#4cc9f0")
    text_c = pack.get("text", "#f1f1f1")
    bg     = pack.get("bg", "#1a1a1a")
    font   = pack.get("font", '"Inter", sans-serif')
    fw     = pack.get("font_weight", "800")
    style  = _PACK_STYLE_MAP.get(pack.get("id", ""), "lt-kicker-name")

    if style == "lt-kicker-name":
        html = f"""\
<div class="card lt-root" data-card-id="{cid}">
<style>
.card[data-card-id="{cid}"].lt-root {{
  width:100%; height:100%; display:flex; align-items:flex-end;
  padding-bottom:20px; padding-left:24px;
}}
.card[data-card-id="{cid}"] .lt-bar {{
  width:4px; height:0px; background:{accent}; border-radius:2px;
  box-shadow:0 0 12px {accent}; flex-shrink:0;
}}
.card[data-card-id="{cid}"] .lt-text-col {{
  display:flex; flex-direction:column; gap:3px;
  margin-left:12px; overflow:hidden;
}}
.card[data-card-id="{cid}"] .lt-name {{
  font-family:{font}; font-size:20px; font-weight:{fw}; color:{text_c};
  opacity:0; white-space:nowrap; overflow:hidden;
}}
.card[data-card-id="{cid}"] .lt-sub {{
  font-family:{font}; font-size:12px; font-weight:600; color:{accent};
  letter-spacing:0.12em; text-transform:uppercase; opacity:0;
}}
</style>
<div class="lt-bar" id="{cid}-bar"></div>
<div class="lt-text-col">
  <div class="lt-name" id="{cid}-name">{name}</div>
  {'<div class="lt-sub" id="' + cid + '-sub">' + title + '</div>' if title else ""}
</div>
</div>"""

    elif style == "lt-accent-underline":
        html = f"""\
<div class="card lt-root" data-card-id="{cid}">
<style>
.card[data-card-id="{cid}"].lt-root {{
  width:100%; height:100%; display:flex; align-items:flex-end;
  padding-bottom:20px; padding-left:24px;
}}
.card[data-card-id="{cid}"] .lt-block {{
  display:flex; flex-direction:column; gap:6px; align-items:flex-start;
}}
.card[data-card-id="{cid}"] .lt-name {{
  font-family:{font}; font-size:22px; font-weight:{fw}; color:{text_c};
  opacity:0;
}}
.card[data-card-id="{cid}"] .lt-sub {{
  font-family:{font}; font-size:12px; font-weight:600;
  color:{pack.get("text_secondary","rgba(255,255,255,0.5)")}; opacity:0;
}}
.card[data-card-id="{cid}"] .lt-line {{
  width:0; height:2px; background:{accent};
  box-shadow:{pack.get("accent_line_glow","0 0 8px " + accent)};
  border-radius:1px;
}}
</style>
<div class="lt-block">
  <div class="lt-name" id="{cid}-name">{name}</div>
  {'<div class="lt-sub" id="' + cid + '-sub">' + title + '</div>' if title else ""}
  <div class="lt-line" id="{cid}-line"></div>
</div>
</div>"""

    else:  # lt-soft-pill
        bg_solid = bg if "gradient" not in bg else "#2a1f14"
        html = f"""\
<div class="card lt-root" data-card-id="{cid}">
<style>
.card[data-card-id="{cid}"].lt-root {{
  width:100%; height:100%; display:flex; align-items:flex-end;
  padding-bottom:20px; padding-left:24px;
}}
.card[data-card-id="{cid}"] .lt-pill {{
  display:inline-flex; flex-direction:column; gap:3px;
  background:{bg_solid}; border:1px solid rgba(217,119,87,0.30);
  border-radius:32px; padding:10px 20px;
  opacity:0; transform:translateY(10px);
}}
.card[data-card-id="{cid}"] .lt-name {{
  font-family:{font}; font-size:18px; font-weight:{fw}; color:{text_c};
}}
.card[data-card-id="{cid}"] .lt-sub {{
  font-family:{font}; font-size:11px; font-weight:600; color:{accent};
  letter-spacing:0.10em; text-transform:uppercase;
}}
</style>
<div class="lt-pill" id="{cid}-pill">
  <div class="lt-name">{name}</div>
  {'<div class="lt-sub">' + title + '</div>' if title else ""}
</div>
</div>"""

    return html


def _render_gsap(
    params: dict, pack: dict, card_id: str, start: float, end: float
) -> list[str]:
    cid   = _ej(card_id)
    t_in  = round(start + 0.15, 4)
    style = _PACK_STYLE_MAP.get(pack.get("id", ""), "lt-kicker-name")
    lines: list[str] = []

    if style == "lt-kicker-name":
        lines += [
            f"  tl.to('#{cid}-bar',{{height:'44px',duration:0.22,ease:'power2.out'}},{t_in:.4f});",
            f"  tl.fromTo('#{cid}-name',{{opacity:0,x:-14}},{{opacity:1,x:0,duration:0.28,ease:'power2.out'}},{round(t_in+0.18,4):.4f});",
            f"  tl.to('#{cid}-sub',{{opacity:1,duration:0.20,ease:'power1.out'}},{round(t_in+0.38,4):.4f});",
        ]

    elif style == "lt-accent-underline":
        lines += [
            f"  tl.fromTo('#{cid}-name',{{opacity:0,y:8}},{{opacity:1,y:0,duration:0.30,ease:'power2.out'}},{t_in:.4f});",
            f"  tl.to('#{cid}-sub',{{opacity:1,duration:0.20,ease:'power1.out'}},{round(t_in+0.25,4):.4f});",
            f"  tl.to('#{cid}-line',{{width:'100%',duration:0.35,ease:'power2.out'}},{round(t_in+0.30,4):.4f});",
        ]

    else:  # lt-soft-pill
        lines += [
            f"  tl.to('#{cid}-pill',{{opacity:1,y:0,duration:0.30,ease:'back.out(1.4)'}},{t_in:.4f});",
        ]

    return lines


register(BRollType(
    name="lower_third",
    patterns=[],
    extractor=lambda m, w, i: ({}, 0.0),
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=4.0,
    preferred_zone="lower-third-name",
    min_confidence=0.70,
))
