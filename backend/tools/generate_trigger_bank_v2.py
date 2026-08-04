#!/usr/bin/env python3
"""
Generate ADDITIONAL trigger phrases (bank v2) for all 96 storyboard card types.

Unlike v1 (clean/oral/indirect, 10 each), this batch pushes:
  - Harder Tier 3: signal buried deeper, indirect vocabulary, narrative context
  - Register diversity: québécois, belge, africain francophone, corporate,
    millennial argot, hesitant/low-confidence speaker
  - Adversarial framing: speaker contradicts, hedges, or mentions the trigger
    as a side note inside another story
  - 5 T1 + 5 T2 + 20 T3 = 30 additional phrases per style

Output: trigger_bank_v2.json (same schema as trigger_bank.json)
Usage:
    python backend/tools/generate_trigger_bank_v2.py --sample
    python backend/tools/generate_trigger_bank_v2.py
    python backend/tools/generate_trigger_bank_v2.py --resume
"""

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

import os
_env_file = ROOT / "backend" / ".env"
if _env_file.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if _line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = _line.split("=", 1)[1].strip()
            break

from anthropic import Anthropic  # noqa: E402

BANK_V2_PATH = Path(__file__).parent / "trigger_bank_v2.json"
BATCH_SIZE   = 6   # smaller batches: 6 × 30 phrases but T3 is longer → safe token margin


def _extract_style_defs() -> str:
    src = (ROOT / "backend" / "app" / "engine" / "storyboard.py").read_text(encoding="utf-8")
    s = src.find("- CONTENT STYLE RULES (follow strictly, do not improvise):")
    e = src.find("- VERBATIM GROUNDING")
    if s == -1 or e == -1:
        raise RuntimeError("Cannot locate style-definition boundaries in storyboard.py")
    return src[s:e].strip()


def _extract_style_names() -> list[str]:
    src = (ROOT / "backend" / "app" / "engine" / "storyboard.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        if '"style":' in line and "prim_journey_map" in line:
            names = re.findall(r'"([^"]+)"', line)
            names = [n for n in names if n != "style"]
            if names:
                return names
    raise RuntimeError("Cannot find style enum in storyboard.py")


def _generate_batch(client: Anthropic, style_names: list[str], style_defs: str) -> dict:
    """Return {style: {tier1: [...5], tier2: [...5], tier3: [...20]}} per style."""
    styles_list = "\n".join(f"- {s}" for s in style_names)

    system = [
        {
            "type": "text",
            "text": (
                "You are a bilingual content-type expert specializing in HARD cases — "
                "phrases where the trigger signal is present but difficult to detect.\n\n"
                "STYLE DEFINITIONS (authoritative):\n\n"
                f"{style_defs}"
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    user = (
        f"For each of the following {len(style_names)} card types, generate exactly "
        "30 phrases in THREE TIERS. This is a SUPPLEMENTARY bank — "
        "the baseline (clean phrases) already exists. "
        "Focus on register diversity and hard indirect cases.\n\n"

        "TIER 1 — REGISTER-VARIED CLEAN (5 phrases total, mix FR/EN): "
        "Direct trigger, but vary the register aggressively:\n"
        "  • 1 phrase in québécois register (e.g. 'c'est le boutte', 'ostie', 'en maudit', 'tu sais')\n"
        "  • 1 phrase in belge/africain francophone register (structures syntaxiques distinctes)\n"
        "  • 1 phrase in very formal corporate register (cold, report-like language)\n"
        "  • 1 phrase by a low-confidence/uncertain speaker ('je crois que', 'si je me souviens bien')\n"
        "  • 1 phrase in millennial/gen-z argot ('en mode', 'genre', 'c'est fou', 'no cap')\n\n"

        "TIER 2 — REGISTER-VARIED ORAL (5 phrases total, mix FR/EN): "
        "Add oral noise AND register variation:\n"
        "  • 2 phrases where the speaker trails off and restarts mid-thought\n"
        "  • 1 phrase where the number/key term is mentioned twice with a self-correction\n"
        "  • 1 phrase mixing French AND English in the same sentence (code-switching)\n"
        "  • 1 phrase with regional oral markers AND hesitations combined\n\n"

        "TIER 3 — DEEPLY INDIRECT (20 phrases total, mix FR/EN): "
        "These are the critical stress tests. "
        "The signal MUST be present and detectable on careful reading, "
        "but a naive classifier would miss it. Use these HARD patterns:\n"
        "  • 4 phrases: signal embedded inside a personal anecdote (the stat/quote/etc. "
        "is mentioned as a side detail in a story about something else entirely)\n"
        "  • 4 phrases: adversarial/skeptical framing — speaker doubts, contests, "
        "or distances themselves from the content ('je suis pas convaincu mais...', "
        "'les gens disent que...', 'j'ai vu passer un truc où...')\n"
        "  • 3 phrases: signal buried in a transition between two other topics "
        "('et d'ailleurs, en parlant de ça...', 'ce qui me fait penser à...')\n"
        "  • 3 phrases: temporal distance — speaker refers to something from weeks/months ago "
        "('l'autre fois je lisais', 'ça m'a rappelé un truc de l'année dernière')\n"
        "  • 3 phrases: speaker attributes the content to someone else "
        "('mon pote m'a dit que', 'une cliente m'a partagé', 'j'ai entendu quelqu'un dire')\n"
        "  • 3 phrases: signal present but expressed with wrong/unusual vocabulary "
        "(e.g. for 'stat': speaker says 'pourcentage', 'proportion', 'ratio', 'part' "
        "instead of standard stat-marker words — NO explicit number visible in the phrase)\n\n"

        "Critical rules:\n"
        "- NEVER use the style name in the phrase\n"
        "- Each phrase is 1-5 spoken sentences\n"
        "- Tier 3 phrases must NEVER use the primary explicit trigger keyword of that style\n"
        "- Every phrase must be realistically speakable in a talking-head video\n"
        "- Mix FR and EN: ~15 FR total, ~15 EN total per style\n"
        "- Tier 3 phrases should be LONGER than Tier 1 (more context = harder to classify)\n\n"
        f"Card types:\n{styles_list}\n\n"
        "Return ONLY valid JSON, no markdown:\n"
        '{"style_name": {"tier1": ["p1","p2","p3","p4","p5"], '
        '"tier2": ["p1","p2","p3","p4","p5"], '
        '"tier3": ["p1",...,"p20"]}, ...}'
    )

    raw_parts: list[str] = []
    with client.messages.stream(
        model="claude-opus-4-7",
        max_tokens=24000,
        system=system,
        messages=[{"role": "user", "content": user}],
    ) as stream:
        for text in stream.text_stream:
            raw_parts.append(text)
    raw = "".join(raw_parts).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"  JSON parse error: {exc}", flush=True)
        print(f"  Response start: {repr(raw[:400])}", flush=True)
        print(f"  Response end:   {repr(raw[-400:])}", flush=True)
        raise


def main() -> None:
    sample_only = "--sample" in sys.argv
    resume      = "--resume" in sys.argv or BANK_V2_PATH.exists()

    print("Extracting style definitions from storyboard.py…", flush=True)
    style_defs = _extract_style_defs()
    all_styles = _extract_style_names()
    print(f"  {len(all_styles)} styles found.", flush=True)

    bank: dict = {}
    if BANK_V2_PATH.exists() and resume:
        bank = json.loads(BANK_V2_PATH.read_text(encoding="utf-8"))
        print(f"  Resuming: {len(bank)} styles already in bank v2.", flush=True)

    already_done = set(bank.keys())
    remaining    = [s for s in all_styles if s not in already_done]

    if sample_only:
        remaining = remaining[:3]
        print(f"\nSAMPLE MODE — {len(remaining)} styles: {remaining}\n", flush=True)

    if not remaining:
        print("Nothing to generate — bank v2 complete.", flush=True)
    else:
        client  = Anthropic()
        batches = [remaining[i:i+BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
        print(f"  {len(remaining)} styles → {len(batches)} batch(es).\n", flush=True)

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
                if total < 20:
                    print(
                        f"  WARNING: '{style}' short — "
                        f"{len(t1)} T1 / {len(t2)} T2 / {len(t3)} T3",
                        flush=True,
                    )
                bank[style] = {"tier1": t1, "tier2": t2, "tier3": t3}
                print(
                    f"  ✓ {style}: {len(t1)} clean + {len(t2)} oral + {len(t3)} indirect "
                    f"= {total} phrases",
                    flush=True,
                )

            if not sample_only:
                BANK_V2_PATH.write_text(
                    json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"  → saved ({len(bank)}/{len(all_styles)} styles)\n", flush=True)

            if batch_num < len(batches):
                time.sleep(0.5)

    if sample_only and bank:
        print("\n" + "=" * 60)
        print("SAMPLE — up to 3 styles, 2 phrases per tier shown")
        print("=" * 60)
        for style, entry in list(bank.items())[:3]:
            print(f"\n── {style} ──")
            print("  TIER 1 — register-varied clean:")
            for p in entry.get("tier1", [])[:2]:
                print(f"    • {p}")
            print("  TIER 2 — register-varied oral:")
            for p in entry.get("tier2", [])[:2]:
                print(f"    • {p}")
            print("  TIER 3 — deeply indirect (first 4):")
            for p in entry.get("tier3", [])[:4]:
                print(f"    • {p}")
        print("\n→ Run WITHOUT --sample to generate all styles.")
    elif not sample_only:
        print(f"\nBank v2 complete: {len(bank)} styles → {BANK_V2_PATH}", flush=True)


if __name__ == "__main__":
    main()
