"""Auto B-roll Generation — Feature 1.

Analyses each keep_segment and produces BrollSpec objects that render as
FFmpeg drawtext/drawbox overlays (STAT CARD, STEP CARDS, SPLIT COMPARISON,
PHONE MOCKUP, FLOW DIAGRAM, QUOTE CARD).

Rules:
  - Minimum 10s gap between any two b-roll overlays.
  - Maximum 3 overlays per 60 seconds.
  - Never in the first 5 seconds of the final edit.
  - Never cover the face (uses subject_position safe zones).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_NUMBER_METRIC = re.compile(
    r"\b(\d[\d,.]*)\s*(k|m|b|%|x|dollars?|\$|times?|hours?|days?|weeks?|months?|years?|"
    r"people|users?|customers?|views?|followers?|subscribers?|students?|sales?|leads?|"
    r"revenue|income|profit|conversions?)\b",
    re.IGNORECASE,
)
_STEP_WORDS = re.compile(
    r"\b(step|first|second|third|fourth|fifth|number|tip|rule|strategy|principle|"
    r"mistake|reason|way|hack|secret|lesson|key)\b",
    re.IGNORECASE,
)
_CONTRAST_WORDS = re.compile(
    r"\b(vs\.?|versus|but|however|instead|unlike|compared|difference|wrong|right|"
    r"myth|truth|before|after|old|new|bad|good)\b",
    re.IGNORECASE,
)
_APP_NAMES = re.compile(
    r"\b(instagram|tiktok|youtube|twitter|linkedin|facebook|notion|figma|canva|"
    r"shopify|stripe|zapier|slack|zoom|gmail|google|apple|iphone|android|app|"
    r"software|tool|platform|website|dashboard|screen|phone|device)\b",
    re.IGNORECASE,
)
_PROCESS_WORDS = re.compile(
    r"\b(process|system|framework|method|formula|flow|pipeline|workflow|steps|"
    r"stages?|phases?|cycle|loop|chain|sequence|path|journey|roadmap)\b",
    re.IGNORECASE,
)
_QUOTE_WORDS = re.compile(
    r'\b(says?|said|told|quote|according|they say|he says|she says|")\b',
    re.IGNORECASE,
)

_MIN_GAP_S = 10.0
_MAX_PER_60S = 3
_NEVER_BEFORE_S = 5.0


@dataclass
class BrollSpec:
    kind: str           # stat_card | step_card | split_comparison | phone_mockup | flow_diagram | quote_card
    at: float           # start time in edit timeline (seconds)
    duration: float     # display duration (seconds)
    params: dict[str, Any] = field(default_factory=dict)

    def to_filter_chain(
        self,
        target_w: int,
        target_h: int,
        safe_top_y_pct: float = 10.0,
        safe_bottom_y_pct: float = 72.0,
        t0: float | None = None,
        t1: float | None = None,
    ) -> str:
        at  = t0 if t0 is not None else self.at
        end = t1 if t1 is not None else (self.at + self.duration)
        enable = f"between(t,{at:.3f},{end:.3f})"
        # Place card in the safe lower zone (below chin) by default.
        # If the safe lower zone is too small (< 15% of frame), use upper zone.
        lower_y_pct  = safe_bottom_y_pct
        lower_h_pct  = 100.0 - safe_bottom_y_pct

        if lower_h_pct < 15.0:
            # Fall back to upper safe zone.
            lower_y_pct = 5.0

        card_y = int(target_h * lower_y_pct / 100)
        card_h = max(80, int(target_h * 0.20))
        card_w = int(target_w * 0.90)
        card_x = (target_w - card_w) // 2

        # Clamp card to frame.
        card_y = min(card_y, target_h - card_h - 10)
        card_y = max(10, card_y)

        if self.kind == "stat_card":
            return self._stat_card(target_w, target_h, card_x, card_y, card_w, card_h, enable)
        if self.kind == "step_card":
            return self._step_card(target_w, target_h, card_x, card_y, card_w, card_h, enable)
        if self.kind == "split_comparison":
            return self._split_comparison(target_w, target_h, enable)
        if self.kind == "phone_mockup":
            return self._phone_mockup(target_w, target_h, card_x, card_y, card_w, card_h, enable)
        if self.kind == "flow_diagram":
            return self._flow_diagram(target_w, target_h, card_x, card_y, card_w, card_h, enable)
        if self.kind == "quote_card":
            return self._quote_card(target_w, target_h, card_x, card_y, card_w, card_h, enable)
        return ""

    def _stat_card(self, w, h, cx, cy, cw, ch, enable) -> str:
        number = str(self.params.get("number", ""))
        label  = str(self.params.get("label", ""))[:30]
        fs_num = max(28, min(72, h // 10))
        fs_lbl = max(16, min(32, h // 22))
        filters = [
            # Dark card background.
            f"drawbox=x={cx}:y={cy}:w={cw}:h={ch}:"
            f"color=0x0A0A0A@0.92:t=fill:enable={enable}",
            # Salmon accent bar on left edge.
            f"drawbox=x={cx}:y={cy}:w=4:h={ch}:"
            f"color=0xFF7751@1.0:t=fill:enable={enable}",
            # Number (large).
            f"drawtext=text={_esc(number)}:"
            f"fontsize={fs_num}:fontcolor=white:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"x={cx + 16}:y={cy + 8}:enable={enable}",
            # Label (smaller, muted).
            f"drawtext=text={_esc(label)}:"
            f"fontsize={fs_lbl}:fontcolor=0xFFFFFF@0.7:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            f"x={cx + 16}:y={cy + fs_num + 14}:enable={enable}",
        ]
        return ",".join(filters)

    def _step_card(self, w, h, cx, cy, cw, ch, enable) -> str:
        step_num = str(self.params.get("step_num", "1"))
        text     = str(self.params.get("text", ""))[:40]
        fs       = max(18, min(36, h // 18))
        filters = [
            f"drawbox=x={cx}:y={cy}:w={cw}:h={ch}:"
            f"color=0x0A0A0A@0.90:t=fill:enable={enable}",
            # Salmon circle indicator.
            f"drawbox=x={cx + 8}:y={cy + (ch - 36) // 2}:w=36:h=36:"
            f"color=0xFF7751@1.0:t=fill:enable={enable}",
            # Step number inside circle.
            f"drawtext=text={_esc(step_num)}:"
            f"fontsize={max(16,fs - 4)}:fontcolor=white:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"x={cx + 8 + 18 - (len(step_num) * 5)}:y={cy + (ch - 36) // 2 + 8}:"
            f"enable={enable}",
            # Step text.
            f"drawtext=text={_esc(text)}:"
            f"fontsize={fs}:fontcolor=white:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"x={cx + 56}:y={cy + (ch - fs) // 2}:enable={enable}",
        ]
        return ",".join(filters)

    def _split_comparison(self, w, h, enable) -> str:
        left_label  = str(self.params.get("left", "OLD WAY"))[:20]
        right_label = str(self.params.get("right", "NEW WAY"))[:20]
        bar_h = max(50, h // 12)
        cy    = h - bar_h - 20
        fs    = max(14, min(24, h // 30))
        filters = [
            # Red half (left).
            f"drawbox=x=0:y={cy}:w={w // 2}:h={bar_h}:"
            f"color=0xcc2222@0.85:t=fill:enable={enable}",
            # Green half (right).
            f"drawbox=x={w // 2}:y={cy}:w={w // 2}:h={bar_h}:"
            f"color=0x22aa44@0.85:t=fill:enable={enable}",
            f"drawtext=text={_esc(left_label)}:"
            f"fontsize={fs}:fontcolor=white:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"x=(w/4 - text_w/2):y={cy + (bar_h - fs) // 2}:enable={enable}",
            f"drawtext=text={_esc(right_label)}:"
            f"fontsize={fs}:fontcolor=white:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"x=(3*w/4 - text_w/2):y={cy + (bar_h - fs) // 2}:enable={enable}",
        ]
        return ",".join(filters)

    def _phone_mockup(self, w, h, cx, cy, cw, ch, enable) -> str:
        label = str(self.params.get("label", ""))[:30]
        fs    = max(14, min(22, h // 28))
        # Draw a simple rounded-ish rectangle to represent a phone screen.
        pw, ph = max(80, cw // 3), max(140, ch * 2)
        px = cx + cw - pw - 10
        py = max(10, cy - ph // 2)
        filters = [
            f"drawbox=x={px}:y={py}:w={pw}:h={ph}:"
            f"color=0x1A1A1A@0.95:t=fill:enable={enable}",
            f"drawbox=x={px + 2}:y={py + 2}:w={pw - 4}:h={ph - 4}:"
            f"color=0xFF7751@1.0:t=2:enable={enable}",
            f"drawtext=text={_esc(label)}:"
            f"fontsize={fs}:fontcolor=white:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"x={cx + 10}:y={cy + (ch - fs) // 2}:enable={enable}",
        ]
        return ",".join(filters)

    def _flow_diagram(self, w, h, cx, cy, cw, ch, enable) -> str:
        steps = self.params.get("steps", [])[:3]
        if not steps:
            return ""
        fs  = max(12, min(18, h // 35))
        sw  = cw // max(1, len(steps))
        filters = [
            f"drawbox=x={cx}:y={cy}:w={cw}:h={ch}:"
            f"color=0x0A0A0A@0.90:t=fill:enable={enable}",
        ]
        for i, step in enumerate(steps):
            bx = cx + i * sw + 4
            filters.append(
                f"drawbox=x={bx}:y={cy + 8}:w={sw - 8}:h={ch - 16}:"
                f"color=0x1A1A1A@0.95:t=fill:enable={enable}"
            )
            filters.append(
                f"drawtext=text={_esc(str(step)[:15])}:"
                f"fontsize={fs}:fontcolor=white:"
                f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
                f"x={bx + 6}:y={cy + (ch - fs) // 2}:enable={enable}"
            )
            if i < len(steps) - 1:
                ax = bx + sw - 4
                filters.append(
                    f"drawtext=text=>:"
                    f"fontsize={fs}:fontcolor=0xFF7751:"
                    f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
                    f"x={ax}:y={cy + (ch - fs) // 2}:enable={enable}"
                )
        return ",".join(filters)

    def _quote_card(self, w, h, cx, cy, cw, ch, enable) -> str:
        text = str(self.params.get("text", ""))[:60]
        author = str(self.params.get("author", ""))[:30]
        fs   = max(16, min(28, h // 24))
        fs_a = max(12, min(18, h // 36))
        filters = [
            f"drawbox=x={cx}:y={cy}:w={cw}:h={ch}:"
            f"color=0x0A0A0A@0.92:t=fill:enable={enable}",
            f"drawbox=x={cx}:y={cy}:w={cw}:h=3:"
            f"color=0xFF7751@1.0:t=fill:enable={enable}",
            f"drawtext=text={_esc(text)}:"
            f"fontsize={fs}:fontcolor=white:"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf:"
            f"x={cx + 12}:y={cy + 14}:enable={enable}",
        ]
        if author:
            filters.append(
                f"drawtext=text=- {_esc(author)}:"
                f"fontsize={fs_a}:fontcolor=0xFF7751:"
                f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
                f"x={cx + 12}:y={cy + ch - fs_a - 10}:enable={enable}"
            )
        return ",".join(filters)


def _esc(text: str) -> str:
    """Escape text for FFmpeg drawtext."""
    return (
        text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
            .replace("%", "\\%")
    )


class BrollGenerator:
    """Generates BrollSpec overlays for a list of transcript segments."""

    def generate(
        self,
        segments: list[dict[str, Any]],
        edit_timeline_map: dict[float, float],  # source_start -> edit_start
        total_edit_duration: float,
        subject_position: dict[str, float] | None = None,
    ) -> list[BrollSpec]:
        specs: list[BrollSpec] = []
        last_at: float = -_MIN_GAP_S
        count_per_window: dict[int, int] = {}  # 60s window index → count

        for seg in segments:
            try:
                seg_start = float(seg.get("start", 0))
                seg_text  = str(seg.get("text", ""))
            except (TypeError, ValueError):
                continue

            # Map source time to edit timeline.
            edit_at = _map_to_edit(seg_start, edit_timeline_map, total_edit_duration)
            if edit_at is None:
                continue

            if edit_at < _NEVER_BEFORE_S:
                continue
            if edit_at - last_at < _MIN_GAP_S:
                continue

            # Rate limit: max _MAX_PER_60S per 60s window.
            win = int(edit_at // 60)
            if count_per_window.get(win, 0) >= _MAX_PER_60S:
                continue

            spec = _classify_segment(seg_text, edit_at)
            if spec is None:
                continue

            # Clamp duration to not exceed segment length.
            try:
                seg_end = float(seg.get("end", seg_start + 5))
            except (TypeError, ValueError):
                seg_end = seg_start + 5
            spec.duration = min(spec.duration, max(2.0, seg_end - seg_start - 0.5))

            specs.append(spec)
            last_at = edit_at
            count_per_window[win] = count_per_window.get(win, 0) + 1

        return specs


def _classify_segment(text: str, at: float) -> BrollSpec | None:
    """Return the best BrollSpec for a segment, or None."""
    # Priority 1: number + metric → STAT CARD.
    m = _NUMBER_METRIC.search(text)
    if m:
        number = m.group(1) + m.group(2).upper()
        label  = text[:50].strip()
        return BrollSpec("stat_card", at, 3.5, {"number": number, "label": label})

    # Priority 2: quote words → QUOTE CARD.
    if _QUOTE_WORDS.search(text):
        return BrollSpec("quote_card", at, 3.0, {"text": text[:60].strip(), "author": ""})

    # Priority 3: contrast words → SPLIT COMPARISON.
    if _CONTRAST_WORDS.search(text):
        return BrollSpec("split_comparison", at, 3.0, {"left": "BEFORE", "right": "AFTER"})

    # Priority 4: step words → STEP CARD.
    m = _STEP_WORDS.search(text)
    if m:
        return BrollSpec("step_card", at, 3.0, {"step_num": "1", "text": text[:40].strip()})

    # Priority 5: process words → FLOW DIAGRAM.
    if _PROCESS_WORDS.search(text):
        words_list = text.split()[:9]
        steps = [" ".join(words_list[i:i+3]) for i in range(0, min(9, len(words_list)), 3)]
        return BrollSpec("flow_diagram", at, 3.5, {"steps": steps[:3]})

    # Priority 6: app names → PHONE MOCKUP.
    if _APP_NAMES.search(text):
        return BrollSpec("phone_mockup", at, 3.0, {"label": text[:30].strip()})

    return None


def _map_to_edit(
    source_t: float,
    edit_timeline_map: dict[float, float],
    total_edit_duration: float,
) -> float | None:
    """Map a source timestamp to edit-timeline time using the segment map."""
    if not edit_timeline_map:
        return None
    sorted_keys = sorted(edit_timeline_map.keys())
    # Find the segment that contains source_t.
    for i, key in enumerate(sorted_keys):
        next_key = sorted_keys[i + 1] if i + 1 < len(sorted_keys) else key + 9999
        if key <= source_t < next_key:
            offset = source_t - key
            edit_t = edit_timeline_map[key] + offset
            if 0 <= edit_t <= total_edit_duration:
                return edit_t
    return None
