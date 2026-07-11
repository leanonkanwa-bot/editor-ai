"""
generative_primitive — compositional scene renderer.

Scenes are assembled from three independent layers:
  frame   → outer wrapper  (phone | card | none | none-fullbleed)
  visual  → main element   (icon | number_counter | chart_trace | chat_bubbles | quote_mark)
  text_slot → text element (kicker | label | quote_text | none)

Combined via layout (stacked | side_by_side | centered_overlay)
and animated via entry (pop | trace | fade | slide_up | sequential).

All visual parameters (colors, fonts, radii, shadows) come from the style
pack injected by the caller. The LLM only picks axis values from whitelisted
vocabularies — never colors, SVG, or free text.
"""
from __future__ import annotations

from app.engine.broll_registry import BRollType, register


# ── Helpers ───────────────────────────────────────────────────────────────────

def _e(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _ej(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


# ── SVG icon set (closed vocabulary, accent-color rendered) ───────────────────
# "ACCENT" is replaced at render time with the active pack accent color.

_SVG_ICONS: dict[str, str] = {
    "spark": (
        '<path d="M13 2 L4 13 L10.5 13 L8.5 22 L20 11 L13.5 11 Z"'
        ' fill="ACCENT" stroke="none"/>'
    ),
    "stop": (
        '<path d="M8.93 3 L15.07 3 L21 8.93 L21 15.07 L15.07 21 L8.93 21'
        ' L3 15.07 L3 8.93 Z" fill="none" stroke="ACCENT" stroke-width="2"'
        ' stroke-linejoin="round"/>'
        '<line x1="12" y1="8" x2="12" y2="13.5" stroke="ACCENT"'
        ' stroke-width="2" stroke-linecap="round"/>'
        '<circle cx="12" cy="16.5" r="1" fill="ACCENT"/>'
    ),
    "growth": (
        '<polyline points="3,17 8,10 13,14 21,6" fill="none" stroke="ACCENT"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        '<polyline points="16.5,6 21,6 21,10.5" fill="none" stroke="ACCENT"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    "alert": (
        '<path d="M12 3 L21.5 20 L2.5 20 Z" fill="none" stroke="ACCENT"'
        ' stroke-width="2" stroke-linejoin="round"/>'
        '<line x1="12" y1="9" x2="12" y2="14" stroke="ACCENT"'
        ' stroke-width="2" stroke-linecap="round"/>'
        '<circle cx="12" cy="17.5" r="1" fill="ACCENT"/>'
    ),
    "heart": (
        '<path d="M12 21 C12 21 3 14.5 3 8.5 C3 5.5 5.5 3 8.5 3'
        ' C10.2 3 11.5 4.1 12 5.2 C12.5 4.1 13.8 3 15.5 3'
        ' C18.5 3 21 5.5 21 8.5 C21 14.5 12 21 12 21 Z"'
        ' fill="ACCENT" stroke="none"/>'
    ),
    "star": (
        '<polygon points="12,2 15.09,8.26 22,9.27 17,14.14 18.18,21.02'
        ' 12,17.77 5.82,21.02 7,14.14 2,9.27 8.91,8.26"'
        ' fill="ACCENT" stroke="none"/>'
    ),
    "check": (
        '<polyline points="4,12 9,17 20,6" fill="none" stroke="ACCENT"'
        ' stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
}

ALLOWED_ICONS: frozenset[str] = frozenset(_SVG_ICONS)


# ── Frame layer ───────────────────────────────────────────────────────────────

def _frame_html(
    frame: str, bg: str, radius: str, shadow: str, shadow_i: str, accent: str, cid: str
) -> tuple[str, str, str]:
    """Returns (open_html, close_html, css)."""
    shadow_v = f"{shadow}, {shadow_i}" if shadow_i else shadow

    if frame == "phone":
        css = f"""\
.card[data-card-id="{cid}"] .prim-frame {{
  width:220px; height:370px; border-radius:36px;
  border:2px solid rgba(255,255,255,0.12);
  background:#111; display:flex; flex-direction:column;
  padding:40px 10px 16px; position:relative; overflow:hidden;
  box-shadow:0 12px 40px rgba(0,0,0,0.75),inset 0 1px 0 rgba(255,255,255,0.08);
  gap:8px;
}}
.card[data-card-id="{cid}"] .prim-frame::before {{
  content:""; position:absolute; top:12px; left:50%;
  transform:translateX(-50%);
  width:56px; height:6px; background:rgba(255,255,255,0.12);
  border-radius:3px;
}}"""
        return (
            f'<div class="prim-frame" id="{cid}-frame">',
            '</div>',
            css,
        )

    if frame == "none-fullbleed":
        css = f"""\
.card[data-card-id="{cid}"] .prim-frame {{
  width:100%; height:100%; display:flex; align-items:center;
  justify-content:center; position:relative;
}}"""
        return (
            f'<div class="prim-frame" id="{cid}-frame">',
            '</div>',
            css,
        )

    if frame == "none":
        css = f"""\
.card[data-card-id="{cid}"] .prim-frame {{
  display:flex; align-items:center; justify-content:center;
}}"""
        return (
            f'<div class="prim-frame" id="{cid}-frame">',
            '</div>',
            css,
        )

    # Default: card
    css = f"""\
.card[data-card-id="{cid}"] .prim-frame {{
  background:{bg}; border-radius:{radius}; padding:32px 44px;
  display:flex; flex-direction:column; align-items:center; gap:14px;
  box-shadow:{shadow_v}; position:relative; overflow:hidden;
}}"""
    return (
        f'<div class="prim-frame" id="{cid}-frame">',
        '</div>',
        css,
    )


# ── Visual layer ──────────────────────────────────────────────────────────────

def _visual_html(params: dict, pack: dict, cid: str) -> tuple[str, str]:
    """Returns (html, css) for the visual element."""
    visual  = params.get("visual", "icon")
    accent  = pack.get("accent", "#4cc9f0")
    bg      = pack.get("bg",     "#1a1a1a")
    text_c  = pack.get("text",   "#f1f1f1")
    font    = pack.get("font",   '"Inter", sans-serif')
    fw      = pack.get("font_weight", "800")

    if visual == "icon":
        key      = params.get("icon_name", "spark")
        raw      = _SVG_ICONS.get(key, _SVG_ICONS["spark"])
        svg_inner = raw.replace("ACCENT", accent)
        html = (
            f'<div class="prim-visual prim-icon" id="{cid}-prim-visual">'
            f'<svg viewBox="0 0 24 24" width="72" height="72"'
            f' xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
            f'{svg_inner}'
            f'</svg></div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-icon {{
  opacity:0; display:flex; align-items:center; justify-content:center;
}}"""
        return html, css

    if visual == "number_counter":
        val  = _e(str(params.get("content_value", "")))
        html = (
            f'<div class="prim-visual prim-number" id="{cid}-prim-visual">'
            f'{val}</div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-number {{
  font-family:{font}; font-size:88px; font-weight:{fw}; color:{text_c};
  font-variant-numeric:tabular-nums; line-height:1; opacity:0;
}}"""
        return html, css

    if visual == "chart_trace":
        # Rising path — SVG stroke-dashoffset animated by GSAP.
        # Path length estimated at ~350px; dasharray set to 360 for safety.
        path_d = "M0,70 L40,55 L80,40 L120,25 L160,12 L200,5"
        html = (
            f'<div class="prim-visual prim-chart-wrap" id="{cid}-prim-visual">'
            f'<svg viewBox="0 0 200 80" width="200" height="80"'
            f' xmlns="http://www.w3.org/2000/svg" overflow="visible">'
            f'<path d="{path_d}" fill="none" stroke="{accent}"'
            f' stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"'
            f' stroke-dasharray="360" stroke-dashoffset="360"'
            f' id="{cid}-chart-path"/>'
            f'<circle cx="200" cy="5" r="5" fill="{accent}" opacity="0"'
            f' id="{cid}-chart-dot"/>'
            f'</svg></div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-chart-wrap {{
  opacity:0; display:flex; align-items:center; justify-content:center;
}}"""
        return html, css

    if visual == "chat_bubbles":
        n       = int(params.get("n_bubbles", 2))
        label   = _e(params.get("label",  ""))
        kicker  = _e(params.get("kicker", ""))
        # Text assignment: kicker → left bubble, label → right bubble, "…" → 3rd
        texts = {
            1: [label or kicker or "…"],
            2: [kicker or "…", label or "…"],
            3: [kicker or "…", label or "…", "…"],
        }.get(n, [kicker or "…", label or "…"])
        sides = ["left", "right", "left"]

        bubbles = "".join(
            f'<div class="prim-bubble bubble-{sides[i]}" id="{cid}-bubble-{i}">'
            f'{texts[i]}</div>'
            for i in range(len(texts))
        )
        html = f'<div class="prim-visual prim-bubbles" id="{cid}-prim-visual">{bubbles}</div>'
        css = f"""\
.card[data-card-id="{cid}"] .prim-bubbles {{
  display:flex; flex-direction:column; gap:8px; width:100%; padding:0 4px;
}}
.card[data-card-id="{cid}"] .prim-bubble {{
  font-family:{font}; font-size:14px; font-weight:600;
  padding:10px 14px; border-radius:18px; max-width:82%;
  opacity:0; line-height:1.35; word-break:break-word;
}}
.card[data-card-id="{cid}"] .bubble-left {{
  align-self:flex-start; background:rgba(255,255,255,0.12);
  color:{text_c}; border-bottom-left-radius:4px;
}}
.card[data-card-id="{cid}"] .bubble-right {{
  align-self:flex-end; background:{accent};
  color:{bg}; border-bottom-right-radius:4px;
}}"""
        return html, css

    if visual == "progress_bar":
        raw = str(params.get("content_value", ""))
        pct_str = raw.replace("%", "").strip()
        try:
            pct = max(0, min(100, int(float(pct_str))))
        except ValueError:
            pct = 50
        label_v = _e(params.get("label", f"{pct}%"))
        html = (
            f'<div class="prim-visual prim-progress" id="{cid}-prim-visual">'
            f'<div class="pb-header">'
            f'<span class="pb-label">{label_v}</span>'
            f'<span class="pb-pct">{pct}%</span>'
            f'</div>'
            f'<div class="pb-track"><div class="pb-fill" id="{cid}-pb-fill"'
            f' data-target="{pct}"></div></div>'
            f'</div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-progress {{
  opacity:0; display:flex; flex-direction:column; gap:10px; width:100%;
}}
.card[data-card-id="{cid}"] .pb-header {{
  display:flex; justify-content:space-between; align-items:baseline;
}}
.card[data-card-id="{cid}"] .pb-label {{
  font-family:{font}; font-size:15px; font-weight:700; color:{text_c};
}}
.card[data-card-id="{cid}"] .pb-pct {{
  font-family:{font}; font-size:22px; font-weight:{fw}; color:{accent};
  font-variant-numeric:tabular-nums;
}}
.card[data-card-id="{cid}"] .pb-track {{
  width:100%; height:8px; background:rgba(255,255,255,0.12);
  border-radius:4px; overflow:hidden;
}}
.card[data-card-id="{cid}"] .pb-fill {{
  height:100%; width:0%; background:{accent}; border-radius:4px;
}}"""
        return html, css

    if visual == "checklist":
        items_raw = params.get("label", "")
        lines_raw = [l.strip() for l in items_raw.split("|") if l.strip()] if "|" in items_raw else [items_raw]
        kicker_v  = params.get("kicker", "")
        all_items = ([kicker_v] if kicker_v else []) + lines_raw
        if len(all_items) < 2:
            all_items = ["Étape précédente", "En cours"] + all_items
        all_items = all_items[:4]
        tick_svg = (
            f'<svg viewBox="0 0 16 16" width="14" height="14"'
            f' xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
            f'<circle cx="8" cy="8" r="7" fill="{accent}" opacity="0.18"/>'
            f'<polyline points="4,8 7,11 12,5" fill="none" stroke="{accent}"'
            f' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            f'</svg>'
        )
        items_html = "".join(
            f'<div class="cl-item" id="{cid}-cl-{i}">'
            f'{tick_svg}'
            f'<span>{_e(t)}</span>'
            f'</div>'
            for i, t in enumerate(all_items)
        )
        html = f'<div class="prim-visual prim-checklist" id="{cid}-prim-visual">{items_html}</div>'
        css = f"""\
.card[data-card-id="{cid}"] .prim-checklist {{
  display:flex; flex-direction:column; gap:9px; width:100%;
}}
.card[data-card-id="{cid}"] .cl-item {{
  display:flex; align-items:center; gap:9px; opacity:0;
  font-family:{font}; font-size:14px; font-weight:600; color:{text_c};
}}
.card[data-card-id="{cid}"] .cl-item:last-child {{
  color:{accent};
}}"""
        return html, css

    if visual == "countdown_ring":
        dur_label = _e(params.get("content_value", params.get("label", "??")))
        r   = 44
        circ = round(2 * 3.14159 * r, 2)
        html = (
            f'<div class="prim-visual prim-ring" id="{cid}-prim-visual">'
            f'<svg viewBox="0 0 100 100" width="110" height="110"'
            f' xmlns="http://www.w3.org/2000/svg">'
            f'<circle cx="50" cy="50" r="{r}" fill="none"'
            f' stroke="rgba(255,255,255,0.10)" stroke-width="7"/>'
            f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="{accent}"'
            f' stroke-width="7" stroke-linecap="round"'
            f' stroke-dasharray="{circ}" stroke-dashoffset="{circ}"'
            f' id="{cid}-ring-arc" transform="rotate(-90 50 50)"/>'
            f'<text x="50" y="56" text-anchor="middle"'
            f' font-family="{font}" font-weight="{fw}" font-size="14"'
            f' fill="{text_c}">{dur_label}</text>'
            f'</svg></div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-ring {{
  opacity:0; display:flex; align-items:center; justify-content:center;
}}"""
        return html, css

    if visual == "versus_meter":
        left_label  = _e(params.get("kicker", "A"))
        right_label = _e(params.get("label",  "B"))
        html = (
            f'<div class="prim-visual prim-versus" id="{cid}-prim-visual">'
            f'<div class="vs-row">'
            f'<span class="vs-name vs-left">{left_label}</span>'
            f'<span class="vs-name vs-right">{right_label}</span>'
            f'</div>'
            f'<div class="vs-bars">'
            f'<div class="vs-bar vs-bar-left" id="{cid}-vs-left"></div>'
            f'<div class="vs-bar vs-bar-right" id="{cid}-vs-right"></div>'
            f'</div>'
            f'</div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-versus {{
  opacity:0; display:flex; flex-direction:column; gap:10px; width:100%;
}}
.card[data-card-id="{cid}"] .vs-row {{
  display:flex; justify-content:space-between;
  font-family:{font}; font-size:13px; font-weight:700;
}}
.card[data-card-id="{cid}"] .vs-left {{ color:{accent}; }}
.card[data-card-id="{cid}"] .vs-right {{ color:{text_c}; opacity:0.55; }}
.card[data-card-id="{cid}"] .vs-bars {{
  display:flex; gap:4px; height:10px;
}}
.card[data-card-id="{cid}"] .vs-bar {{
  height:100%; border-radius:5px; width:0%;
}}
.card[data-card-id="{cid}"] .vs-bar-left {{
  background:{accent}; flex:0 0 0%;
}}
.card[data-card-id="{cid}"] .vs-bar-right {{
  background:rgba(255,255,255,0.20); flex:0 0 0%;
}}"""
        return html, css

    if visual == "stat_grid":
        val_raw  = _e(params.get("content_value", params.get("label", "×")))
        sub_text = _e(params.get("kicker", ""))
        html = (
            f'<div class="prim-visual prim-stat-grid" id="{cid}-prim-visual">'
            f'<div class="sg-big" id="{cid}-sg-val">{val_raw}</div>'
            f'<div class="sg-sub">{sub_text}</div>'
            f'</div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-stat-grid {{
  opacity:0; display:flex; flex-direction:column; align-items:center; gap:4px;
}}
.card[data-card-id="{cid}"] .sg-big {{
  font-family:{font}; font-size:72px; font-weight:{fw}; color:{accent};
  font-variant-numeric:tabular-nums; line-height:1;
}}
.card[data-card-id="{cid}"] .sg-sub {{
  font-family:{font}; font-size:14px; font-weight:600;
  color:{text_c}; opacity:0.70; text-align:center;
}}"""
        return html, css

    if visual == "timeline_dots":
        label_v = _e(params.get("label", ""))
        n_steps = 4
        dots_html = ""
        for i in range(n_steps):
            is_last = i == n_steps - 1
            color = accent if is_last else "rgba(255,255,255,0.25)"
            size  = "14px" if is_last else "10px"
            dots_html += (
                f'<div class="td-dot" id="{cid}-td-{i}"'
                f' style="background:{color};width:{size};height:{size}"></div>'
            )
            if i < n_steps - 1:
                dots_html += f'<div class="td-line" id="{cid}-td-line-{i}"></div>'
        html = (
            f'<div class="prim-visual prim-timeline" id="{cid}-prim-visual">'
            f'<div class="td-row">{dots_html}</div>'
            f'<div class="td-label">{label_v}</div>'
            f'</div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-timeline {{
  opacity:0; display:flex; flex-direction:column; align-items:center; gap:10px;
  width:100%;
}}
.card[data-card-id="{cid}"] .td-row {{
  display:flex; align-items:center; gap:0; width:100%;
  justify-content:center;
}}
.card[data-card-id="{cid}"] .td-dot {{
  border-radius:50%; flex-shrink:0; opacity:0;
}}
.card[data-card-id="{cid}"] .td-line {{
  flex:1; height:2px; background:rgba(255,255,255,0.18);
  max-width:48px; opacity:0;
}}
.card[data-card-id="{cid}"] .td-label {{
  font-family:{font}; font-size:13px; font-weight:700; color:{accent};
  text-align:center; opacity:0;
}}"""
        return html, css

    if visual == "macos_notification":
        app_name = _e(params.get("kicker", "Notification"))
        message  = _e(params.get("label",  "Nouveau message"))
        html = (
            f'<div class="prim-visual prim-notif" id="{cid}-prim-visual">'
            f'<div class="notif-bar">'
            f'<div class="notif-dot"></div>'
            f'<span class="notif-app">{app_name}</span>'
            f'<span class="notif-time">maintenant</span>'
            f'</div>'
            f'<div class="notif-msg">{message}</div>'
            f'</div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-notif {{
  opacity:0; transform:translateY(-12px) scale(0.96);
  display:flex; flex-direction:column; gap:6px;
  background:rgba(30,30,35,0.88); border-radius:14px;
  padding:14px 18px; width:100%;
  backdrop-filter:blur(12px);
  border:1px solid rgba(255,255,255,0.10);
  box-shadow:0 8px 32px rgba(0,0,0,0.35);
}}
.card[data-card-id="{cid}"] .notif-bar {{
  display:flex; align-items:center; gap:6px;
}}
.card[data-card-id="{cid}"] .notif-dot {{
  width:10px; height:10px; border-radius:50%;
  background:{accent}; flex-shrink:0;
}}
.card[data-card-id="{cid}"] .notif-app {{
  font-family:{font}; font-size:12px; font-weight:700;
  color:rgba(255,255,255,0.55); letter-spacing:0.06em;
  text-transform:uppercase; flex:1;
}}
.card[data-card-id="{cid}"] .notif-time {{
  font-family:{font}; font-size:11px; color:rgba(255,255,255,0.35);
}}
.card[data-card-id="{cid}"] .notif-msg {{
  font-family:{font}; font-size:15px; font-weight:600; color:{text_c};
  line-height:1.35;
}}"""
        return html, css

    # quote_mark
    html = (
        f'<div class="prim-visual prim-quote-mark" id="{cid}-prim-visual">'
        f'<svg viewBox="0 0 48 36" width="64" height="48"'
        f' xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
        f'<path d="M0 36 L0 18 Q0 0 18 0 L18 8 Q8 8 8 18 L16 18 L16 36 Z"'
        f' fill="{accent}"/>'
        f'<path d="M26 36 L26 18 Q26 0 44 0 L44 8 Q34 8 34 18 L42 18 L42 36 Z"'
        f' fill="{accent}"/>'
        f'</svg></div>'
    )
    css = f"""\
.card[data-card-id="{cid}"] .prim-quote-mark {{
  opacity:0; display:flex; align-items:center; justify-content:center;
}}"""
    return html, css


# ── Text slot layer ───────────────────────────────────────────────────────────

def _text_slot_html(params: dict, pack: dict, cid: str) -> tuple[str, str]:
    """Returns (html, css) for the text element. Empty for chat_bubbles
    (bubbles carry their own text) or slot=none."""
    visual    = params.get("visual",    "icon")
    text_slot = params.get("text_slot", "label")

    # Chat bubbles carry text internally — suppress external text slot
    if visual == "chat_bubbles" or text_slot == "none":
        return "", ""

    accent  = pack.get("accent",         "#4cc9f0")
    text_c  = pack.get("text",           "#f1f1f1")
    text_s  = pack.get("text_secondary", "rgba(255,255,255,0.45)")
    font    = pack.get("font",           '"Inter", sans-serif')
    fw      = pack.get("font_weight",    "800")

    if text_slot == "kicker":
        val  = _e(params.get("kicker", ""))
        html = f'<div class="prim-text prim-kicker" id="{cid}-prim-text">{val}</div>'
        css  = f"""\
.card[data-card-id="{cid}"] .prim-kicker {{
  font-family:{font}; font-size:18px; font-weight:700; color:{accent};
  letter-spacing:0.12em; text-transform:uppercase; opacity:0;
}}"""
        return html, css

    if text_slot == "quote_text":
        val  = _e(params.get("label", ""))
        html = (
            f'<div class="prim-text prim-quote-text" id="{cid}-prim-text">'
            f'{val}</div>'
        )
        css  = f"""\
.card[data-card-id="{cid}"] .prim-quote-text {{
  font-family:{font}; font-size:22px; font-weight:600; color:{text_c};
  font-style:italic; text-align:center; line-height:1.45;
  max-width:280px; opacity:0;
}}"""
        return html, css

    # Default: label
    val  = _e(params.get("label", ""))
    html = f'<div class="prim-text prim-label" id="{cid}-prim-text">{val}</div>'
    css  = f"""\
.card[data-card-id="{cid}"] .prim-label {{
  font-family:{font}; font-size:22px; font-weight:600; color:{text_s};
  text-align:center; max-width:300px; opacity:0;
}}"""
    return html, css


# ── Layout assembly ───────────────────────────────────────────────────────────

def _assemble_layout(
    frame_open: str, frame_close: str,
    vis_html: str, text_html: str,
    layout: str, cid: str,
) -> tuple[str, str]:
    """Combines visual + text via layout, wraps with frame.
    Returns (full_html, layout_css).
    """
    if layout == "side_by_side":
        inner = (
            f'<div class="prim-layout" id="{cid}-layout">'
            f'{vis_html}{text_html}</div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-layout {{
  display:flex; flex-direction:row; align-items:center; gap:20px;
}}"""
    elif layout == "centered_overlay":
        inner = (
            f'<div class="prim-layout" id="{cid}-layout">'
            f'{vis_html}'
            f'<div class="prim-overlay-text">{text_html}</div>'
            f'</div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-layout {{
  position:relative; display:inline-flex;
  align-items:center; justify-content:center;
}}
.card[data-card-id="{cid}"] .prim-overlay-text {{
  position:absolute; display:flex; flex-direction:column;
  align-items:center; justify-content:center;
}}"""
    else:  # stacked (default)
        inner = (
            f'<div class="prim-layout" id="{cid}-layout">'
            f'{vis_html}{text_html}</div>'
        )
        css = f"""\
.card[data-card-id="{cid}"] .prim-layout {{
  display:flex; flex-direction:column; align-items:center; gap:14px;
}}"""

    full_html = f'{frame_open}{inner}{frame_close}'
    return full_html, css


# ── Top-level render ──────────────────────────────────────────────────────────

def _render_html(params: dict, pack: dict, card_id: str) -> str:
    p        = pack or {}
    bg       = p.get("bg",           "#1a1a1a")
    accent   = p.get("accent",       "#4cc9f0")
    radius   = p.get("radius",       "20px")
    shadow   = p.get("shadow",       "0 8px 32px rgba(0,0,0,0.4)")
    shadow_i = p.get("shadow_inset", "")
    frame    = params.get("frame",   "card")
    layout   = params.get("layout",  "stacked")
    cid      = card_id

    frame_open, frame_close, frame_css = _frame_html(
        frame, bg, radius, shadow, shadow_i, accent, cid
    )
    vis_html,  vis_css  = _visual_html(params, p, cid)
    text_html, text_css = _text_slot_html(params, p, cid)
    inner_html, layout_css = _assemble_layout(
        frame_open, frame_close, vis_html, text_html, layout, cid
    )

    return f"""\
<div class="card" data-card-id="{cid}">
<style>
.card[data-card-id="{cid}"] .root {{
  width:100%; height:100%; display:flex;
  align-items:center; justify-content:center;
}}
{frame_css}
{layout_css}
{vis_css}
{text_css}
</style>
<div class="root">
  {inner_html}
</div>
</div>"""


# ── GSAP ──────────────────────────────────────────────────────────────────────

def _render_gsap(
    params: dict, pack: dict, card_id: str, start: float, end: float
) -> list[str]:
    cid    = _ej(card_id)
    t_in   = round(start + 0.20, 4)
    visual = params.get("visual", "icon")
    entry  = params.get("entry",  "pop")
    label  = params.get("label",  "")

    lines: list[str] = []

    # ── Frame fade-in ────────────────────────────────────────────────────────
    lines.append(
        f"  tl.fromTo('#{cid}-frame',"
        f"{{opacity:0}},{{opacity:1,duration:0.25,ease:'power1.out'}},{t_in:.4f});"
    )

    t_vis = round(t_in + 0.10, 4)

    # ── Visual element animation ─────────────────────────────────────────────
    if visual == "chart_trace":
        # Always trace regardless of entry value
        lines += [
            f"  gsap.set('#{cid}-prim-visual',{{opacity:1}});",
            f"  tl.to('#{cid}-chart-path',{{strokeDashoffset:0,"
            f"duration:0.90,ease:'power2.out'}},{t_vis:.4f});",
            f"  tl.to('#{cid}-chart-dot',{{opacity:1,scale:1.4,"
            f"duration:0.20,ease:'back.out(2)'}},{round(t_vis+0.80, 4):.4f});",
        ]
        t_text = round(t_vis + 1.0, 4)

    elif visual == "chat_bubbles":
        n = int(params.get("n_bubbles", 2))
        lines.append(f"  gsap.set('#{cid}-prim-visual',{{opacity:1}});")
        if entry == "sequential":
            for i in range(n):
                t_b = round(t_vis + i * 0.40, 4)
                lines.append(
                    f"  tl.fromTo('#{cid}-bubble-{i}',"
                    f"{{opacity:0,y:14}},"
                    f"{{opacity:1,y:0,duration:0.28,ease:'power2.out'}},{t_b:.4f});"
                )
        else:
            lines.append(
                f"  tl.to('.card[data-card-id=\"{cid}\"] .prim-bubble',"
                f"{{opacity:1,stagger:0.18,duration:0.25,ease:'power1.out'}},{t_vis:.4f});"
            )
        t_text = round(t_vis + n * 0.40 + 0.10, 4)

    elif visual == "quote_mark":
        lines.append(
            f"  tl.fromTo('#{cid}-prim-visual',"
            f"{{opacity:0,scale:0.7}},"
            f"{{opacity:1,scale:1,duration:0.40,ease:'back.out(1.4)'}},{t_vis:.4f});"
        )
        t_text = round(t_vis + 0.35, 4)

    elif visual == "number_counter":
        lines.append(
            f"  tl.fromTo('#{cid}-prim-visual',"
            f"{{opacity:0,scale:0.7}},"
            f"{{opacity:1,scale:1,duration:0.35,ease:'back.out(1.7)'}},{t_vis:.4f});"
        )
        t_text = round(t_vis + 0.30, 4)

    elif visual == "progress_bar":
        try:
            pct = max(0, min(100, int(float(str(params.get("content_value", "50")).replace("%", "")))))
        except ValueError:
            pct = 50
        lines += [
            f"  tl.fromTo('#{cid}-prim-visual',"
            f"{{opacity:0}},{{opacity:1,duration:0.20,ease:'power1.out'}},{t_vis:.4f});",
            f"  tl.to('#{cid}-pb-fill',"
            f"{{width:'{pct}%',duration:0.80,ease:'power2.out'}},{round(t_vis+0.15, 4):.4f});",
        ]
        t_text = round(t_vis + 0.90, 4)

    elif visual == "checklist":
        n_items = len([k for k in params.keys() if k == "label"])
        all_items_raw = params.get("label", "")
        kicker_v = params.get("kicker", "")
        parts = ([kicker_v] if kicker_v else []) + \
                ([l.strip() for l in all_items_raw.split("|") if l.strip()] if "|" in all_items_raw else [all_items_raw])
        n_items = min(len(parts), 4)
        if n_items == 0:
            n_items = 2
        lines.append(f"  gsap.set('#{cid}-prim-visual',{{opacity:1}});")
        for i in range(n_items):
            t_b = round(t_vis + i * 0.22, 4)
            lines.append(
                f"  tl.fromTo('#{cid}-cl-{i}',"
                f"{{opacity:0,x:-10}},"
                f"{{opacity:1,x:0,duration:0.25,ease:'power2.out'}},{t_b:.4f});"
            )
        t_text = round(t_vis + n_items * 0.22 + 0.10, 4)

    elif visual == "countdown_ring":
        import math
        r    = 44
        circ = round(2 * math.pi * r, 2)
        lines += [
            f"  tl.fromTo('#{cid}-prim-visual',"
            f"{{opacity:0,scale:0.7}},"
            f"{{opacity:1,scale:1,duration:0.30,ease:'back.out(1.4)'}},{t_vis:.4f});",
            f"  tl.to('#{cid}-ring-arc',"
            f"{{strokeDashoffset:0,duration:1.0,ease:'power2.out'}},{round(t_vis+0.25, 4):.4f});",
        ]
        t_text = round(t_vis + 1.2, 4)

    elif visual == "versus_meter":
        lines += [
            f"  tl.fromTo('#{cid}-prim-visual',"
            f"{{opacity:0}},{{opacity:1,duration:0.20,ease:'power1.out'}},{t_vis:.4f});",
            f"  tl.to('#{cid}-vs-left',"
            f"{{'flex':'0 0 60%',duration:0.70,ease:'power2.out'}},{round(t_vis+0.15, 4):.4f});",
            f"  tl.to('#{cid}-vs-right',"
            f"{{'flex':'0 0 40%',duration:0.70,ease:'power2.out'}},{round(t_vis+0.20, 4):.4f});",
        ]
        t_text = round(t_vis + 0.90, 4)

    elif visual == "macos_notification":
        lines += [
            f"  tl.fromTo('#{cid}-prim-visual',"
            f"{{opacity:0,y:-16,scale:0.94}},"
            f"{{opacity:1,y:0,scale:1,duration:0.35,ease:'back.out(1.2)'}},{t_vis:.4f});",
        ]
        t_text = round(t_vis + 0.30, 4)

    elif visual == "stat_grid":
        lines += [
            f"  tl.fromTo('#{cid}-prim-visual',"
            f"{{opacity:0,scale:0.6}},"
            f"{{opacity:1,scale:1,duration:0.40,ease:'back.out(1.7)'}},{t_vis:.4f});",
        ]
        t_text = round(t_vis + 0.35, 4)

    elif visual == "timeline_dots":
        n_steps = 4
        lines.append(f"  gsap.set('#{cid}-prim-visual',{{opacity:1}});")
        for i in range(n_steps):
            t_d = round(t_vis + i * 0.18, 4)
            lines.append(
                f"  tl.fromTo('#{cid}-td-{i}',"
                f"{{opacity:0,scale:0.3}},"
                f"{{opacity:1,scale:1,duration:0.20,ease:'back.out(2)'}},{t_d:.4f});"
            )
            if i < n_steps - 1:
                t_l = round(t_vis + i * 0.18 + 0.10, 4)
                lines.append(
                    f"  tl.fromTo('#{cid}-td-line-{i}',"
                    f"{{opacity:0,scaleX:0}},"
                    f"{{opacity:1,scaleX:1,transformOrigin:'left center',"
                    f"duration:0.15,ease:'power1.out'}},{t_l:.4f});"
                )
        t_label_d = round(t_vis + (n_steps - 1) * 0.18 + 0.20, 4)
        lines.append(
            f"  tl.fromTo('.card[data-card-id=\"{cid}\"] .td-label',"
            f"{{opacity:0,y:5}},"
            f"{{opacity:1,y:0,duration:0.22,ease:'power1.out'}},{t_label_d:.4f});"
        )
        t_text = round(t_label_d + 0.25, 4)

    else:  # icon — dispatch on entry
        if entry == "pop":
            lines.append(
                f"  tl.fromTo('#{cid}-prim-visual',"
                f"{{opacity:0,scale:0.4}},"
                f"{{opacity:1,scale:1,duration:0.35,ease:'back.out(1.7)'}},{t_vis:.4f});"
            )
        elif entry == "trace":
            _acc = _ej(pack.get("accent", "#4cc9f0"))
            lines += [
                f"  tl.to('#{cid}-prim-visual',{{opacity:1,duration:0.10}},{t_vis:.4f});",
                f"  tl.fromTo('#{cid}-prim-visual',"
                f"{{'box-shadow':'0 0 0 0px rgba(76,201,240,0)'}},"
                f"{{'box-shadow':'0 0 0 3px {_acc}',"
                f"duration:0.50,ease:'power2.out'}},{t_vis:.4f});",
            ]
        elif entry == "slide_up":
            lines.append(
                f"  tl.fromTo('#{cid}-prim-visual',"
                f"{{opacity:0,y:20}},"
                f"{{opacity:1,y:0,duration:0.35,ease:'power2.out'}},{t_vis:.4f});"
            )
        else:  # fade
            lines.append(
                f"  tl.to('#{cid}-prim-visual',"
                f"{{opacity:1,duration:0.40,ease:'power1.out'}},{t_vis:.4f});"
            )
        t_text = round(t_vis + 0.30, 4)

    # ── Text slot animation ──────────────────────────────────────────────────
    if label and params.get("text_slot", "label") != "none" \
            and params.get("visual") != "chat_bubbles":
        lines.append(
            f"  tl.fromTo('#{cid}-prim-text',"
            f"{{opacity:0,y:6}},"
            f"{{opacity:1,y:0,duration:0.25,ease:'power1.out'}},{t_text:.4f});"
        )

    return lines


# ── Register ──────────────────────────────────────────────────────────────────

register(BRollType(
    name="generative_primitive",
    patterns=[],
    extractor=lambda m, w, i: ({}, 0.0),
    render_html=_render_html,
    render_gsap=_render_gsap,
    default_duration=5.0,
    preferred_zone="upper-right",
    min_confidence=0.65,
))
