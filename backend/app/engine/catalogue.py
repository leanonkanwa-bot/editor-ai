"""
B-roll primitive catalogue — lean_glass pack, Wave 11.

Fixed, parameterizable primitives. NOT free generation.
Each entry describes one visual unit: its family, zone, duration range,
and the contentHints fields it requires or accepts.

_family values:
  "card_overlay"  — standard card with card-panel; coexists with video.
  "full_cover"    — fullscreen card; card_overlay cards are dropped from
                    its time window by _apply_full_cover_exclusion().
"""

PRIMITIVES: dict[str, dict] = {
    "prim_stat_counter": {
        "_family": "card_overlay",
        "_pack": "lean_glass",
        "zone": "upper-right",
        "duration_range_s": (3.0, 6.0),  # DUR-EXT enforces 3.0s minimum — keep aligned
        "contentHints": {
            "number":  "REQUIRED — numeric string, e.g. '46.2' or '1000'",
            "title":   "REQUIRED — kicker label below the number",
            "prefix":  "optional — currency symbol, e.g. '$'",
            "suffix":  "optional — unit, e.g. '%', 'K', 'M'",
        },
        "description": (
            "Count-up number (0 → final value) with prefix/suffix, "
            "pulse at arrival, and a kicker label below."
        ),
    },
    "prim_numbered_rule": {
        "_family": "full_cover",
        "_pack": "lean_glass",
        "zone": "fullscreen",
        "duration_range_s": (1.5, 2.5),
        "cover_type": "blackout",
        "contentHints": {
            "number": "REQUIRED — '1'–'9' (single digit for max impact)",
            "title":  "REQUIRED — rule text below the number",
        },
        "description": (
            "Giant number scale-bounces in; rule text fades below. "
            "Black background, accent-color number. "
            "#backdrop-dim animated to solid black."
        ),
    },
    "prim_anecdote_frame": {
        "_family": "full_cover",
        "_pack": "lean_glass",
        "zone": "fullscreen",
        "duration_range_s": (3.0, 8.0),
        "cover_type": "overlay",
        "contentHints": {},
        "description": (
            "Vignette + film-grain overlay. Video remains visible underneath. "
            "No text. Soft fade in/out. "
            "No backdrop-dim (intentional — video shows through)."
        ),
    },
    "prim_split_compare": {
        "_family": "full_cover",
        "_pack": "lean_glass",
        "zone": "fullscreen",
        "duration_range_s": (3.0, 5.0),  # DUR-EXT enforces 3.0s minimum — keep aligned
        "cover_type": "blackout",
        "contentHints": {
            "left_label":  "REQUIRED — left panel label, e.g. 'AVANT'",
            "right_label": "REQUIRED — right panel label, e.g. 'APRÈS'",
        },
        "description": (
            "Two panels slide from opposite edges to a central divider. "
            "#backdrop-dim animated to solid black."
        ),
    },
    "prim_journey_map": {
        "_family": "full_cover",
        "_pack": "lean_glass",
        "zone": "fullscreen",
        "duration_range_s": (3.5, 6.0),
        "cover_type": "overlay",
        "contentHints": {
            "from_city":    "REQUIRED — departure city name, e.g. 'Paris'",
            "to_city":      "REQUIRED — arrival city name, e.g. 'Bangkok'",
            "from_country": "optional — departure country, e.g. 'France'",
            "to_country":   "optional — arrival country, e.g. 'Thaïlande'",
            "distance_km":  "optional — numeric distance string, e.g. '9560'",
        },
        "description": (
            "Flight-tracker card. Bezier arc animates from departure to arrival. "
            "Trail draws progressively with glow effect; plane chevron follows the arc "
            "with auto-rotation from bezier tangent. City labels and dots reveal on "
            "departure then arrival. Dark overlay (95% opacity) on video."
        ),
    },
    "prim_cinematic_reveal": {
        "_family": "full_cover",
        "_pack": "lean_glass",
        "zone": "fullscreen",
        "duration_range_s": (2.0, 3.5),
        "cover_type": "blackout",
        "contentHints": {
            "title":  "REQUIRED — main phrase to reveal (max 60 chars)",
            "kicker": "optional — small eyebrow label above the title (max 30 chars)",
            "detail": "optional — softer one-line amplifier below",
        },
        "description": (
            "Multi-layer cinematic depth reveal on a full-cover black canvas. "
            "Three layers enter at staggered offsets with distinct signature easings: "
            "Layer 0 — diamond ring background scales from 0.58 with sine.inOut (slow, atmospheric). "
            "Layer 1 — kicker flips from rotateX(-52°) via power3.out (weighted, decisive). "
            "Layer 2 — title approaches from scale(0.88)+rotateY(7°) via expo.out (fast, cinematic). "
            "Layer 3 — accent line scaleX grows left→right via power2.inOut. "
            "Layer 4 — detail fades with y rise via power2.out. "
            "No filter:blur anywhere — depth illusion is purely from 3D transforms + stagger."
        ),
    },
    "prim_shatter_truth": {
        "_family": "full_cover",
        "_pack": "lean_glass",
        "zone": "fullscreen",
        "duration_range_s": (2.0, 3.0),
        "cover_type": "blackout",
        "contentHints": {
            "myth_text":  "REQUIRED — false belief / myth / excuse to shatter (max 50 chars)",
            "truth_text": "REQUIRED — the truth that replaces it (max 60 chars)",
        },
        "description": (
            "Confrontation primitive: a myth text appears stable (0–0.6s), then "
            "micro-vibrates (0.6–0.75s, sine.inOut ±1px), shatters into 5 horizontal "
            "fragments that scatter radially (power4.out). A white flash punctuates "
            "the impact. The truth imposes itself via back.out(1.3). "
            "All depth via transforms + opacity, zero filter:blur. "
            "Narrative: confrontation / demolition of false belief. "
            "Independent budget: 1 per video — distinct from prim_cinematic_reveal "
            "and prim_ascension_reveal (those are climax-of-pride / result moments)."
        ),
    },
    "prim_split_stage": {
        "_family": "full_cover",
        "_pack": "lean_glass",
        "zone": "fullscreen",
        "duration_range_s": (3.5, 6.0),
        "cover_type": "none",
        "contentHints": {
            "side":   "REQUIRED — which side the speaker video moves to: 'left' or 'right'",
            "mode":   "REQUIRED — content type on the opposite side: 'steps' or 'diagram'",
            "kicker": "optional — small eyebrow label above the content (max 25 chars)",
            "steps":  "mode='steps' REQUIRED — list of 2-5 step strings, e.g. ['Prompt', 'HyperFrames', 'Vidéo']",
            "nodes":  "mode='diagram' REQUIRED — list of 2-4 dicts with keys 'icon' (emoji) and 'label' (str)",
        },
        "description": (
            "Layout-shift primitive: the speaker video shrinks to 50% and slides to one side "
            "(left or right) with subtle rotateY depth, while the opposite half reveals "
            "structured content — numbered steps (mode='steps') or an icon+label "
            "vertical diagram (mode='diagram'). "
            "Speaker returns to fullscreen at exit via one of 3 rotating variants "
            "(A: direct slide-back, B: fade-bridge, C: zoom-forward burst). "
            "No blackout — the speaker remains visible throughout; do not pair with backdrop-dim. "
            "Budget: 1-2 per video maximum. "
            "Ideal moments: explaining a multi-step process, a workflow, or a framework the viewer should remember. "
            "Alternate 'side' between occurrences for visual variety."
        ),
    },
    "prim_confession_frame": {
        "_family": "full_cover",
        "_pack": "lean_glass",
        "zone": "fullscreen",
        "duration_range_s": (2.5, 4.0),
        "cover_type": "none",
        "contentHints": {
            "confession_text": "REQUIRED — first-person past-tense confession, max 80 chars",
            "kicker": "optional — quiet eyebrow label (max 30 chars), e.g. 'MON POINT BAS', 'CE QUE J\\'AI CACHÉ'",
        },
        "description": (
            "Fragility primitive: a desaturation overlay (mix-blend-mode:saturation #808080, opacity 0→0.4, "
            "1.2s sine.inOut) drains colour from the pack background — no filter:blur. "
            "A radial vignette pulls the edges dark. The confession text rises discreetly from "
            "bottom-left (y:8→0 + opacity, power2.out, no overshoot). A thin 2px stadium-shape "
            "accent line draws left→right (scaleX 0→1, power1.inOut, 0.9s). "
            "Register: intimate, understated — emotional inverse of prim_cinematic_reveal. "
            "Independent budget: 1 per video. Can coexist with prim_cinematic_reveal."
        ),
    },
}
