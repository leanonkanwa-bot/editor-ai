"""
Adaptive graphics engine — selects the right motion graphic for each video
moment and returns FFmpeg filter strings. Pure drawtext/drawbox: no PIL,
no extra input streams required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_FONTS_DIR = "/usr/local/share/fonts/leanlead"


# ── helpers ───────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
            .replace("'",  "\\'")
            .replace(":",  "\\:")
            .replace("/",  "\\/")
            .replace("[",  "\\[")
            .replace("]",  "\\]")
    )


def _wrap(text: str, max_chars: int = 28) -> str:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars and cur:
            lines.append(cur.strip())
            cur = w + " "
        else:
            cur += w + " "
    if cur.strip():
        lines.append(cur.strip())
    return "\\n".join(lines)


# ── filter generators ─────────────────────────────────────────────────────────

def _stat_callout_filters(
    number: str, label: str,
    t0: float, t1: float,
    target_w: int, target_h: int,
    short_form: bool,
) -> str:
    enable   = f"enable=between(t,{t0:.3f},{t1:.3f})"
    num_size = int(target_h * 0.13)
    lbl_size = int(target_h * 0.035)
    line_h   = max(3, int(target_h * 0.003))
    pad      = int(target_w * 0.04)

    if short_form:
        box_x = int(target_w * 0.10)
        box_y = int(target_h * 0.68)
        box_w = int(target_w * 0.80)
        box_h = int(target_h * 0.18)
    else:
        box_x = int(target_w * 0.55)
        box_y = int(target_h * 0.25)
        box_w = int(target_w * 0.38)
        box_h = int(target_h * 0.50)

    cx        = box_x + box_w // 2
    text_y_n  = box_y + int(box_h * 0.20)
    text_y_l  = box_y + int(box_h * 0.72)

    return ",".join([
        f"drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:color=black@0.75:t=fill:{enable}",
        f"drawbox=x={box_x+pad}:y={box_y+8}:w={box_w-2*pad}:h={line_h}:color=0xFF7751:t=fill:{enable}",
        (f"drawtext=text='{_esc(number)}'"
         f":fontfile={_FONTS_DIR}/Poppins-ExtraBold.ttf"
         f":fontcolor=white:fontsize={num_size}"
         f":x={cx}-text_w/2:y={text_y_n}:{enable}"),
        (f"drawtext=text='{_esc(label)}'"
         f":fontfile={_FONTS_DIR}/Poppins-Bold.ttf"
         f":fontcolor=0xAAAAAA:fontsize={lbl_size}"
         f":x={cx}-text_w/2:y={text_y_l}:{enable}"),
        f"drawbox=x={box_x+pad}:y={box_y+box_h-8-line_h}:w={box_w-2*pad}:h={line_h}:color=0xFF7751:t=fill:{enable}",
    ])


def _split_truth_filters(
    wrong: str, right: str,
    t0: float, t1: float,
    target_w: int, target_h: int,
) -> str:
    enable   = f"enable=between(t,{t0:.3f},{t1:.3f})"
    half_w   = target_w // 2
    lbl_size = int(target_h * 0.045)
    txt_size = int(target_h * 0.032)
    hdr_y    = int(target_h * 0.35)
    txt_y    = int(target_h * 0.44)
    pad      = int(target_w * 0.03)
    lx       = half_w // 2
    rx       = half_w + half_w // 2

    return ",".join([
        f"drawbox=x=0:y=0:w={half_w}:h={target_h}:color=red@0.18:t=fill:{enable}",
        f"drawbox=x={half_w}:y=0:w={half_w}:h={target_h}:color=green@0.18:t=fill:{enable}",
        f"drawbox=x={half_w-1}:y=0:w=2:h={target_h}:color=white@0.4:t=fill:{enable}",
        (f"drawtext=text='What they think'"
         f":fontfile={_FONTS_DIR}/Poppins-Bold.ttf"
         f":fontcolor=0xFF5555:fontsize={lbl_size}"
         f":x={lx}-text_w/2:y={hdr_y}:{enable}"),
        (f"drawtext=text='What works'"
         f":fontfile={_FONTS_DIR}/Poppins-Bold.ttf"
         f":fontcolor=0x55FF55:fontsize={lbl_size}"
         f":x={rx}-text_w/2:y={hdr_y}:{enable}"),
        (f"drawtext=text='{_esc(_wrap(wrong, 22))}'"
         f":fontfile={_FONTS_DIR}/Poppins-Bold.ttf"
         f":fontcolor=white:fontsize={txt_size}"
         f":x={pad}:y={txt_y}:{enable}"),
        (f"drawtext=text='{_esc(_wrap(right, 22))}'"
         f":fontfile={_FONTS_DIR}/Poppins-Bold.ttf"
         f":fontcolor=white:fontsize={txt_size}"
         f":x={half_w+pad}:y={txt_y}:{enable}"),
    ])


def _quote_slam_filters(
    text: str,
    t0: float, t1: float,
    target_w: int, target_h: int,
) -> str:
    enable    = f"enable=between(t,{t0:.3f},{t1:.3f})"
    font_size = int(target_h * 0.055)
    return ",".join([
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.82:t=fill:{enable}",
        (f"drawtext=text='{_esc(_wrap(text, 30))}'"
         f":fontfile={_FONTS_DIR}/Poppins-Bold.ttf"
         f":fontcolor=white:fontsize={font_size}"
         f":x=(w-text_w)/2:y=(h-text_h)/2:{enable}"),
    ])


def _step_reveal_filters(
    steps: list[str], current_step: int,
    t0: float, t1: float,
    target_w: int, target_h: int,
    short_form: bool,
) -> str:
    enable    = f"enable=between(t,{t0:.3f},{t1:.3f})"
    step_size = int(target_h * 0.042)
    num_size  = int(target_h * 0.048)
    line_h    = int(step_size * 1.8)

    if short_form:
        sx, sy = int(target_w * 0.08), int(target_h * 0.65)
    else:
        sx, sy = int(target_w * 0.55), int(target_h * 0.20)

    card_h = len(steps) * line_h + 20
    parts = [
        f"drawbox=x={sx-12}:y={sy-10}:w={int(target_w*0.40)}:h={card_h}:color=black@0.75:t=fill:{enable}",
    ]
    for idx, step_text in enumerate(steps):
        y        = sy + idx * line_h
        current  = idx == current_step
        nc = "0xFF7751" if current else "0x666666"
        tc = "white"   if current else "0x888888"
        parts.append(
            f"drawtext=text='{idx+1}'"
            f":fontfile={_FONTS_DIR}/Poppins-ExtraBold.ttf"
            f":fontcolor={nc}:fontsize={num_size}:x={sx}:y={y}:{enable}"
        )
        parts.append(
            f"drawtext=text='{_esc(step_text[:36])}'"
            f":fontfile={_FONTS_DIR}/Poppins-Bold.ttf"
            f":fontcolor={tc}:fontsize={step_size}"
            f":x={sx+num_size+12}:y={y+4}:{enable}"
        )
    return ",".join(parts)


# ── GraphicSpec ───────────────────────────────────────────────────────────────

@dataclass
class GraphicSpec:
    kind: str        # stat_callout | split_truth | step_reveal | quote_slam | empty
    at: float
    duration: float
    params: dict = field(default_factory=dict)
    short_form: bool = False

    def to_filter_chain(
        self,
        target_w: int,
        target_h: int,
        t0: float | None = None,
        t1: float | None = None,
    ) -> str:
        t0 = t0 if t0 is not None else self.at
        t1 = t1 if t1 is not None else self.at + self.duration
        p  = self.params

        if self.kind == "stat_callout":
            return _stat_callout_filters(
                p.get("number", "?"), p.get("label", ""),
                t0, t1, target_w, target_h, self.short_form,
            )
        if self.kind == "split_truth":
            return _split_truth_filters(
                p.get("wrong", ""), p.get("right", ""),
                t0, t1, target_w, target_h,
            )
        if self.kind == "quote_slam":
            return _quote_slam_filters(
                p.get("text", ""), t0, t1, target_w, target_h,
            )
        if self.kind == "step_reveal":
            return _step_reveal_filters(
                p.get("steps", []), p.get("current", 0),
                t0, t1, target_w, target_h, self.short_form,
            )
        return ""


# ── content-type detection ────────────────────────────────────────────────────

_COACHING_RE   = re.compile(
    r"\b(client|business|revenue|sales|strategy|leads?|close|offer|funnel|"
    r"prospect|pitch|deal|crm|kpi|agency|retainer)\b", re.I)
_EDUCATION_RE  = re.compile(
    r"\b(how to|learn|understand|explain|works?|because|reason|science|"
    r"study|research|concept|method|principle|framework)\b", re.I)
_STORY_RE      = re.compile(
    r"\b(I was|when I|one day|I remember|there was|at that point|"
    r"back when|I used to|years ago)\b", re.I)
_MOTIVATION_RE = re.compile(
    r"\b(you can|believe|possible|mindset|potential|dream|vision|"
    r"achieve|inspire|courage|decide|commit)\b", re.I)


def detect_content_type(text: str) -> str:
    scores = {
        "coaching":   len(_COACHING_RE.findall(text)),
        "education":  len(_EDUCATION_RE.findall(text)),
        "story":      len(_STORY_RE.findall(text)),
        "motivation": len(_MOTIVATION_RE.findall(text)),
    }
    return max(scores, key=lambda k: scores[k]) if any(scores.values()) else "education"


# ── segment analysers ─────────────────────────────────────────────────────────

_NUMBER_RE   = re.compile(r"\b(\d+[\d,.]*%?|\$[\d,.]+[kKmMbB]?)\b")
_CONTRAST_RE = re.compile(
    r"\b(but|however|wrong|mistake|instead|actually|truth|myth|"
    r"most people|they think|reality|not)\b", re.I)
_STEP_RE     = re.compile(
    r"\b(first|second|third|step\s*\d|number\s*\d|\d\s*[.:)]\s*\w)\b", re.I)


def _extract_number(text: str) -> tuple[str, str]:
    m = _NUMBER_RE.search(text)
    if not m:
        return "", ""
    number    = m.group(0)
    remainder = (text[:m.start()] + text[m.end():]).strip()
    label     = " ".join(remainder.split()[:5])
    return number, label


def _extract_contrast(text: str) -> tuple[str, str]:
    m = _CONTRAST_RE.search(text)
    if not m:
        return "", ""
    wrong = " ".join(text[:m.start()].strip().split()[-6:])
    right = " ".join(text[m.end():].strip().split()[:8])
    return wrong, right


def _extract_steps(text: str) -> list[str]:
    parts = re.split(
        r"\b(?:first|second|third|fourth|step\s*\d|[123][.:)]\s*)", text, flags=re.I
    )
    steps = [" ".join(p.strip().split()[:6]) for p in parts if p.strip()]
    return steps[:4] if len(steps) >= 2 else []


# ── selector ──────────────────────────────────────────────────────────────────

_MAX_PER_TYPE      = 2
_GRAPHICS_PER_MIN  = 4
_MIN_GAP_S         = 8.0

_CONTENT_PREFS: dict[str, list[str]] = {
    "coaching":   ["stat_callout", "step_reveal", "split_truth", "quote_slam"],
    "education":  ["split_truth",  "step_reveal", "stat_callout", "quote_slam"],
    "story":      ["quote_slam",   "stat_callout"],
    "motivation": ["quote_slam",   "stat_callout", "split_truth"],
}


class GraphicSelector:
    def __init__(self) -> None:
        self.last_kind: str | None = None
        self.last_at: float        = -999.0
        self.kind_counts: dict[str, int] = {}
        self.total: int            = 0
        self.content_type: str     = "education"

    def configure(self, content_type: str) -> None:
        self.content_type = content_type

    def select(
        self,
        segment_text: str,
        segment_role: str,
        at: float,
        duration: float,
        video_context: dict[str, Any],
    ) -> GraphicSpec | None:
        if not segment_text.strip():
            return None
        if at - self.last_at < _MIN_GAP_S:
            return None

        total_dur = video_context.get("total_duration", 60.0)
        max_total = max(1, int(total_dur / 60 * _GRAPHICS_PER_MIN))
        if self.total >= max_total:
            return None

        short_form = video_context.get("short_form", True)
        spec       = self._classify(segment_text, segment_role, at, duration, short_form)
        if spec is None:
            return None

        # Enforce per-type cap — try next preference
        if self.kind_counts.get(spec.kind, 0) >= _MAX_PER_TYPE:
            for alt in _CONTENT_PREFS.get(self.content_type, ["quote_slam"]):
                if alt != spec.kind and self.kind_counts.get(alt, 0) < _MAX_PER_TYPE:
                    spec = self._build_quote_slam(segment_text, at, duration, short_form)
                    if spec is not None:
                        spec = GraphicSpec(
                            kind=alt, at=at, duration=duration,
                            params=spec.params, short_form=short_form,
                        )
                    break
            else:
                return None

        if spec is None or spec.kind == self.last_kind:
            return None

        self.last_kind = spec.kind
        self.last_at   = at
        self.kind_counts[spec.kind] = self.kind_counts.get(spec.kind, 0) + 1
        self.total    += 1
        return spec

    def _classify(
        self,
        text: str,
        role: str,
        at: float,
        duration: float,
        short_form: bool,
    ) -> GraphicSpec | None:
        role_up = role.upper()

        if role_up in {"PRINCIPE", "PAYOFF", "REALISATION", "REFRAME"}:
            return self._build_quote_slam(text, at, duration, short_form)

        number, label = _extract_number(text)
        if number:
            return GraphicSpec(
                kind="stat_callout", at=at, duration=duration,
                params={"number": number, "label": label},
                short_form=short_form,
            )

        wrong, right = _extract_contrast(text)
        if wrong and right:
            return GraphicSpec(
                kind="split_truth", at=at, duration=duration,
                params={"wrong": wrong, "right": right},
                short_form=short_form,
            )

        steps = _extract_steps(text)
        if steps:
            return GraphicSpec(
                kind="step_reveal", at=at, duration=duration,
                params={"steps": steps, "current": 0},
                short_form=short_form,
            )

        if role_up in {"HOOK", "HISTOIRE", "OPEN_LOOP"}:
            return None

        if len(text.split()) <= 20:
            return self._build_quote_slam(text, at, duration, short_form)

        return None

    def _build_quote_slam(
        self,
        text: str,
        at: float,
        duration: float,
        short_form: bool,
    ) -> GraphicSpec | None:
        words = text.split()
        if len(words) > 25:
            text = " ".join(words[:20]) + "…"
        return GraphicSpec(
            kind="quote_slam", at=at,
            duration=min(duration, 4.0),
            params={"text": text},
            short_form=short_form,
        )


def build_video_context(transcript: dict[str, Any], plan: Any) -> dict[str, Any]:
    full_text    = transcript.get("text", "")
    content_type = detect_content_type(full_text)
    short_form   = getattr(plan, "format", "short") == "short"
    return {
        "total_duration": float(transcript.get("duration", 60.0)),
        "content_type":   content_type,
        "short_form":     short_form,
        "segment_roles":  [
            s.get("beat", "")
            for s in (getattr(plan, "script_structure", None) or [])
        ],
    }
