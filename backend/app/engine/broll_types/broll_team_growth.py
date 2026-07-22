"""
team_growth — animated dot-grid showing team expansion (N members → M members).

Triggers on:
  FR: "passé de 3 à 12 membres", "embauché 5 personnes", "on était 2 on est maintenant 8",
      "notre équipe a grossi de 4 à 20 collaborateurs"
  EN: "team grew from 3 to 15", "hired 5 people", "went from 1 to 10 employees",
      "team went from solo to 8 members"

Visual: row of dots — initial N (dim), then M-N new ones (accent color) pop in.
Cap at 12 visible dots; overflow shown as "+N" label.

Distinct from growth_curve: growth_curve requires numeric revenue/multiplier context;
team_growth requires explicit people/team vocabulary.
"""
from __future__ import annotations

import re
from app.engine.broll_registry import BRollType, register


# ── Patterns ──────────────────────────────────────────────────────────────────

_TEAM_FR_RE = re.compile(
    r"\b(?:"
    r"(?:passé|passer|passée)\s+de\s+(?P<fr_from>\d+)\s+[àa]\s+(?P<fr_to>\d+)\s+"
    r"(?:personnes?|membres?|collaborateurs?|équipiers?|employ(?:é|és?)|salariés?)|"
    r"(?:embauch(?:é|er)|recrut(?:é|er)|engagé)\s+(?P<hired>\d+)\s+(?:personnes?|collaborateurs?|membres?)|"
    r"on\s+était\s+(?P<solo_fr>\d+)(?:\s+(?:personnes?|membres?))?\s*(?:,\s*)?(?:on\s+est\s+(?:maintenant\s+)?|aujourd'hui\s+on\s+est\s+)(?P<now_fr>\d+)|"
    r"(?:équipe|team)\s+(?:a\s+grossi|est\s+passée|est\s+montée)\s+de\s+(?P<gr_from>\d+)\s+[àa]\s+(?P<gr_to>\d+)"
    r")\b",
    re.IGNORECASE,
)

_TEAM_EN_RE = re.compile(
    r"\b(?:"
    r"team\s+(?:grew|went|expanded|scaled)\s+from\s+(?P<en_from>\d+)\s+to\s+(?P<en_to>\d+)|"
    r"(?:hired|recruited|onboarded)\s+(?P<hired_en>\d+)\s+(?:people|employees?|members?|staff)|"
    r"(?:went|going)\s+from\s+(?P<solo_en>\d+)\s+(?:to\s+)?(?P<now_en>\d+)\s+(?:employees?|members?|people|team\s+members?)|"
    r"(?:solo|just\s+me)\s+to\s+(?:a\s+team\s+of\s+)?(?P<solo_to>\d+)"
    r")\b",
    re.IGNORECASE,
)

_ALL_PATTERNS = [_TEAM_FR_RE, _TEAM_EN_RE]

_DOT_MAX = 12  # max visible dots


def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ej(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


def _safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ── Extractor ─────────────────────────────────────────────────────────────────

def _extractor(match, words, word_idx: int) -> tuple[dict, float]:
    gd = match.groupdict()

    # from/to
    from_val = _safe_int(gd.get("fr_from") or gd.get("en_from") or gd.get("gr_from") or gd.get("solo_fr") or gd.get("solo_en"))
    to_val   = _safe_int(gd.get("fr_to")   or gd.get("en_to")   or gd.get("gr_to")   or gd.get("now_fr")  or gd.get("now_en"))

    # hired-only (no start count → assume 1)
    hired = _safe_int(gd.get("hired") or gd.get("hired_en"))
    if hired is not None and from_val is None:
        from_val, to_val = 1, 1 + hired

    solo_to = _safe_int(gd.get("solo_to"))
    if solo_to is not None:
        from_val, to_val = 1, solo_to

    if from_val is None or to_val is None or to_val <= from_val:
        return {}, 0.0
    if from_val < 1 or to_val > 500:
        return {}, 0.0

    conf = 0.90 if match.re is _TEAM_FR_RE else 0.86

    return {"start_count": from_val, "end_count": to_val}, conf


# ── Render HTML ───────────────────────────────────────────────────────────────

def _dot_html(card_id: str, count: int, new_start: int, end: int, accent: str, text_s: str) -> str:
    """Build dot spans: first new_start are 'existing', rest are 'new'."""
    visible   = min(count, _DOT_MAX)
    overflow  = count - visible
    dots = []
    for i in range(visible):
        if i < new_start:
            dots.append(f'<span class="tg-dot tg-dot-old"></span>')
        else:
            dots.append(f'<span class="tg-dot tg-dot-new"></span>')
    html = "".join(dots)
    if overflow > 0:
        html += f'<span class="tg-overflow">+{overflow}</span>'
    return html


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

    start_count = params.get("start_count", 1)
    end_count   = params.get("end_count", 2)
    new_count   = end_count - start_count

    glow_css = f" text-shadow:{_e(glow_i)};" if glow_i else ""

    # Dot color/style per pack
    if pack_id == "lean_paper":
        dot_old_css = f"background:rgba(0,0,0,0.12); border:2px solid rgba(0,0,0,0.20);"
        dot_new_css = f"background:{accent};"
    elif pack_id == "lean_vibe":
        dot_old_css = "background:rgba(255,255,255,0.25);"
        dot_new_css = f"background:{accent}; box-shadow:0 0 8px rgba(255,230,109,0.4);"
    elif pack_id == "lean_ledger":
        dot_old_css = f"background:rgba(0,200,150,0.15); border:1px solid rgba(0,200,150,0.30);"
        dot_new_css = f"background:{accent};"
    elif pack_id == "lean_craft":
        dot_old_css = "background:rgba(61,43,31,0.15);"
        dot_new_css = f"background:{accent};"
    elif pack_id == "lean_cinema":
        dot_old_css = "background:rgba(245,240,232,0.15);"
        dot_new_css = f"background:{accent};"
    else:
        dot_old_css = "background:rgba(255,255,255,0.18);"
        dot_new_css = f"background:{accent}; box-shadow:0 0 10px {accent}88;"

    dots_html = _dot_html(card_id, end_count, start_count, end_count, accent, text_s)

    # Label text
    if pack_id == "lean_ledger":
        label_txt  = f"TEAM: {start_count} → {end_count}"
        sublabel   = f"+ {new_count} NEW"
    elif pack_id in ("lean_craft", "lean_cinema"):
        label_txt  = f"{start_count} → {end_count} membres"
        sublabel   = f"+ {new_count} nouvelles personnes"
    else:
        label_txt  = f"{start_count} → {end_count} MEMBRES"
        sublabel   = f"+ {new_count} nouvelles recrues"

    css = f"""\
.card[data-card-id="{card_id}"] .root{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;}}
.card[data-card-id="{card_id}"] .tg-wrap{{background:{bg};border-radius:{radius};padding:32px 40px;
  display:flex;flex-direction:column;align-items:center;gap:20px;box-shadow:{shadow_v};width:90%;max-width:440px;}}
.card[data-card-id="{card_id}"] .tg-headline{{font-family:{font};font-size:26px;font-weight:{fw};
  color:{text_c};letter-spacing:0.02em;opacity:0;text-align:center;{glow_css}}}
.card[data-card-id="{card_id}"] .tg-dots-wrap{{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;opacity:0;}}
.card[data-card-id="{card_id}"] .tg-dot{{width:18px;height:18px;border-radius:50%;display:inline-block;transition:none;}}
.card[data-card-id="{card_id}"] .tg-dot-old{{{dot_old_css}}}
.card[data-card-id="{card_id}"] .tg-dot-new{{{dot_new_css} opacity:0;transform:scale(0);}}
.card[data-card-id="{card_id}"] .tg-overflow{{font-family:{font};font-size:14px;font-weight:700;
  color:{text_s};align-self:center;}}
.card[data-card-id="{card_id}"] .tg-sublabel{{font-family:{font};font-size:16px;font-weight:600;
  color:{accent};letter-spacing:0.06em;text-transform:uppercase;opacity:0;}}"""

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="tg-wrap">
    <div class="tg-headline" id="{card_id}-tg-headline">{_e(label_txt)}</div>
    <div class="tg-dots-wrap" id="{card_id}-tg-dots">{dots_html}</div>
    <div class="tg-sublabel" id="{card_id}-tg-sublabel">{_e(sublabel)}</div>
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

    start_count = params.get("start_count", 1)
    end_count   = params.get("end_count", 2)
    new_count   = end_count - start_count
    visible_new = min(new_count, _DOT_MAX - min(start_count, _DOT_MAX))

    t_in      = round(start + 0.18, 4)
    t_dots    = round(t_in + 0.30, 4)
    t_new     = round(t_dots + 0.40, 4)
    t_sub     = round(t_new + max(0.25, visible_new * 0.08), 4)

    lines: list[str] = []

    # Headline
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-tg-headline',{{opacity:1,duration:0.80,ease:'power1.in'}},{t_in:.4f});")
    elif is_ledger:
        lines.append(f"  tl.to('#{cid}-tg-headline',{{opacity:1,duration:0.15,ease:'none'}},{t_in:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-tg-headline',{{opacity:0,y:-6}},{{opacity:1,y:0,duration:0.30,ease:'power2.out'}},{t_in:.4f});")

    # Existing dots fade in as a group
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-tg-dots',{{opacity:1,duration:0.80,ease:'power1.in'}},{t_dots:.4f});")
    else:
        lines.append(f"  tl.to('#{cid}-tg-dots',{{opacity:1,duration:0.25,ease:'power1.out'}},{t_dots:.4f});")

    # New dots pop in with stagger (class-based selector — no ID drift risk)
    if visible_new > 0:
        stagger = 0.06 if is_vibe else 0.08
        if is_cinema:
            lines.append(
                f"  gsap.to('.card[data-card-id=\"{card_id}\"] .tg-dot-new',"
                f"{{opacity:1,scale:1,duration:0.60,ease:'power2.in',stagger:{stagger}}});"
            )
        elif is_vibe:
            lines.append(
                f"  gsap.fromTo('.card[data-card-id=\"{card_id}\"] .tg-dot-new',"
                f"{{opacity:0,scale:0}},{{opacity:1,scale:1,duration:0.30,ease:'back.out(2.0)',stagger:{stagger}}});"
            )
        else:
            lines.append(
                f"  gsap.fromTo('.card[data-card-id=\"{card_id}\"] .tg-dot-new',"
                f"{{opacity:0,scale:0}},{{opacity:1,scale:1,duration:0.25,ease:'back.out(1.6)',stagger:{stagger}}});"
            )
        # Delay the stagger start
        lines.append(
            f"  gsap.delayedCall({t_new:.4f} - gsap.globalTimeline.time(), function(){{}});"
        )
        # Simpler: use tl.add with absolute time
        # Actually the gsap.to above doesn't use the timeline — use absolute positioning:
        # This is intentional: we use gsap.to with a delay= prop, not tl
        # Replace with tl-based stagger that uses absolute time:
        lines.pop()  # remove the delayedCall placeholder
        lines.pop()  # remove the gsap.fromTo / gsap.to for new dots
        # Use tl.to with stagger and delay
        if is_cinema:
            lines.append(
                f"  tl.to('.card[data-card-id=\"{card_id}\"] .tg-dot-new',"
                f"{{opacity:1,scale:1,duration:0.60,ease:'power2.in',stagger:{stagger}}},{t_new:.4f});"
            )
        elif is_vibe:
            lines.append(
                f"  tl.fromTo('.card[data-card-id=\"{card_id}\"] .tg-dot-new',"
                f"{{opacity:0,scale:0}},{{opacity:1,scale:1,duration:0.30,ease:'back.out(2.0)',stagger:{stagger}}},{t_new:.4f});"
            )
        else:
            lines.append(
                f"  tl.fromTo('.card[data-card-id=\"{card_id}\"] .tg-dot-new',"
                f"{{opacity:0,scale:0}},{{opacity:1,scale:1,duration:0.25,ease:'back.out(1.6)',stagger:{stagger}}},{t_new:.4f});"
            )

    # Sublabel
    lines.append(f"  tl.to('#{cid}-tg-sublabel',{{opacity:1,duration:0.30,ease:'power1.out'}},{t_sub:.4f});")

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

register(BRollType(
    name="team_growth",
    patterns=_ALL_PATTERNS,
    extractor=_extractor,
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=5.0,
    preferred_zone="upper-data",
    min_confidence=0.84,
))
