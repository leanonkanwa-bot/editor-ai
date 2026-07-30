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
        "duration_range_s": (1.2, 1.8),
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
        "duration_range_s": (2.0, 2.5),
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
}
