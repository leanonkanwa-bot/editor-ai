"""
calendar_date — animated calendar or countdown for date/time references.

Three display modes based on what was extracted:

  month_date   — full month grid, target day highlighted with halo + pulse
  countdown    — "dans N jours/semaines" → number + unit with clock icon
  week_day     — horizontal week strip, target day highlighted

Tier-1 STRONG patterns (confidence 0.92, non-ambiguous forms):
  "le 15 mars", "le 15 du mois", "dans 3 semaines", "before Friday",
  "avant la fin du mois"

Tier-2 CONTEXTUAL bare patterns (confidence 0.55 + context bonus):
  "le 15" alone — needs a positive signal word in ±6 words to reach min_confidence.

Negative signals (any match → confidence 0.0, immediate reject):
  "étape", "point", "raison", "clé", "conseil", "verset", "chapitre", "page",
  "15ème", "15e", "numéro"
"""
from __future__ import annotations

import calendar as _cal
import datetime
import re

from app.engine.broll_registry import BRollType, register


# ── Month / day name tables ────────────────────────────────────────────────────
_MONTHS_FR = {
    "janvier":1,"février":2,"mars":3,"avril":4,"mai":5,"juin":6,
    "juillet":7,"août":8,"septembre":9,"octobre":10,"novembre":11,"décembre":12,
}
_MONTHS_EN = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
}
_MONTH_ALL = {**_MONTHS_FR, **_MONTHS_EN}

_DAYS_FR = {"lundi":0,"mardi":1,"mercredi":2,"jeudi":3,"vendredi":4,"samedi":5,"dimanche":6}
_DAYS_EN = {"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
_DAY_ALL = {**_DAYS_FR, **_DAYS_EN}

_MONTH_NAMES_FR = ["","Janvier","Février","Mars","Avril","Mai","Juin",
                   "Juillet","Août","Septembre","Octobre","Novembre","Décembre"]
_DAY_SHORT_FR   = ["L","M","M","J","V","S","D"]
_DAY_NAMES_FR   = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]


# ── Patterns ──────────────────────────────────────────────────────────────────

# Tier-1: unambiguous full-date forms
_FULL_DATE_FR = re.compile(
    r"\b(?:le|au|avant\s+le|dès\s+le|jusqu'?au|jusqu'au)\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<month>" + "|".join(_MONTHS_FR) + r")\b",
    re.IGNORECASE,
)
_FULL_DATE_EN = re.compile(
    r"\b(?:on|the)?\s*(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?:of\s+)?(?P<month>" + "|".join(_MONTHS_EN) + r")\b",
    re.IGNORECASE,
)
_DU_MOIS = re.compile(
    r"\b(?:le|au|avant\s+le|dès\s+le)\s+(?P<day>\d{1,2})\s+du\s+mois\b",
    re.IGNORECASE,
)
_DANS_N = re.compile(
    r"\bdans\s+(?P<n>\d+)\s+(?P<unit>jours?|semaines?|mois)\b",
    re.IGNORECASE,
)
_IN_N_EN = re.compile(
    r"\bin\s+(?P<n>\d+)\s+(?P<unit>days?|weeks?|months?)\b",
    re.IGNORECASE,
)
_FIN_PERIODE = re.compile(
    r"\b(?P<fin>avant\s+la\s+fin\s+(?:du\s+mois|de\s+la\s+semaine|de\s+l'?année|du\s+trimestre)"
    r"|by\s+(?:end\s+of\s+(?:the\s+)?(?:month|week|year)))\b",
    re.IGNORECASE,
)
_DAY_OF_WEEK = re.compile(
    r"\b(?P<day_name>" + "|".join(list(_DAYS_FR) + list(_DAYS_EN)) + r")"
    r"(?:\s+(?:prochain|dernier|next|last|matin|soir|morning|evening))?\b",
    re.IGNORECASE,
)

# Tier-2: bare "le N" (ambiguous — needs context)
_BARE_LE_N = re.compile(r"\b(?:le|au)\s+(?P<day>\d{1,2})\b", re.IGNORECASE)

# Context signal lists
_POSITIVE_CTX = re.compile(
    r"\b(rendez.?vous|réunion|meeting|call|live|lancement|ouverture|fermeture|"
    r"deadline|date.limite|délai|limite|clôture|inscription|fin|dernier)\b",
    re.IGNORECASE,
)
_NEGATIVE_CTX = re.compile(
    r"\b(étape|point|raison|clé|conseil|astuce|façon|type|sorte|verset|"
    r"chapitre|page|numéro|exemple|partie|argument|erreur|leçon|chose|"
    r"\d+ème|\d+e\b)\b",
    re.IGNORECASE,
)


def _ctx_words(words: list, word_idx: int, radius: int = 6) -> str:
    n = len(words)
    return " ".join(
        getattr(words[i], "text", "") for i in range(max(0, word_idx - radius), min(n, word_idx + radius + 1))
    )


# ── Extractor ─────────────────────────────────────────────────────────────────

def _extractor(match, words, word_idx: int) -> tuple[dict, float]:
    gd = match.groupdict()
    today = datetime.date.today()

    # Negative context check applies to all patterns
    ctx = _ctx_words(words, word_idx, 6)
    if _NEGATIVE_CTX.search(ctx):
        return {}, 0.0

    # Full date: "le 15 mars" / "April 5th"
    if gd.get("day") and gd.get("month"):
        day = int(gd["day"])
        month_str = gd["month"].lower()
        month = _MONTH_ALL.get(month_str, today.month)
        year = today.year
        if month < today.month or (month == today.month and day < today.day):
            year += 1
        return {"display_type": "month_date", "day": day, "month": month, "year": year}, 0.92

    # "le N du mois"
    if gd.get("day") and "unit" not in gd and "day_name" not in gd and "n" not in gd and "fin" not in gd:
        day = int(gd["day"])
        if not 1 <= day <= 31:
            return {}, 0.0
        return {
            "display_type": "month_date", "day": day,
            "month": today.month, "year": today.year,
        }, 0.90

    # "dans N jours/semaines" / "in N days"
    if gd.get("n") is not None:
        n = int(gd["n"])
        unit_raw = gd.get("unit", "jours").lower()
        if "semaine" in unit_raw or "week" in unit_raw:
            unit = "semaine"
            unit_display = f"semaine{'s' if n > 1 else ''}"
        elif "mois" in unit_raw or "month" in unit_raw:
            unit = "mois"
            unit_display = "mois"
        else:
            unit = "jour"
            unit_display = f"jour{'s' if n > 1 else ''}"
        return {"display_type": "countdown", "n": n, "unit": unit, "unit_display": unit_display}, 0.92

    # "avant la fin du mois" / "by end of month"
    if gd.get("fin"):
        text = gd["fin"].lower()
        if "semaine" in text or "week" in text:
            # Days to end of week
            days_left = 7 - today.weekday()
            return {"display_type": "countdown", "n": days_left, "unit": "jour",
                    "unit_display": f"jour{'s' if days_left != 1 else ''}"}, 0.88
        elif "mois" in text or "month" in text:
            import calendar
            _, last = calendar.monthrange(today.year, today.month)
            days_left = last - today.day
            return {"display_type": "countdown", "n": days_left, "unit": "jour",
                    "unit_display": f"jour{'s' if days_left != 1 else ''}"}, 0.88
        else:
            # année / year
            day_of_year = (datetime.date(today.year, 12, 31) - today).days
            return {"display_type": "countdown", "n": day_of_year, "unit": "jour",
                    "unit_display": "jours"}, 0.85

    # Day of week: "lundi", "Friday"
    if gd.get("day_name"):
        day_name = gd["day_name"]
        day_idx = _DAY_ALL.get(day_name.lower(), -1)
        if day_idx < 0:
            return {}, 0.0
        # Needs positive context signal to pass min_confidence
        conf = 0.55
        if _POSITIVE_CTX.search(ctx):
            conf = 0.80
        return {
            "display_type": "week_day", "day_idx": day_idx,
            "day_name": _DAY_NAMES_FR[day_idx],
        }, conf

    return {}, 0.0


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ej(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


def _calendar_grid_html(day: int, month: int, year: int) -> str:
    """Generate calendar grid cells for the given month, highlighting `day`."""
    first_weekday, days_in_month = _cal.monthrange(year, month)
    # Python's monthrange: 0=Mon, 6=Sun
    cells = []
    for _ in range(first_weekday):
        cells.append('<div class="cal-cell cal-empty"></div>')
    for d in range(1, days_in_month + 1):
        extra_cls = " cal-target" if d == day else ""
        cells.append(f'<div class="cal-cell{extra_cls}">{d}</div>')
    return "\n    ".join(cells)


# ── Render HTML ───────────────────────────────────────────────────────────────

def _render_html(params: dict, pack: dict, card_id: str) -> str:
    p = pack or {}
    bg       = p.get("bg",          "#1a1a1a")
    text_c   = p.get("text",        "#f1f1f1")
    text_sec = p.get("text_secondary", "rgba(255,255,255,0.45)")
    accent   = p.get("accent",      "#4cc9f0")
    font     = p.get("font",        '"Inter", sans-serif')
    fw       = p.get("font_weight", "800")
    radius   = p.get("radius",      "20px")
    shadow   = p.get("shadow",      "0 8px 32px rgba(0,0,0,0.4)")
    shadow_i = p.get("shadow_inset","")
    shadow_v = f"{shadow}, {shadow_i}" if shadow_i else shadow

    display_type = params.get("display_type", "month_date")

    # --- month_date: full calendar grid ---
    if display_type == "month_date":
        day   = params.get("day", 1)
        month = params.get("month", 1)
        year  = params.get("year", datetime.date.today().year)
        month_label = _MONTH_NAMES_FR[min(12, max(1, month))]
        cells_html  = _calendar_grid_html(day, month, year)
        weekday_html = "".join(
            f'<div class="cal-wd">{_e(d)}</div>' for d in _DAY_SHORT_FR
        )
        inner = f"""\
    <div class="cal-header">
      <span id="{card_id}-cal-month" class="cal-month-name">{_e(month_label)}</span>
      <span class="cal-year">{year}</span>
    </div>
    <div class="cal-weekdays">{weekday_html}</div>
    <div class="cal-grid" id="{card_id}-cal-grid">
    {cells_html}
    </div>"""

    # --- countdown: "dans N jours" ---
    elif display_type == "countdown":
        n            = params.get("n", 0)
        unit_display = _e(params.get("unit_display", "jours"))
        inner = f"""\
    <div class="cd-icon" id="{card_id}-cd-icon">
      <svg width="48" height="48" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="24" cy="24" r="21" stroke="{_e(accent)}" stroke-width="2.5" opacity="0.25"/>
        <circle id="{card_id}-cd-ring" cx="24" cy="24" r="21"
          stroke="{_e(accent)}" stroke-width="2.5"
          stroke-linecap="round" stroke-dasharray="132" stroke-dashoffset="132"
          transform="rotate(-90 24 24)"/>
        <text x="24" y="28" text-anchor="middle"
          font-family='{_e(font)}' font-size="16" font-weight="{fw}"
          fill="{_e(accent)}">⏰</text>
      </svg>
    </div>
    <div class="cd-amount-row">
      <span id="{card_id}-cd-n" class="cd-n">0</span>
    </div>
    <div class="cd-unit" id="{card_id}-cd-unit">{unit_display}</div>"""

    # --- week_day: horizontal week strip ---
    else:
        day_idx = params.get("day_idx", 0)
        cells = []
        for i, day_short in enumerate(_DAY_SHORT_FR):
            extra = " wd-target" if i == day_idx else ""
            name_full = _e(_DAY_NAMES_FR[i])
            cells.append(
                f'<div class="wd-cell{extra}" id="{card_id}-wd-{i}" '
                f'title="{name_full}">{_e(day_short)}</div>'
            )
        inner = f"""\
    <div class="wd-strip" id="{card_id}-wd-strip">
      {"".join(cells)}
    </div>
    <div class="wd-label" id="{card_id}-wd-label">{_e(params.get("day_name", ""))}</div>"""

    # --- CSS ---
    css = f"""\
.card[data-card-id="{card_id}"] .root {{
  width:100%; height:100%; display:flex; align-items:center; justify-content:center;
}}
.card[data-card-id="{card_id}"] .card-panel {{
  background:{bg}; border-radius:{radius}; padding:36px 44px;
  display:flex; flex-direction:column; align-items:center; gap:14px;
  box-shadow:{shadow_v}; position:relative; overflow:hidden;
}}
/* ── calendar ── */
.card[data-card-id="{card_id}"] .cal-header {{
  display:flex; align-items:baseline; gap:8px; opacity:0;
}}
.card[data-card-id="{card_id}"] .cal-month-name {{
  font-family:{font}; font-size:26px; font-weight:{fw}; color:{text_c};
  letter-spacing:0.04em;
}}
.card[data-card-id="{card_id}"] .cal-year {{
  font-family:{font}; font-size:18px; font-weight:400; color:{text_sec};
}}
.card[data-card-id="{card_id}"] .cal-weekdays {{
  display:grid; grid-template-columns:repeat(7,1fr); gap:4px; width:100%;
}}
.card[data-card-id="{card_id}"] .cal-wd {{
  font-family:{font}; font-size:13px; font-weight:700; color:{text_sec};
  text-align:center; letter-spacing:0.06em;
}}
.card[data-card-id="{card_id}"] .cal-grid {{
  display:grid; grid-template-columns:repeat(7,1fr); gap:4px; width:100%;
}}
.card[data-card-id="{card_id}"] .cal-cell {{
  font-family:{font}; font-size:18px; font-weight:500; color:{text_c};
  text-align:center; padding:6px 0; border-radius:6px; opacity:0;
  position:relative;
}}
.card[data-card-id="{card_id}"] .cal-empty {{ visibility:hidden; }}
.card[data-card-id="{card_id}"] .cal-target {{
  color:{accent}; font-weight:{fw};
  border:2px dashed {accent}; border-radius:8px;
}}
/* ── countdown ── */
.card[data-card-id="{card_id}"] .cd-icon {{ opacity:0; }}
.card[data-card-id="{card_id}"] .cd-amount-row {{
  display:flex; align-items:baseline; justify-content:center;
}}
.card[data-card-id="{card_id}"] .cd-n {{
  font-family:{font}; font-size:88px; font-weight:{fw};
  color:{text_c}; font-variant-numeric:tabular-nums; line-height:1; opacity:0;
}}
.card[data-card-id="{card_id}"] .cd-unit {{
  font-family:{font}; font-size:28px; font-weight:600; color:{accent};
  letter-spacing:0.06em; opacity:0; text-transform:uppercase;
}}
/* ── week day ── */
.card[data-card-id="{card_id}"] .wd-strip {{
  display:flex; gap:8px; align-items:stretch;
}}
.card[data-card-id="{card_id}"] .wd-cell {{
  font-family:{font}; font-size:18px; font-weight:600; color:{text_sec};
  width:40px; height:40px; display:flex; align-items:center; justify-content:center;
  border-radius:8px; opacity:0;
}}
.card[data-card-id="{card_id}"] .wd-target {{
  color:{accent}; border:2px dashed {accent}; font-weight:{fw};
}}
.card[data-card-id="{card_id}"] .wd-label {{
  font-family:{font}; font-size:22px; font-weight:{fw}; color:{accent};
  letter-spacing:0.08em; opacity:0; text-transform:uppercase;
}}"""

    return f"""\
<div class="card" data-card-id="{card_id}">
<style>
{css}
</style>
<div class="root">
  <div class="card-panel">
{inner}
  </div>
</div>
</div>"""


# ── Render GSAP ───────────────────────────────────────────────────────────────

def _render_gsap(params: dict, pack: dict, card_id: str, start: float, end: float) -> list[str]:
    cid = _ej(card_id)
    display_type = params.get("display_type", "month_date")
    t_in = round(start + 0.20, 4)
    lines: list[str] = []

    if display_type == "month_date":
        # 1. Month header slides in
        lines.append(
            f"  tl.fromTo('.card[data-card-id=\"{cid}\"] .cal-header',"
            f"{{opacity:0,y:-10}},"
            f"{{opacity:1,y:0,duration:0.30,ease:'power2.out'}},{t_in:.4f});"
        )
        # 2. Grid cells fade in with stagger (skip .cal-empty)
        t_grid = round(t_in + 0.20, 4)
        lines.append(
            f"  tl.to('.card[data-card-id=\"{cid}\"] .cal-cell',"
            f"{{opacity:0.45,duration:0.12,stagger:0.012,ease:'power1.out'}},{t_grid:.4f});"
        )
        # 3. Target cell pops with scale + full opacity
        t_target = round(t_in + 0.55, 4)
        lines.append(
            f"  tl.fromTo('.card[data-card-id=\"{cid}\"] .cal-target',"
            f"{{opacity:0,scale:0.6}},"
            f"{{opacity:1,scale:1.15,duration:0.35,ease:'back.out(1.7)'}},{t_target:.4f});"
        )
        lines.append(
            f"  tl.to('.card[data-card-id=\"{cid}\"] .cal-target',"
            f"{{scale:1,duration:0.20,ease:'power2.out'}},{round(t_target+0.35,4):.4f});"
        )
        # 4. Pulse ring on target (second pulse)
        t_pulse = round(t_target + 0.60, 4)
        lines.append(
            f"  tl.fromTo('.card[data-card-id=\"{cid}\"] .cal-target',"
            f"{{boxShadow:'0 0 0px rgba(76,201,240,0)'}},"
            f"{{boxShadow:'0 0 16px 4px rgba(76,201,240,0.55)',duration:0.35,ease:'sine.in',"
            f"yoyo:true,repeat:1}},{t_pulse:.4f});"
        )

    elif display_type == "countdown":
        n   = params.get("n", 0)
        dur = max(0.5, end - start)
        count_dur = round(min(1.4, max(0.5, dur * 0.35)), 3)
        # Clock icon fades in
        lines.append(
            f"  tl.to('#{cid}-cd-icon',{{opacity:1,duration:0.30,ease:'power1.out'}},{t_in:.4f});"
        )
        # SVG ring draws (stroke-dashoffset 132→0)
        lines.append(
            f"  tl.to('#{cid}-cd-ring',"
            f"{{'stroke-dashoffset':0,duration:{count_dur:.3f},ease:'power2.out'}},"
            f"{round(t_in+0.10,4):.4f});"
        )
        # Counter
        t_count = round(t_in + 0.15, 4)
        obj = f"_cd_{cid.replace('-','_')}"
        lines.append(
            f"  tl.to('#{cid}-cd-n',{{opacity:1,duration:0.20,ease:'power1.out'}},{t_count:.4f});"
        )
        lines.append(
            f"  (function(){{ var {obj}={{v:0}};"
            f" tl.to({obj},{{v:{n},duration:{count_dur:.3f},ease:'power2.out',"
            f"onUpdate:function(){{var el=document.querySelector('#{cid}-cd-n');"
            f"if(el) el.textContent=Math.round({obj}.v);}}}},{t_count:.4f}); }})();"
        )
        # Unit label
        t_unit = round(t_count + count_dur - 0.10, 4)
        lines.append(
            f"  tl.to('#{cid}-cd-unit',{{opacity:1,y:0,duration:0.25,ease:'power2.out'}},{t_unit:.4f});"
        )

    else:  # week_day
        day_idx = params.get("day_idx", 0)
        # Non-target cells fade in
        lines.append(
            f"  tl.to('.card[data-card-id=\"{cid}\"] .wd-cell:not(.wd-target)',"
            f"{{opacity:0.40,duration:0.20,stagger:0.04,ease:'power1.out'}},{t_in:.4f});"
        )
        # Target day pops
        t_target = round(t_in + 0.30, 4)
        lines.append(
            f"  tl.fromTo('#{cid}-wd-{day_idx}',"
            f"{{opacity:0,scale:0.7}},"
            f"{{opacity:1,scale:1.12,duration:0.35,ease:'back.out(1.7)'}},{t_target:.4f});"
        )
        lines.append(
            f"  tl.to('#{cid}-wd-{day_idx}',"
            f"{{scale:1,duration:0.20,ease:'power2.out'}},{round(t_target+0.35,4):.4f});"
        )
        # Label fades in below
        lines.append(
            f"  tl.to('#{cid}-wd-label',"
            f"{{opacity:1,duration:0.25,ease:'power1.out'}},{round(t_target+0.40,4):.4f});"
        )

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

_ALL_PATTERNS = [
    _FULL_DATE_FR, _FULL_DATE_EN, _DU_MOIS,
    _DANS_N, _IN_N_EN, _FIN_PERIODE,
    _DAY_OF_WEEK, _BARE_LE_N,
]

register(BRollType(
    name="calendar_date",
    patterns=_ALL_PATTERNS,
    extractor=_extractor,
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=3.5,
    preferred_zone="upper-data",
    min_confidence=0.75,
))
