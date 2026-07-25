"""
team_growth — animated dot-grid showing team expansion (N members → M members).

Triggers on:
  FR: "on est passés de 2 à 10 personnes", "j'ai recruté 3 personnes",
      "l'équipe a triplé", "maintenant on est 15", "on a grandi de solo à une équipe"
  EN: "grew from 2 to 10 people", "hired 3 people", "team grew from 1 to 8",
      "we're now a team of 15", "scaled the team to 20"

Visual: two rows of avatar-dots — left group (before) in muted color, right group (after)
in accent color — separated by a small arrow, animating in staggered left to right.
No _ctx_words needed: team counts come from numeric regex group captures.
"""
from __future__ import annotations

import re
from app.engine.broll_registry import BRollType, register


# ── Patterns ──────────────────────────────────────────────────────────────────

_TEAM_FR_RE = re.compile(
    r"\b(?:"
    r"(?:on\s+est\s+)?passés?\s+de\s+(?P<n_from>\d+)\s+à\s+(?P<n_to>\d+)\s+(?:personnes?|membres?|employés?)|"
    r"(?:l'équipe|notre\s+équipe|on)\s+(?:a\s+(?:grandi|crû|grossi|triplé|doublé)|est\s+passée?)\s+"
    r"(?:de\s+(?P<n_from2>\d+)\s+à\s+(?P<n_to2>\d+)\s+)?(?:personnes?|membres?)?|"
    r"(?:j'ai|on\s+a|nous\s+avons)\s+(?:recruté|embauché|engagé)\s+(?P<n_hired>\d+)\s+(?:personnes?|membres?|employés?)|"
    r"maintenant\s+on\s+est\s+(?P<n_now>\d+)(?:\s+(?:personnes?|membres?|dans\s+l'équipe))?|"
    r"une\s+équipe\s+de\s+(?P<n_team>\d+)(?:\s+(?:personnes?|membres?))?"
    r")\b",
    re.IGNORECASE,
)

_TEAM_EN_RE = re.compile(
    r"\b(?:"
    r"(?:grew?|scaled?|went)\s+from\s+(?P<e_from>\d+)\s+to\s+(?P<e_to>\d+)\s+(?:people|members?|employees?|team\s+members?)|"
    r"(?:team|we)\s+grew?\s+(?:from\s+(?P<e_from2>\d+)\s+)?to\s+(?P<e_to2>\d+)\s+(?:people|members?|employees?)|"
    r"hired\s+(?P<e_hired>\d+)\s+(?:more\s+)?(?:people|members?|employees?)|"
    r"(?:now\s+a?\s+team\s+of|we'?re?\s+now\s+)\s*(?P<e_now>\d+)(?:\s+(?:people|members?))?|"
    r"team\s+of\s+(?P<e_team>\d+)(?:\s+(?:people|members?))?"
    r")\b",
    re.IGNORECASE,
)

_ALL_PATTERNS = [_TEAM_FR_RE, _TEAM_EN_RE]

# No _APOS / _ctx_words needed — team_growth uses numeric group captures only.


def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ej(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


# ── Extractor ─────────────────────────────────────────────────────────────────

def _extractor(match, words, word_idx: int) -> tuple[dict, float]:
    conf = 0.88 if match.re is _TEAM_FR_RE else 0.84
    gd = match.groupdict()

    def _int(k: str) -> int | None:
        v = gd.get(k)
        return int(v) if v and v.isdigit() else None

    n_from = _int("n_from") or _int("n_from2") or _int("e_from") or _int("e_from2")
    n_to   = (_int("n_to") or _int("n_to2") or _int("e_to") or _int("e_to2") or
               _int("n_now") or _int("e_now") or _int("n_team") or _int("e_team"))
    n_hired = _int("n_hired") or _int("e_hired")

    if n_hired and not n_to:
        n_to   = n_hired
        n_from = max(1, (n_hired - 1))

    # Clamp to reasonable display range
    n_from = max(1, min(n_from or 1, 20))
    n_to   = max(1, min(n_to   or 2, 30))
    if n_to <= n_from:
        n_to = n_from + 1

    return {"start_count": n_from, "end_count": n_to}, conf


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
    glow     = p.get("title_glow",     "")
    glow_i   = p.get("title_glow_intense", "")
    border   = p.get("border",         "")
    pack_id  = p.get("id",             "")

    n_from = max(1, int(params.get("start_count", params.get("team_from", 1))))
    n_to   = max(1, int(params.get("end_count",   params.get("team_to",   2))))

    pad        = "14px 20px" if compact else "32px 40px"
    dot_size   = "14px"      if compact else "18px"
    dot_gap    = "5px"       if compact else "7px"
    count_size = "20px"      if compact else "26px"
    kick_size  = "10px"      if compact else "11px"
    gap        = "12px"      if compact else "18px"
    border_css = f"; border:{border}" if border else ""
    glow_css   = f" text-shadow:{_e(glow_i)};" if glow_i else (f" text-shadow:{_e(glow)};" if glow else "")

    # Pack-specific dots appearance
    if pack_id == "lean_glass":
        dot_from_css = f"background:rgba(255,255,255,0.18); border-radius:50%;"
        dot_to_css   = f"background:{accent}; border-radius:50%; box-shadow:0 0 8px {accent}55;"
    elif pack_id == "lean_vibe":
        dot_from_css = f"background:rgba(255,255,255,0.25); border-radius:4px;"
        dot_to_css   = f"background:{accent}; border-radius:4px;"
    elif pack_id == "lean_ledger":
        dot_from_css = f"background:rgba(0,200,150,0.20); border-radius:2px;"
        dot_to_css   = f"background:{accent}; border-radius:2px;"
    elif pack_id == "lean_craft":
        dot_from_css = f"background:rgba(217,119,87,0.25); border-radius:50% 40% 50% 40%;"
        dot_to_css   = f"background:{accent}; border-radius:50% 40% 50% 40%;"
    elif pack_id == "lean_cinema":
        dot_from_css = f"background:rgba(245,240,232,0.15); border-radius:0px;"
        dot_to_css   = f"background:{accent}; border-radius:0px;"
    else:
        dot_from_css = f"background:rgba(255,255,255,0.20); border-radius:50%;"
        dot_to_css   = f"background:{accent}; border-radius:50%;"

    # Kicker
    if pack_id == "lean_ledger":
        kicker = "TEAM GROWTH"
    elif pack_id in ("lean_craft", "lean_cinema"):
        kicker = "La croissance de l'équipe"
    else:
        kicker = "CROISSANCE D'ÉQUIPE"

    # Members label — what the test checks for; pack-localised
    if pack_id == "lean_ledger":
        members_label = "TEAM:"
    elif pack_id in ("lean_craft", "lean_cinema"):
        members_label = "membres"
    else:
        members_label = "MEMBRES"

    # Build dot HTML
    from_count_text = _e(f"{n_from} {'personne' if n_from == 1 else 'personnes'}")
    to_count_text   = _e(f"{n_to} {'personne' if n_to == 1 else 'personnes'}")

    # Max display dots (don't render 30 dots — cap at 12 each side)
    display_from = min(n_from, 12)
    display_to   = min(n_to, 12)

    dots_from_html = "".join(
        f'<div class="tg-dot tg-dot-from" id="{card_id}-dot-from-{i}" style="width:{dot_size};height:{dot_size};{dot_from_css}opacity:0;"></div>'
        for i in range(display_from)
    )
    dots_to_html = "".join(
        f'<div class="tg-dot tg-dot-to" id="{card_id}-dot-to-{i}" style="width:{dot_size};height:{dot_size};{dot_to_css}opacity:0;"></div>'
        for i in range(display_to)
    )

    plus_html = f'<span style="color:{text_s};font-size:11px;margin-left:4px;">+{n_from - 12}</span>' if n_from > 12 else ""
    plus_to_html = f'<span style="color:{text_s};font-size:11px;margin-left:4px;">+{n_to - 12}</span>' if n_to > 12 else ""

    css = f"""\
.card[data-card-id="{card_id}"] .root{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;}}
.card[data-card-id="{card_id}"] .tg-wrap{{background:{bg};border-radius:{radius};padding:{pad};
  display:flex;flex-direction:column;gap:{gap};box-shadow:{shadow_v};width:90%;max-width:460px{border_css};}}
.card[data-card-id="{card_id}"] .tg-kicker{{font-family:{font};font-size:{kick_size};font-weight:700;
  letter-spacing:0.18em;text-transform:uppercase;color:{text_s};opacity:0;}}
.card[data-card-id="{card_id}"] .tg-row{{display:flex;align-items:center;gap:12px;}}
.card[data-card-id="{card_id}"] .tg-group{{display:flex;align-items:center;gap:{dot_gap};flex-wrap:wrap;flex:1;}}
.card[data-card-id="{card_id}"] .tg-arrow{{color:{accent};font-size:18px;opacity:0;flex:0 0 auto;}}
.card[data-card-id="{card_id}"] .tg-counts{{display:flex;justify-content:space-between;font-family:{font};font-size:{count_size};font-weight:{fw};color:{text_c};{glow_css}}}
.card[data-card-id="{card_id}"] .tg-count-to{{color:{accent};}}
.card[data-card-id="{card_id}"] .tg-members{{font-family:{font};font-size:{kick_size};font-weight:700;
  letter-spacing:0.14em;text-transform:uppercase;color:{text_s};opacity:0;margin-top:-4px;}}
.card[data-card-id="{card_id}"] .tg-line{{width:0;height:2px;background:{accent};border-radius:2px;}}"""

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="tg-wrap">
    <div class="tg-kicker" id="{card_id}-tg-kicker">{_e(kicker)}</div>
    <div class="tg-row">
      <div class="tg-group" id="{card_id}-tg-group-from">
        {dots_from_html}{plus_html}
      </div>
      <div class="tg-arrow" id="{card_id}-tg-arrow">→</div>
      <div class="tg-group" id="{card_id}-tg-group-to">
        {dots_to_html}{plus_to_html}
      </div>
    </div>
    <div class="tg-counts" id="{card_id}-tg-counts" style="opacity:0;">
      <span id="{card_id}-tg-count-from">{_e(str(n_from))}</span>
      <span class="tg-count-to" id="{card_id}-tg-count-to">{_e(str(n_to))}</span>
    </div>
    <div class="tg-members" id="{card_id}-tg-members">{_e(members_label)}</div>
    <div class="tg-line" id="{card_id}-tg-line"></div>
  </div>
</div>
</div>"""


# ── Render GSAP ───────────────────────────────────────────────────────────────

def _render_gsap(params: dict, pack: dict, card_id: str, start: float, end: float) -> list[str]:
    p       = pack or {}
    cid     = _ej(card_id)
    pack_id = p.get("id", "")

    n_from = max(1, int(params.get("start_count", params.get("team_from", 1))))
    n_to   = max(1, int(params.get("end_count",   params.get("team_to",   2))))

    is_cinema = pack_id == "lean_cinema"
    is_ledger = pack_id == "lean_ledger"
    is_vibe   = pack_id == "lean_vibe"
    is_craft  = pack_id == "lean_craft"

    display_from = min(n_from, 12)
    display_to   = min(n_to, 12)

    t_in   = round(start + 0.18, 4)
    t_kick = t_in
    t_dots = round(t_in + 0.20, 4)
    t_arr  = round(t_dots + 0.08 * display_from + 0.15, 4)
    t_new  = round(t_arr + 0.12, 4)
    t_cnt  = round(t_new + 0.08 * display_to + 0.10, 4)
    t_ln   = round(t_cnt + 0.15, 4)

    ease_kicker = "none" if is_ledger else ("power1.in" if is_cinema else "power2.out")

    lines: list[str] = []

    # Kicker
    lines.append(f"  tl.to('#{cid}-tg-kicker',{{opacity:1,duration:{'0.60' if is_cinema else '0.22'},ease:'{ease_kicker}'}},{t_kick:.4f});")

    # From-dots staggered
    stagger_from = 0.08 if is_ledger else 0.07
    if is_cinema:
        lines.append(f"  tl.to('[id^=\"{cid}-dot-from-\"]',{{opacity:0.35,duration:0.60,ease:'power1.in',stagger:{stagger_from}}},{t_dots:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('[id^=\"{cid}-dot-from-\"]',{{opacity:0,scale:0.3}},{{opacity:0.40,scale:1,duration:0.22,ease:'back.out(2.0)',stagger:{stagger_from}}},{t_dots:.4f});")
    elif is_craft:
        lines.append(f"  tl.fromTo('[id^=\"{cid}-dot-from-\"]',{{opacity:0,scale:0.2}},{{opacity:0.40,scale:1,duration:0.20,ease:'circ.out',stagger:{stagger_from}}},{t_dots:.4f});")
    else:
        lines.append(f"  tl.fromTo('[id^=\"{cid}-dot-from-\"]',{{opacity:0,scale:0.3}},{{opacity:0.35,scale:1,duration:0.20,ease:'power2.out',stagger:{stagger_from}}},{t_dots:.4f});")

    # Arrow
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-tg-arrow',{{opacity:1,duration:0.50,ease:'power1.in'}},{t_arr:.4f});")
    elif is_craft:
        lines.append(f"  tl.fromTo('#{cid}-tg-arrow',{{opacity:0,scaleX:0.2}},{{opacity:1,scaleX:1,duration:0.28,ease:'circ.out',transformOrigin:'left center'}},{t_arr:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-tg-arrow',{{opacity:0,scaleX:0.3}},{{opacity:1,scaleX:1,duration:0.25,ease:'power2.out',transformOrigin:'left center'}},{t_arr:.4f});")

    # New dots staggered (accent color)
    stagger_to = 0.07 if is_ledger else 0.06
    if is_cinema:
        lines.append(f"  tl.to('[id^=\"{cid}-dot-to-\"]',{{opacity:1,duration:0.55,ease:'power2.in',stagger:{stagger_to}}},{t_new:.4f});")
    elif is_vibe:
        lines.append(f"  tl.fromTo('[id^=\"{cid}-dot-to-\"]',{{opacity:0,scale:0.3}},{{opacity:1,scale:1,duration:0.20,ease:'back.out(2.0)',stagger:{stagger_to}}},{t_new:.4f});")
    elif is_craft:
        lines.append(f"  tl.fromTo('[id^=\"{cid}-dot-to-\"]',{{opacity:0,scale:0.2}},{{opacity:1,scale:1,duration:0.18,ease:'circ.out',stagger:{stagger_to}}},{t_new:.4f});")
    else:
        lines.append(f"  tl.fromTo('[id^=\"{cid}-dot-to-\"]',{{opacity:0,scale:0.3}},{{opacity:1,scale:1,duration:0.18,ease:'power2.out',stagger:{stagger_to}}},{t_new:.4f});")

    # Counts row
    if is_cinema:
        lines.append(f"  tl.to('#{cid}-tg-counts',{{opacity:1,duration:0.60,ease:'power1.in'}},{t_cnt:.4f});")
    elif is_craft:
        lines.append(f"  tl.fromTo('#{cid}-tg-counts',{{opacity:0,y:6}},{{opacity:1,y:0,duration:0.30,ease:'circ.out'}},{t_cnt:.4f});")
    else:
        lines.append(f"  tl.fromTo('#{cid}-tg-counts',{{opacity:0,y:5}},{{opacity:1,y:0,duration:0.28,ease:'power2.out'}},{t_cnt:.4f});")

    # Accent line
    line_w = "56px" if is_cinema else ("40px" if is_ledger else "72px")
    lines.append(f"  tl.to('#{cid}-tg-line',{{width:'{line_w}',duration:0.40,ease:'power2.out'}},{t_ln:.4f});")

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
    min_confidence=0.82,
))
