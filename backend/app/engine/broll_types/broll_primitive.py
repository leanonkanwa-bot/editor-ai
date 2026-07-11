"""
generative_primitive — universal renderer for LLM-chosen shape/entry combos.

This type is NEVER triggered by the pattern scanner (patterns=[]).
It is created exclusively by broll_generative.py, which calls the
Haiku LLM to pick shape/entry/content_type/zone for uncovered strong beats.

Shape × entry combinations supported (4 × 4 = 16):
  shape:  circle | bar | badge | grid
  entry:  pop | trace | fade | slide_up

Content slot (value always deterministic — extracted from transcript by code,
except emoji where the LLM picks the glyph):
  number  — large numeral + optional unit
  text    — short phrase (≤ 4 words, from transcript)
  emoji   — large centred glyph (≤ 2 chars, LLM-chosen)

Label (≤ 4 words) and kicker (≤ 2 words) are ALWAYS set by code
from transcript words nearest the beat — never by the LLM.
"""
from __future__ import annotations

from app.engine.broll_registry import BRollType, register


# ── Helpers ───────────────────────────────────────────────────────────────────

def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _ej(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


# ── HTML ──────────────────────────────────────────────────────────────────────

def _render_html(params: dict, pack: dict, card_id: str) -> str:
    p        = pack or {}
    bg       = p.get("bg",           "#1a1a1a")
    text_c   = p.get("text",         "#f1f1f1")
    text_s   = p.get("text_secondary","rgba(255,255,255,0.45)")
    accent   = p.get("accent",       "#4cc9f0")
    font     = p.get("font",         '"Inter", sans-serif')
    fw       = p.get("font_weight",  "800")
    radius   = p.get("radius",       "20px")
    shadow   = p.get("shadow",       "0 8px 32px rgba(0,0,0,0.4)")
    shadow_i = p.get("shadow_inset", "")
    shadow_v = f"{shadow}, {shadow_i}" if shadow_i else shadow

    shape        = params.get("shape",        "badge")
    content_type = params.get("content_type", "text")
    content_val  = _e(str(params.get("content_value", "")))
    label        = _e(params.get("label", ""))
    kicker       = _e(params.get("kicker", ""))
    cid          = card_id

    # ── Content element ──────────────────────────────────────────────────────
    if content_type == "emoji":
        content_html = f'<div class="prim-emoji" id="{cid}-prim-content">{content_val}</div>'
        content_css  = f""".card[data-card-id="{cid}"] .prim-emoji {{
  font-size:80px; line-height:1; opacity:0; display:block; text-align:center;
}}"""
    elif content_type == "number":
        content_html = f'<div class="prim-number" id="{cid}-prim-content">{content_val}</div>'
        content_css  = f""".card[data-card-id="{cid}"] .prim-number {{
  font-family:{font}; font-size:88px; font-weight:{fw}; color:{text_c};
  font-variant-numeric:tabular-nums; line-height:1; opacity:0;
}}"""
    else:  # text
        content_html = f'<div class="prim-text" id="{cid}-prim-content">{content_val}</div>'
        content_css  = f""".card[data-card-id="{cid}"] .prim-text {{
  font-family:{font}; font-size:36px; font-weight:{fw}; color:{text_c};
  text-align:center; line-height:1.25; opacity:0; max-width:320px;
}}"""

    # ── Shape wrapper ────────────────────────────────────────────────────────
    if shape == "circle":
        shape_css = f"""\
.card[data-card-id="{cid}"] .prim-shape {{
  width:160px; height:160px; border-radius:50%;
  border:3px solid {accent}; display:flex; align-items:center;
  justify-content:center; opacity:0;
  box-shadow:0 0 0px {accent};
}}"""
        shape_open  = f'<div class="prim-shape" id="{cid}-prim-shape">'
        shape_close = "</div>"

    elif shape == "bar":
        shape_css = f"""\
.card[data-card-id="{cid}"] .prim-shape {{
  width:0%; max-width:340px; height:6px; border-radius:3px;
  background:{accent}; opacity:1;
}}
.card[data-card-id="{cid}"] .prim-bar-wrap {{
  width:340px; display:flex; flex-direction:column; gap:10px; align-items:flex-start;
}}"""
        # Bar: content goes above the bar
        bar_inner = f'{content_html}<div class="prim-shape" id="{cid}-prim-shape"></div>'
        shape_open  = f'<div class="prim-bar-wrap">{bar_inner}'
        shape_close = '</div>'
        content_html = ""  # already injected

    elif shape == "grid":
        # 3×3 grid of accent squares
        cells = "".join(
            f'<div class="prim-cell" id="{cid}-cell-{i}"></div>'
            for i in range(9)
        )
        shape_css = f"""\
.card[data-card-id="{cid}"] .prim-shape {{
  display:grid; grid-template-columns:repeat(3,1fr); gap:8px; width:120px;
}}
.card[data-card-id="{cid}"] .prim-cell {{
  width:32px; height:32px; border-radius:4px;
  background:{accent}; opacity:0;
}}"""
        # Grid: content below grid
        shape_open  = f'<div class="prim-shape" id="{cid}-prim-shape">{cells}</div>'
        shape_close = ""

    else:  # badge (default)
        shape_css = f"""\
.card[data-card-id="{cid}"] .prim-shape {{
  background:{accent}; border-radius:40px; padding:14px 28px;
  display:inline-flex; align-items:center; justify-content:center;
  opacity:0; transform:scale(0.8);
}}
.card[data-card-id="{cid}"] .prim-shape .prim-emoji,
.card[data-card-id="{cid}"] .prim-shape .prim-number,
.card[data-card-id="{cid}"] .prim-shape .prim-text {{
  color:{bg}; opacity:1;
}}"""
        shape_open  = f'<div class="prim-shape" id="{cid}-prim-shape">'
        shape_close = "</div>"

    # ── Supplementary text ───────────────────────────────────────────────────
    kicker_html = (
        f'<div class="prim-kicker" id="{cid}-prim-kicker">{kicker}</div>'
        if kicker else ""
    )
    label_html = (
        f'<div class="prim-label" id="{cid}-prim-label">{label}</div>'
        if label else ""
    )

    supp_css = f"""\
.card[data-card-id="{cid}"] .prim-kicker {{
  font-family:{font}; font-size:18px; font-weight:700; color:{accent};
  letter-spacing:0.12em; text-transform:uppercase; opacity:0;
}}
.card[data-card-id="{cid}"] .prim-label {{
  font-family:{font}; font-size:24px; font-weight:600; color:{text_s};
  text-align:center; max-width:300px; opacity:0;
}}"""

    panel_css = f"""\
.card[data-card-id="{cid}"] .root {{
  width:100%; height:100%; display:flex; align-items:center; justify-content:center;
}}
.card[data-card-id="{cid}"] .card-panel {{
  background:{bg}; border-radius:{radius}; padding:36px 48px;
  display:flex; flex-direction:column; align-items:center; gap:16px;
  box-shadow:{shadow_v}; position:relative; overflow:hidden;
}}"""

    # For badge shape: content is inside the shape wrapper
    if shape == "badge":
        inner = f"{kicker_html}{shape_open}{content_html}{shape_close}{label_html}"
    elif shape == "bar":
        inner = f"{kicker_html}{shape_open}{shape_close}{label_html}"
    else:
        inner = f"{kicker_html}{shape_open}{shape_close}{content_html}{label_html}"

    return f"""\
<div class="card" data-card-id="{cid}">
<style>
{panel_css}
{shape_css}
{content_css}
{supp_css}
</style>
<div class="root">
  <div class="card-panel">
    {inner}
  </div>
</div>
</div>"""


# ── GSAP ─────────────────────────────────────────────────────────────────────

def _render_gsap(params: dict, pack: dict, card_id: str, start: float, end: float) -> list[str]:
    cid   = _ej(card_id)
    dur   = max(0.5, end - start)
    t_in  = round(start + 0.20, 4)

    shape        = params.get("shape",   "badge")
    entry        = params.get("entry",   "pop")
    content_type = params.get("content_type", "text")
    kicker       = params.get("kicker",  "")
    label        = params.get("label",   "")

    lines: list[str] = []

    # 0. kicker (always fades in first if present)
    if kicker:
        lines.append(
            f"  tl.to('#{cid}-prim-kicker',"
            f"{{opacity:1,duration:0.25,ease:'power1.out'}},{t_in:.4f});"
        )

    t_shape = round(t_in + (0.15 if kicker else 0), 4)

    # 1. Shape entry animation
    if shape == "circle":
        if entry == "pop":
            lines.append(
                f"  tl.fromTo('#{cid}-prim-shape',"
                f"{{opacity:0,scale:0.4}},"
                f"{{opacity:1,scale:1,duration:0.35,ease:'back.out(1.7)'}},{t_shape:.4f});"
            )
        elif entry == "trace":
            # SVG ring draw via boxShadow spread trick (pure CSS circle)
            lines.append(
                f"  tl.to('#{cid}-prim-shape',"
                f"{{opacity:1,duration:0.10}},{t_shape:.4f});"
            )
            lines.append(
                f"  tl.fromTo('#{cid}-prim-shape',"
                f"{{'box-shadow':'0 0 0 0px rgba(76,201,240,0)'}},"
                f"{{'box-shadow':'0 0 0 3px {_ej(pack.get(\"accent\",\"#4cc9f0\"))}',duration:0.50,ease:'power2.out'}}"
                f",{t_shape:.4f});"
            )
        elif entry == "slide_up":
            lines.append(
                f"  tl.fromTo('#{cid}-prim-shape',"
                f"{{opacity:0,y:24}},"
                f"{{opacity:1,y:0,duration:0.35,ease:'power2.out'}},{t_shape:.4f});"
            )
        else:  # fade
            lines.append(
                f"  tl.to('#{cid}-prim-shape',"
                f"{{opacity:1,duration:0.40,ease:'power1.out'}},{t_shape:.4f});"
            )

    elif shape == "bar":
        if entry in ("trace", "pop"):
            lines.append(
                f"  tl.to('#{cid}-prim-shape',"
                f"{{width:'100%',duration:0.55,ease:'power2.out'}},{t_shape:.4f});"
            )
        elif entry == "slide_up":
            lines.append(
                f"  tl.fromTo('#{cid}-prim-shape',"
                f"{{width:'0%',opacity:0}},"
                f"{{width:'100%',opacity:1,duration:0.45,ease:'power2.out'}},{t_shape:.4f});"
            )
        else:  # fade
            lines.append(
                f"  tl.to('#{cid}-prim-shape',"
                f"{{width:'100%',duration:0.45,ease:'power1.inOut'}},{t_shape:.4f});"
            )

    elif shape == "grid":
        # Stagger over cells
        stagger = 0.04
        if entry == "pop":
            lines.append(
                f"  tl.fromTo('.card[data-card-id=\"{cid}\"] .prim-cell',"
                f"{{opacity:0,scale:0.4}},"
                f"{{opacity:1,scale:1,stagger:{stagger},duration:0.25,ease:'back.out(1.7)'}}"
                f",{t_shape:.4f});"
            )
        elif entry == "trace":
            lines.append(
                f"  tl.to('.card[data-card-id=\"{cid}\"] .prim-cell',"
                f"{{opacity:1,stagger:0.07,duration:0.15,ease:'power1.out'}}"
                f",{t_shape:.4f});"
            )
        elif entry == "slide_up":
            lines.append(
                f"  tl.fromTo('.card[data-card-id=\"{cid}\"] .prim-cell',"
                f"{{opacity:0,y:12}},"
                f"{{opacity:1,y:0,stagger:{stagger},duration:0.22,ease:'power2.out'}}"
                f",{t_shape:.4f});"
            )
        else:  # fade
            lines.append(
                f"  tl.to('.card[data-card-id=\"{cid}\"] .prim-cell',"
                f"{{opacity:1,stagger:{stagger},duration:0.20,ease:'power1.out'}}"
                f",{t_shape:.4f});"
            )

    else:  # badge
        if entry == "pop":
            lines.append(
                f"  tl.fromTo('#{cid}-prim-shape',"
                f"{{opacity:0,scale:0.6}},"
                f"{{opacity:1,scale:1,duration:0.35,ease:'back.out(1.7)'}},{t_shape:.4f});"
            )
        elif entry == "trace":
            lines.append(
                f"  tl.fromTo('#{cid}-prim-shape',"
                f"{{opacity:0,clipPath:'inset(0 100% 0 0)'}},"
                f"{{opacity:1,clipPath:'inset(0 0% 0 0)',duration:0.40,ease:'power2.out'}}"
                f",{t_shape:.4f});"
            )
        elif entry == "slide_up":
            lines.append(
                f"  tl.fromTo('#{cid}-prim-shape',"
                f"{{opacity:0,y:20}},"
                f"{{opacity:1,y:0,duration:0.35,ease:'power2.out'}},{t_shape:.4f});"
            )
        else:  # fade
            lines.append(
                f"  tl.fromTo('#{cid}-prim-shape',"
                f"{{opacity:0,scale:0.95}},"
                f"{{opacity:1,scale:1,duration:0.35,ease:'power1.out'}},{t_shape:.4f});"
            )

    # 2. Content inside shape (circle / grid / standalone)
    if shape not in ("badge", "bar"):
        t_content = round(t_shape + 0.20, 4)
        lines.append(
            f"  tl.fromTo('#{cid}-prim-content',"
            f"{{opacity:0,scale:0.8}},"
            f"{{opacity:1,scale:1,duration:0.28,ease:'back.out(1.4)'}},{t_content:.4f});"
        )
    elif shape == "bar":
        # Content (above bar) appears with bar
        t_content = t_shape
        lines.append(
            f"  tl.fromTo('#{cid}-prim-content',"
            f"{{opacity:0}},"
            f"{{opacity:1,duration:0.30,ease:'power1.out'}},{t_content:.4f});"
        )

    # 3. Label
    if label:
        t_label = round(t_shape + 0.30, 4)
        lines.append(
            f"  tl.fromTo('#{cid}-prim-label',"
            f"{{opacity:0,y:6}},"
            f"{{opacity:1,y:0,duration:0.25,ease:'power1.out'}},{t_label:.4f});"
        )

    return lines


# ── Register (patterns=[] → scanner never triggers this type) ─────────────────

register(BRollType(
    name="generative_primitive",
    patterns=[],          # never triggered by semantic_scanner
    extractor=lambda m, w, i: ({}, 0.0),
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=5.0,
    preferred_zone="upper-data",
    min_confidence=0.65,
))
