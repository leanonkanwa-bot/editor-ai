#!/usr/bin/env python3
"""
Generate trigger phrase bank for all 93 storyboard card types.

Reads live style definitions from storyboard.py, calls Claude in batches
of 8 styles, and builds backend/tools/trigger_bank.json with 15 FR + 15 EN
natural-language trigger phrases per style.

Usage:
    python backend/tools/generate_trigger_bank.py --sample   # 4 styles, preview only
    python backend/tools/generate_trigger_bank.py            # full 93-style generation
    python backend/tools/generate_trigger_bank.py --resume   # skip already-generated styles
"""

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

# ── API key ──────────────────────────────────────────────────────────────────
import os
_env_file = ROOT / "backend" / ".env"
if _env_file.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if _line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = _line.split("=", 1)[1].strip()
            break

from anthropic import Anthropic  # noqa: E402 (after env setup)

BANK_PATH  = Path(__file__).parent / "trigger_bank.json"
BATCH_SIZE = 8   # styles per API call; 8 × 30 phrases ≈ 7 000 output tokens, safe margin


# ── Extraction helpers ────────────────────────────────────────────────────────

def _extract_style_defs() -> str:
    src = (ROOT / "backend" / "app" / "engine" / "storyboard.py").read_text(encoding="utf-8")
    s = src.find("- CONTENT STYLE RULES (follow strictly, do not improvise):")
    e = src.find("- VERBATIM GROUNDING")
    if s == -1 or e == -1:
        raise RuntimeError("Cannot locate style-definition boundaries in storyboard.py")
    return src[s:e].strip()


def _extract_style_names() -> list[str]:
    src = (ROOT / "backend" / "app" / "engine" / "storyboard.py").read_text(encoding="utf-8")
    # The enum line looks like: "style": "stat"|"key_phrase"|...|"prim_journey_map",
    for line in src.splitlines():
        if '"style":' in line and "prim_journey_map" in line:
            # Extract all quoted tokens on this line
            names = re.findall(r'"([^"]+)"', line)
            # First token is "style" itself — drop it
            names = [n for n in names if n != "style"]
            if names:
                return names
    raise RuntimeError("Cannot find style enum in storyboard.py")


# ── Claude call ───────────────────────────────────────────────────────────────

def _generate_batch(client: Anthropic, style_names: list[str], style_defs: str) -> dict:
    """Return {style: {fr: [...15], en: [...15]}} for each style in the batch."""
    styles_list = "\n".join(f"- {s}" for s in style_names)

    system = [
        {
            "type": "text",
            "text": (
                "You are a bilingual content-type expert who knows exactly how "
                "content creators, entrepreneurs, coaches, and educators speak "
                "in talking-head videos.\n\n"
                "STYLE DEFINITIONS (authoritative — use these to calibrate your phrases):\n\n"
                f"{style_defs}"
            ),
            "cache_control": {"type": "ephemeral"},   # cache across batch calls
        }
    ]

    user = (
        f"For each of the following {len(style_names)} card types, generate exactly "
        "30 phrases split into THREE TIERS (10 per tier, mix of French and English — "
        "roughly 5 FR + 5 EN per tier, alternating):\n\n"
        "TIER 1 — CLEAN (10 phrases): Direct, textbook-obvious triggers. "
        "The signal is clear and explicit. "
        "Example for 'stat': 'Le taux d'abandon est à 72%.'\n\n"
        "TIER 2 — ORAL/NOISY (10 phrases): Real spoken-language messiness. "
        "MUST include hesitations (\"euh...\", \"um...\", \"attends\"), "
        "mid-sentence self-corrections (\"enfin non, je veux dire...\", \"or rather...\"), "
        "incomplete clauses, filler words (\"tu vois\", \"genre\", \"you know\", \"like\"), "
        "false starts, or repetitions. The trigger signal must still be present "
        "but buried in spoken noise. "
        "Example for 'stat': 'Attends euh... c'est 72%, non 71% je crois, "
        "enfin quelque chose comme ça — les trois quarts des gens abandonnent leur panier.'\n\n"
        "TIER 3 — INDIRECT/BURIED (10 phrases): The trigger signal is present "
        "but expressed obliquely, without the usual explicit markers. "
        "The phrase is surrounded by non-relevant context. "
        "A naive classifier would miss it. "
        "The signal is there if you read carefully. "
        "Example for 'stat': 'Quelqu'un me montrait une étude l'autre jour, "
        "je sais plus qui, mais grosso modo les trois quarts des visiteurs repartent "
        "sans avoir acheté — c'est hallucinant quand tu y penses.'\n\n"
        "Critical rules:\n"
        "- NEVER use the style name in the phrase itself\n"
        "- Each phrase is 1-4 spoken sentences\n"
        "- Tier 2 phrases MUST contain at least one hesitation, correction, or filler\n"
        "- Tier 3 phrases MUST NOT use the primary trigger keyword of that style "
        "(e.g. for 'stat': avoid 'selon une étude / 72% / taux')\n"
        "- Mix FR and EN across tiers (~15 FR total, ~15 EN total per style)\n\n"
        f"Card types:\n{styles_list}\n\n"
        "Return ONLY a valid JSON object, no markdown, no explanation:\n"
        '{"style_name": {"tier1": ["p1",...,"p10"], "tier2": ["p1",...,"p10"], '
        '"tier3": ["p1",...,"p10"]}, ...}'
    )

    resp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Dump first/last 300 chars to help diagnose
        print(f"  JSON parse error: {exc}", flush=True)
        print(f"  Response start: {repr(raw[:300])}", flush=True)
        print(f"  Response end:   {repr(raw[-300:])}", flush=True)
        raise


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    sample_only = "--sample" in sys.argv
    resume      = "--resume" in sys.argv or BANK_PATH.exists()

    print("Extracting style definitions from storyboard.py…", flush=True)
    style_defs  = _extract_style_defs()
    all_styles  = _extract_style_names()
    print(f"  {len(all_styles)} styles found in enum.", flush=True)

    # Load existing bank for resumable runs
    bank: dict = {}
    if BANK_PATH.exists() and resume:
        bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
        print(f"  Resuming: {len(bank)} styles already in bank.", flush=True)

    already_done = set(bank.keys())
    remaining    = [s for s in all_styles if s not in already_done]

    if sample_only:
        remaining = remaining[:4]
        print(f"\nSAMPLE MODE — generating {len(remaining)} styles: {remaining}\n", flush=True)

    if not remaining:
        print("Nothing to generate — bank is complete.", flush=True)
    else:
        client  = Anthropic()
        batches = [remaining[i:i+BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
        print(f"  {len(remaining)} styles to generate across {len(batches)} batch(es).\n", flush=True)

        for batch_num, batch in enumerate(batches, 1):
            print(f"Batch {batch_num}/{len(batches)}: {batch}", flush=True)

            try:
                result = _generate_batch(client, batch, style_defs)
            except Exception as exc:
                print(f"  ERROR on batch {batch_num}: {exc}", flush=True)
                print("  Saving progress and aborting.", flush=True)
                break

            for style in batch:
                if style not in result:
                    print(f"  WARNING: '{style}' missing from response", flush=True)
                    continue
                entry = result[style]
                t1 = entry.get("tier1", [])
                t2 = entry.get("tier2", [])
                t3 = entry.get("tier3", [])
                total = len(t1) + len(t2) + len(t3)
                if total < 25:
                    print(
                        f"  WARNING: '{style}' short — "
                        f"{len(t1)} tier1 / {len(t2)} tier2 / {len(t3)} tier3",
                        flush=True,
                    )
                bank[style] = {"tier1": t1, "tier2": t2, "tier3": t3}
                print(
                    f"  ✓ {style}: {len(t1)} clean + {len(t2)} oral + {len(t3)} indirect "
                    f"= {total} phrases",
                    flush=True,
                )

            if not sample_only:
                BANK_PATH.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  → saved ({len(bank)}/{len(all_styles)} styles)\n", flush=True)

            if batch_num < len(batches):
                time.sleep(0.5)

    # ── Output ────────────────────────────────────────────────────────────────
    if sample_only:
        print("\n" + "="*60)
        print("SAMPLE — 4 styles, 2 phrases per tier shown")
        print("="*60)
        for style, entry in list(bank.items())[:4]:
            print(f"\n── {style} ──")
            print("  TIER 1 — clean:")
            for p in entry.get("tier1", [])[:2]:
                print(f"    • {p}")
            print("  TIER 2 — oral/noisy:")
            for p in entry.get("tier2", [])[:2]:
                print(f"    • {p}")
            print("  TIER 3 — indirect/buried:")
            for p in entry.get("tier3", [])[:2]:
                print(f"    • {p}")
        print(
            "\n→ If sample looks good, run WITHOUT --sample to generate all 93 styles."
        )
    else:
        print(f"\nBank complete: {len(bank)} styles → {BANK_PATH}", flush=True)


if __name__ == "__main__":
    main()
