# Style Extensions — "Momentum"

Notes on the "Momentum" editing style (`editing_style="momentum"`) and what is
and isn't active in the current render pipeline.

## What's implemented and live

- **`backend/app/agent/rules.py`** — `MOMENTUM_STYLE` prose block, registered in
  `system_prompt()` via `elif editing_style == "momentum": blocks.insert(0, MOMENTUM_STYLE)`.
  Drives the planner's pacing, hook structure, caption styling, b-roll, and
  zoom guidance for this style.
- **`backend/app/engine/render.py`** — `MOMENTUM_ZOOM` beat-mapped zoom table
  (max 150%, `MAX_ZOOM_MOMENTUM`), applied in the per-segment zoom dispatch
  alongside the existing Priestley branch.
- **`backend/app/engine/captions.py`** — `_build_momentum_ass()`: bold white
  ALL-CAPS 2-word kinetic groups (Anton), lime/chartreuse (#CCFF00) highlight on
  numbers/money/percentages, heavy black outline, scale-pop entry animation, and
  full-screen lime-on-black title cards for hook/stat/mantra moments. Wired into
  `build_ass()` via `if caption_style == "momentum": return _build_momentum_ass(...)`.
- **`editor_frontend/index.html` / `app.js`** — new "Momentum" style card in the
  style selector, with `caption_style` auto-synced to `"momentum"` when selected
  (same pattern as the existing Priestley card).

## What's scaffolded but not yet rendering

- **`backend/app/engine/hyperframes_engine.py`** — five new GSAP HTML templates
  added to `generate_composition_html()`: `portrait_callout` (B&W headshot +
  tilted red label banner), `step_diagram` (dark bg, glowing icon, "STEP 0X"),
  `scoreboard_stat` (big number / divider / small number), `big_number`
  (oversized comma-formatted figure with lime glow), and `social_handle`
  (rounded lower-third pill for handle/platform).

  **These templates do not currently affect rendered output.** The active
  `render()` pipeline disables both:
  - `remapped_hyperframes = []` ("FIX 1 — SALMON SCREEN" — color-flash MKV
    overlays render as a solid color screen and are disabled until the overlay
    pipeline is validated), and
  - `rendered_graphics: list[RenderedGraphic] = []` ("Motion graphics disabled
    — clean professional output. Only cuts + captions + zoom are applied.").

  To bring Momentum's motion graphics to life in actual exports, one of the
  following is required (out of scope for this change):
  1. Re-enable and validate the HyperFrames/Chromium overlay pipeline in
     `render.py`, or
  2. Add equivalent native types to the still-active FFmpeg overlay path
     (`_overlay_lower_third` / `_overlay_stat` / `_overlay_kinetic`, driven by
     `plan.motion_graphics`), porting the Momentum visual language (lime
     accents, red banners, "STEP 0X" framing) to FFmpeg `drawtext`/`drawbox`
     filters.

Until then, Momentum videos render with cuts + zoom (up to 150%, beat-mapped)
+ the new lime/white kinetic captions — the core pacing and caption identity of
the style — without the full motion-graphics layer.
