"""Edit report PDF generator — REPORT_ONLY mode.

HTML template rendered by Chrome headless to produce a PDF with
LeanRetention visual identity (dark header, orange accent #FF5736).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path


_FR_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

_BEAT_LABELS: dict[str, str] = {
    "hook":          "Accroche",
    "amplify":       "Amplification",
    "context":       "Contexte",
    "tension":       "Tension",
    "story":         "Récit",
    "realization":   "Réalisation",
    "principle":     "Principe",
    "payoff":        "Payoff",
    "emotional_end": "Conclusion",
}

# (row-border CSS class, pill CSS class) per beat key
_BEAT_CSS: dict[str, tuple[str, str]] = {
    "hook":          ("b-hook",  "p-hook"),
    "amplify":       ("b-amp",   "p-amp"),
    "context":       ("b-ctx",   "p-ctx"),
    "tension":       ("b-amp",   "p-amp"),
    "story":         ("b-story", "p-story"),
    "realization":   ("b-real",  "p-real"),
    "principle":     ("b-prin",  "p-prin"),
    "payoff":        ("b-pay",   "p-pay"),
    "emotional_end": ("b-end",   "p-end"),
}

# (display label, chip CSS class) per drop reason prefix
_REASON_MAP: dict[str, tuple[str, str]] = {
    "filler_word":              ("Filler",       "chip-filler"),
    "pause_after_filler":       ("Pause",        "chip-filler"),
    "llm_filler":               ("Filler",       "chip-filler"),
    "llm_repetition":           ("Répétition",   "chip-rep"),
    "llm_false_start":          ("Faux départ",  "chip-fs"),
    "llm_premature_conclusive": ("Conclusion",   "chip-fs"),
    "stutter":                  ("Répétition",   "chip-rep"),
}

_BRAND_ICON = (
    '<svg width="30" height="30" viewBox="0 0 32 32" fill="none"'
    ' xmlns="http://www.w3.org/2000/svg">'
    '<rect width="32" height="32" rx="7" fill="#FF5736" fill-opacity="0.15"/>'
    '<path d="M10 9 L10 23 L23 16 Z" fill="#FF5736" opacity="0.9"/>'
    '<path d="M13 19 L17 14 L20 16 L24 11" stroke="#FF5736"'
    ' stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
    ' fill="none" opacity="0.6"/>'
    '</svg>'
)

_CSS = """\
@page { size: A4; margin: 0; }
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --ink: #0F1117; --paper: #FAFAF9; --accent: #FF5736;
  --dim: #6B6360; --rule: #E8E3DF; --surface: #FFFFFF;
}
html, body {
  width: 794px; background: var(--paper); color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 12px; line-height: 1.45;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
.hd {
  background: var(--ink); padding: 36px 48px 30px;
  position: relative; overflow: hidden; page-break-inside: avoid;
}
.hd::before {
  content: ""; position: absolute; inset: 0;
  background: radial-gradient(ellipse 60% 80% at 100% 0%, rgba(255,87,54,0.08), transparent 70%);
}
.hd-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.brand { display: flex; align-items: center; gap: 10px; }
.brand-name { font-size: 15px; font-weight: 700; color: #fff; letter-spacing: -0.01em; }
.doc-badge {
  font-size: 9px; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--accent); border: 1px solid rgba(255,87,54,0.35); padding: 4px 10px; border-radius: 3px;
}
.hd-title { font-size: 28px; font-weight: 800; color: #fff; letter-spacing: -0.03em; margin: 0 0 6px; }
.hd-title em { color: var(--accent); font-style: normal; }
.hd-meta { font-size: 11.5px; color: rgba(255,255,255,0.45); }
.hd-meta strong { color: rgba(255,255,255,0.70); font-weight: 500; }
.hd-stats {
  display: flex; gap: 24px; margin-top: 20px;
  padding-top: 18px; border-top: 1px solid rgba(255,255,255,0.07);
}
.stat { display: flex; flex-direction: column; gap: 2px; }
.stat-n {
  font-size: 20px; font-weight: 800; color: #fff;
  letter-spacing: -0.03em; font-variant-numeric: tabular-nums;
}
.stat-l { font-size: 9px; color: rgba(255,255,255,0.38); text-transform: uppercase; letter-spacing: 0.10em; }
.stat-div { width: 1px; background: rgba(255,255,255,0.10); margin: 2px 0; }
.body { padding: 0 48px 48px; }
.sec { display: flex; align-items: center; gap: 12px; margin: 34px 0 4px; }
.sec-label { font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); white-space: nowrap; }
.sec-rule { flex: 1; height: 1px; background: var(--rule); }
.sec-sub { font-size: 11px; color: var(--dim); font-style: italic; margin: 0 0 18px; line-height: 1.5; }
.beats { display: flex; flex-direction: column; gap: 2px; margin-bottom: 4px; }
.beat-row {
  display: flex; align-items: baseline;
  border-left: 3px solid transparent; padding: 8px 12px 8px 14px;
  border-radius: 0 4px 4px 0; page-break-inside: avoid;
}
.beat-row:nth-child(odd) { background: rgba(255,255,255,0.55); }
.beat-pill {
  font-size: 8px; font-weight: 700; letter-spacing: 0.09em; text-transform: uppercase;
  padding: 3px 7px; border-radius: 3px; white-space: nowrap;
  min-width: 94px; text-align: center; flex-shrink: 0;
}
.beat-ts {
  font-family: "Courier New", Courier, monospace;
  font-size: 10px; color: var(--dim); padding: 0 14px;
  white-space: nowrap; flex-shrink: 0; font-variant-numeric: tabular-nums; min-width: 100px;
}
.beat-excerpt { font-size: 11px; color: #2A2420; font-style: italic; line-height: 1.4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.b-hook { border-color: #FF5736; } .p-hook { background: #FFF2EE; color: #C2401A; }
.b-amp  { border-color: #F59E0B; } .p-amp  { background: #FFFBEB; color: #92400E; }
.b-ctx  { border-color: #3B82F6; } .p-ctx  { background: #EFF6FF; color: #1D4ED8; }
.b-story{ border-color: #3B82F6; } .p-story{ background: #EFF6FF; color: #1D4ED8; }
.b-real { border-color: #10B981; } .p-real { background: #ECFDF5; color: #065F46; }
.b-prin { border-color: #10B981; } .p-prin { background: #ECFDF5; color: #065F46; }
.b-pay  { border-color: #FF5736; } .p-pay  { background: #FFF2EE; color: #C2401A; }
.b-end  { border-color: #8B5CF6; } .p-end  { background: #F5F3FF; color: #5B21B6; }
.key-block {
  margin-top: 14px; padding: 10px 14px;
  border-left: 3px solid var(--rule); background: var(--surface); page-break-inside: avoid;
}
.key-label { font-size: 9px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--dim); margin-bottom: 6px; }
.key-line { font-size: 11px; color: #2A2420; line-height: 1.6; }
.key-line + .key-line { border-top: 1px solid var(--rule); margin-top: 5px; padding-top: 5px; }
.drops { display: flex; flex-direction: column; gap: 7px; }
.drop-card { background: var(--surface); border: 1px solid var(--rule); border-radius: 4px; overflow: hidden; page-break-inside: avoid; }
.drop-main { display: flex; align-items: center; padding: 9px 14px; }
.drop-chip {
  font-size: 8px; font-weight: 700; letter-spacing: 0.10em; text-transform: uppercase;
  padding: 3px 8px; border-radius: 3px; white-space: nowrap; flex-shrink: 0;
  min-width: 72px; text-align: center;
}
.chip-filler { background: #FFF2EE; color: #C2401A; }
.chip-rep    { background: #FFF7ED; color: #92400E; }
.chip-fs     { background: #EEF2FF; color: #3730A3; }
.chip-nm     { background: #F0FDF4; color: #166534; }
.drop-ts {
  font-family: "Courier New", Courier, monospace;
  font-size: 10px; color: var(--dim); padding: 0 12px;
  white-space: nowrap; font-variant-numeric: tabular-nums; min-width: 100px; flex-shrink: 0;
  border-left: 1px solid var(--rule); border-right: 1px solid var(--rule); margin: 0 4px;
}
.drop-word { font-size: 11.5px; color: #1A1614; font-style: italic; font-weight: 500; flex: 1; padding-left: 10px; }
.drop-sub {
  padding: 6px 14px; background: #F7F6F4; border-top: 1px solid var(--rule);
  font-size: 10px; color: var(--dim); display: flex; justify-content: space-between; align-items: center;
}
.drop-sub-cta { font-size: 9.5px; color: var(--accent); font-weight: 500; white-space: nowrap; padding-left: 8px; }
.guide { background: var(--surface); border: 1px solid var(--rule); border-radius: 4px; padding: 14px 18px; margin-top: 6px; page-break-inside: avoid; }
.guide-step { display: flex; gap: 11px; align-items: baseline; font-size: 11px; color: #2A2420; line-height: 1.5; }
.guide-step + .guide-step { margin-top: 7px; padding-top: 7px; border-top: 1px solid var(--rule); }
.step-n { font-size: 9.5px; font-weight: 700; color: var(--accent); min-width: 14px; text-align: right; flex-shrink: 0; }
.footer {
  margin-top: 34px; padding-top: 14px; border-top: 1px solid var(--rule);
  display: flex; justify-content: space-between; align-items: center;
}
.footer-l { font-size: 9px; color: var(--dim); }
.footer-r { font-size: 9px; color: var(--rule); }
"""


def _ts(secs: float) -> str:
    secs = max(0.0, float(secs))
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _ts_p(secs: float) -> str:
    """Precise timestamp (M:SS.ss) for CapCut sub-frame positioning."""
    secs = max(0.0, float(secs))
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = secs % 60
    return f"{h}:{m:02d}:{s:05.2f}" if h else f"{m}:{s:05.2f}"


def _drop_info(reason: str) -> tuple[str, str]:
    for prefix, (label, cls) in _REASON_MAP.items():
        if reason.startswith(prefix):
            return label, cls
    return "Détection", "chip-nm"


def _word_text(drop, source_words: list[dict]) -> str:
    """Resolve the spoken text of a drop for display."""
    reason = drop.reason
    # Text embedded in reason: "filler_word:Euh"
    if ":" in reason and not reason.startswith("llm"):
        candidate = reason.split(":", 1)[1].strip()
        if candidate:
            return f"« {candidate} »"

    # Match via target_intervals
    if hasattr(drop, "target_intervals") and drop.target_intervals:
        texts: list[str] = []
        seen: set[str] = set()
        for ti_start, _ti_end in drop.target_intervals:
            for w in source_words:
                if abs(float(w.get("start", 0)) - ti_start) < 0.120:
                    txt = str(w.get("text", "")).strip()
                    if txt and txt not in seen:
                        texts.append(txt)
                        seen.add(txt)
                    break
        if texts:
            return f"« {' '.join(texts)} »"

    # Overlap scan fallback
    texts2 = [
        str(w.get("text", "")).strip()
        for w in source_words
        if float(w.get("start", 0)) >= drop.start - 0.05
        and float(w.get("end", 0)) <= drop.end + 0.10
        and str(w.get("text", "")).strip()
    ]
    return f"« {' '.join(texts2[:4])} »" if texts2 else "?"


def _chromium_bin() -> str:
    candidates = [
        os.environ.get("CHROMIUM_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    raise FileNotFoundError(
        "Chromium not found for PDF generation. "
        "Install chromium or set CHROMIUM_PATH."
    )


def _html_to_pdf(html: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as fh:
        fh.write(html)
        tmp_html = Path(fh.name)
    try:
        subprocess.run(
            [
                _chromium_bin(),
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--no-first-run",
                "--disable-extensions",
                f"--print-to-pdf={output_path.resolve()}",
                tmp_html.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
    finally:
        tmp_html.unlink(missing_ok=True)


def _render_html(
    job_id: str,
    source_filename: str,
    plan_segments: list[dict],
    key_lines: list[str],
    detected_drops: list,
    stutter_near_misses: list[dict],
    source_words: list[dict],
    src_duration: float,
) -> str:
    now = datetime.now()
    date_str = f"{now.day} {_FR_MONTHS[now.month - 1]} {now.year}"
    dur_str = _ts(src_duration)
    n_drops = len(detected_drops)
    n_nm = len(stutter_near_misses)
    fname = escape(source_filename)
    jid_short = escape(job_id[:16])

    # Beat rows — consolidate before display in two passes:
    # 1. Merge consecutive same-beat segments into one block.
    # 2. Absorb short blocks (< 3 s) into the preceding segment so the
    #    timeline stays continuous with no fragment rows.  A 2-second
    #    "RÉALISATION" between two longer beats is not a standalone narrative
    #    phase — extending the preceding block's end is more accurate than
    #    showing a semi-visible row the user can't act on.
    _merged_segs: list[dict] = []
    for seg in plan_segments:
        _beat = str(seg.get("beat", "story")).lower()
        if _merged_segs and _merged_segs[-1]["beat"] == _beat:
            _merged_segs[-1]["end"] = float(seg.get("end", _merged_segs[-1]["end"]))
        else:
            _merged_segs.append({
                "beat": _beat,
                "start": float(seg.get("start", 0)),
                "end": float(seg.get("end", 0)),
                "summary": str(seg.get("summary", "")).strip(),
            })

    _MIN_BEAT_DUR = 3.0
    _consolidated: list[dict] = []
    for seg in _merged_segs:
        if _consolidated and (seg["end"] - seg["start"]) < _MIN_BEAT_DUR:
            _consolidated[-1]["end"] = seg["end"]  # absorb into preceding
        else:
            _consolidated.append(seg)

    beat_parts: list[str] = []
    for seg in _consolidated:
        beat = seg["beat"]
        label = _BEAT_LABELS.get(beat, beat.title())
        bc, pc = _BEAT_CSS.get(beat, ("b-story", "p-story"))
        t_s = seg["start"]
        t_e = seg["end"]
        raw = seg["summary"]
        excerpt = escape((raw[:72] + "…") if len(raw) > 75 else raw)
        beat_parts.append(
            f'<div class="beat-row {bc}">'
            f'<span class="beat-pill {pc}">{escape(label)}</span>'
            f'<span class="beat-ts">{_ts(t_s)} &rarr; {_ts(t_e)}</span>'
            f'<span class="beat-excerpt">{excerpt}</span>'
            f'</div>'
        )
    beats_html = (
        "\n".join(beat_parts)
        if beat_parts
        else '<p class="sec-sub">Aucune structure identifiée pour cette vidéo.</p>'
    )

    # Key lines block
    key_html = ""
    if key_lines:
        lines_html = "\n".join(
            f'<div class="key-line">{escape(kl)}</div>'
            for kl in key_lines[:6]
        )
        key_html = (
            '<div class="key-block">'
            '<div class="key-label">Extraits clés identifiés</div>'
            f'{lines_html}'
            '</div>'
        )

    # Drop cards
    drop_parts: list[str] = []
    for drop in sorted(detected_drops, key=lambda d: d.start):
        # Filter zero-duration artifacts (start ≈ end, e.g. Whisper tokenisation
        # splitting "c'est" into a bare "c" token at the same timestamp).
        if drop.end - drop.start < 0.05:
            continue
        label, cls = _drop_info(drop.reason)
        word_raw = _word_text(drop, source_words)
        # Filter single-character detections (same Whisper artefact family).
        if len(word_raw.strip("« »").strip()) <= 1:
            continue
        word = escape(word_raw)
        ts_range = f"{_ts(drop.start)} &rarr; {_ts(drop.end)}"
        ts_precise = f"{_ts_p(drop.start)} &rarr; {_ts_p(drop.end)}"
        drop_parts.append(
            f'<div class="drop-card">'
            f'<div class="drop-main">'
            f'<span class="drop-chip {cls}">{escape(label)}</span>'
            f'<span class="drop-ts">{ts_range}</span>'
            f'<span class="drop-word">{word}</span>'
            f'</div>'
            f'<div class="drop-sub">'
            f'<span>Repéré à ce point. Timestamp source : {ts_precise}</span>'
            f'<span class="drop-sub-cta">Repère CapCut →</span>'
            f'</div>'
            f'</div>'
        )
    drops_html = (
        "\n".join(drop_parts)
        if drop_parts
        else '<p class="sec-sub">Aucune détection pour cette vidéo.</p>'
    )

    # Near-miss section (optional)
    nm_section = ""
    if stutter_near_misses:
        nm_parts: list[str] = []
        for nm in stutter_near_misses:
            word = escape(str(nm.get("word", "?")))
            t1 = float(nm.get("t1", 0))
            t2 = float(nm.get("t2", 0))
            gap = float(nm.get("gap", 0))
            bridge = nm.get("bridge", [])
            bridge_mid = f" {escape(' '.join(bridge[:3]))} " if bridge else " "
            nm_parts.append(
                f'<div class="drop-card">'
                f'<div class="drop-main">'
                f'<span class="drop-chip chip-nm">Proche</span>'
                f'<span class="drop-ts">{_ts(t1)} / {_ts(t2)}</span>'
                f'<span class="drop-word">'
                f'« {word}{bridge_mid}{word} »'
                f' (écart {gap:.2f}s)</span>'
                f'</div>'
                f'<div class="drop-sub">'
                f'<span>Conservé. À écouter dans la source :'
                f' {_ts_p(t1)} et {_ts_p(t2)}</span>'
                f'<span class="drop-sub-cta">Optionnel →</span>'
                f'</div>'
                f'</div>'
            )
        nm_section = (
            '<div class="sec">'
            '<span class="sec-label">Répétitions Proches</span>'
            '<div class="sec-rule"></div>'
            '</div>'
            '<p class="sec-sub">Paires de mots identiques séparées par moins'
            ' de 1 seconde, conservées par le système.'
            ' À écouter si vous souhaitez affiner davantage.</p>'
            f'<div class="drops">{"".join(nm_parts)}</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Rapport éditorial leanon</title>
<style>{_CSS}</style>
</head>
<body>

<div class="hd">
  <div class="hd-top">
    <div class="brand">{_BRAND_ICON}<span class="brand-name">leanon</span></div>
    <span class="doc-badge">Rapport Éditorial</span>
  </div>
  <h1 class="hd-title">Analyse <em>Éditoriale</em></h1>
  <div class="hd-meta">
    <strong>{fname}</strong>
    &nbsp;·&nbsp; {date_str}
    &nbsp;·&nbsp; Durée source : <strong>{dur_str}</strong>
  </div>
  <div class="hd-stats">
    <div class="stat">
      <span class="stat-n">{len(plan_segments)}</span>
      <span class="stat-l">Segments narratifs</span>
    </div>
    <div class="stat-div"></div>
    <div class="stat">
      <span class="stat-n">{n_drops}</span>
      <span class="stat-l">Points détectés</span>
    </div>
    <div class="stat-div"></div>
    <div class="stat">
      <span class="stat-n">{n_nm}</span>
      <span class="stat-l">Répétitions proches</span>
    </div>
  </div>
</div>

<div class="body">

  <div class="sec">
    <span class="sec-label">Structure Éditoriale Identifiée</span>
    <div class="sec-rule"></div>
  </div>
  <p class="sec-sub">Voici comment la vidéo a été analysée et structurée
  pour maximiser la rétention. Chaque segment correspond à un rôle narratif précis.</p>
  <div class="beats">{beats_html}</div>
  {key_html}

  <div class="sec">
    <span class="sec-label">Repères dans la Vidéo Source</span>
    <div class="sec-rule"></div>
  </div>
  <p class="sec-sub">Pour aller plus loin : chaque décision prise par le système,
  avec le timestamp exact dans la vidéo source originale.</p>
  <div class="drops">{drops_html}</div>

  {nm_section}

  <div class="sec">
    <span class="sec-label">Utiliser ces Repères dans CapCut</span>
    <div class="sec-rule"></div>
  </div>
  <div class="guide">
    <div class="guide-step">
      <span class="step-n">1</span>
      <span>Importer la <strong>vidéo source originale</strong>
      (pas la vidéo rendue) dans CapCut.</span>
    </div>
    <div class="guide-step">
      <span class="step-n">2</span>
      <span>Sur la timeline, placer la tête de lecture au timestamp indiqué.
      Zoomer pour une précision à la fraction de seconde.</span>
    </div>
    <div class="guide-step">
      <span class="step-n">3</span>
      <span>Utiliser <strong>Diviser</strong> avant et après le passage,
      puis <strong>Supprimer</strong>.</span>
    </div>
  </div>

  <div class="footer">
    <span class="footer-l">leanon.ai · Job {jid_short} · {date_str}</span>
    <span class="footer-r">1</span>
  </div>

</div>
</body>
</html>"""


def build_edit_report(
    job_id: str,
    source_filename: str,
    plan_segments: list[dict],
    key_lines: list[str],
    detected_drops: list,
    stutter_near_misses: list[dict],
    source_words: list[dict],
    src_duration: float,
    output_path: Path,
) -> Path:
    """Generate the editorial PDF report and write it to *output_path*.

    Renders an HTML template via Chrome headless (chromium --headless=new).
    Requires chromium in PATH or CHROMIUM_PATH env var (already present in
    the Railway container for HyperFrames rendering).

    Returns the written *output_path*.
    """
    html = _render_html(
        job_id=job_id,
        source_filename=source_filename,
        plan_segments=plan_segments,
        key_lines=key_lines,
        detected_drops=detected_drops,
        stutter_near_misses=stutter_near_misses,
        source_words=source_words,
        src_duration=src_duration,
    )
    _html_to_pdf(html, output_path)
    return output_path
