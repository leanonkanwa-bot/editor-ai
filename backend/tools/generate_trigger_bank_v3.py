#!/usr/bin/env python3
"""
Generate trigger bank v3 — 100 ADDITIONAL phrases per style (15 T1 + 15 T2 + 70 T3).

v1 covered clean / oral hesitant / indirect (10+10+10 each).
v2 covered register-varied / adversarial oral / deeply indirect (5+5+20 each).
v3 pushes the frontier: boundary-stress clean, compound-trigger oral, ultra-hard adversarial.

Usage:
    python backend/tools/generate_trigger_bank_v3.py --sample
    python backend/tools/generate_trigger_bank_v3.py --types income_reveal,data_bar_chart,stat
    python backend/tools/generate_trigger_bank_v3.py
    python backend/tools/generate_trigger_bank_v3.py --resume
    python backend/tools/generate_trigger_bank_v3.py --model claude-opus-4-7
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

BANK_V3_PATH = Path(__file__).parent / "trigger_bank_v3.json"


def _sanitize_json_strings(raw: str) -> str:
    """Replace literal newlines/tabs inside JSON string values with spaces.

    LLMs occasionally emit multi-line text for long anecdote phrases, producing
    illegal bare newlines inside JSON strings and causing 'Unterminated string'
    parse errors. This scan is character-level and correctly handles escaped
    characters (e.g. \\" inside a string does NOT close it).
    """
    out: list[str] = []
    in_string = False
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and in_string:
            out.append(c)
            i += 1
            if i < len(raw):
                out.append(raw[i])
                i += 1
            continue
        if c == '"':
            in_string = not in_string
        elif in_string and c in ("\n", "\r", "\t"):
            out.append(" ")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)
DEFAULT_MODEL = "claude-sonnet-5"
BATCH_SIZE    = 1   # 1 type × 100 phrases ≈ 8-12K output — avoids JSON malformation on long T3


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


def _generate_batch(
    client: Anthropic,
    style_names: list[str],
    style_defs: str,
    model: str,
) -> dict:
    """Return {style: {tier1: [...15], tier2: [...15], tier3: [...70]}} per style."""
    styles_list = "\n".join(f"- {s}" for s in style_names)

    system = [
        {
            "type": "text",
            "text": (
                "You are a bilingual content-type expert specializing in FRONTIER ADVERSARIAL cases — "
                "phrases that stress-test the boundary between similar styles.\n\n"
                "STYLE DEFINITIONS (authoritative):\n\n"
                f"{style_defs}"
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    user = (
        f"For each of the following {len(style_names)} card types, generate exactly "
        "100 phrases in THREE TIERS. This is bank v3 — v1 and v2 already cover standard direct "
        "phrases and register variation. Focus on NEW adversarial patterns not covered before.\n\n"

        "TIER 1 — BOUNDARY-STRESS CLEAN (15 phrases total):\n"
        "Direct trigger signal, but surface framing deliberately resembles a NEIGHBOR style. "
        "A naive classifier might misfire to the wrong style, but careful reading confirms this one.\n"
        "  • 4 phrases where the surface wording echoes a neighbor style's keywords "
        "    (e.g., for 'income_reveal': uses words like 'données', 'chiffre' from stat/data_bar_chart "
        "    territory, but is unmistakably a personal revenue disclosure)\n"
        "  • 3 phrases with counter-intuitive framing ('contrairement à ce qu'on croit...', "
        "    'ce n'est pas un simple chiffre...') that still trigger the correct style\n"
        "  • 3 phrases where the speaker downplays the signal ('ça peut paraître anodin, "
        "    mais...', 'ce n'est qu'un exemple, mais...')\n"
        "  • 3 phrases with rhetorical question opener that then delivers the trigger signal "
        "    ('vous savez ce que j'ai découvert ? X.')\n"
        "  • 2 phrases in a language mix (FR+EN in same sentence) that is clean but boundary-stress\n\n"

        "TIER 2 — COMPOUND TRIGGER / DIALOGUE-EMBEDDED (15 phrases total):\n"
        "Phrase contains MULTIPLE style signals; the LLM must pick the strongest one. "
        "Or the signal is delivered through a quoted exchange or multi-voice situation.\n"
        "  • 4 phrases: speaker quotes someone else delivering the trigger "
        "    ('elle m'a dit quelque chose qui m'a marqué : [trigger signal]')\n"
        "  • 3 phrases: trigger appears as a correction or clarification within a dialogue\n"
        "  • 3 phrases: multiple potential style signals compete — correct one wins because "
        "    of the primary emphasis\n"
        "  • 3 phrases: speaker is reading aloud from a text, report, or screen\n"
        "  • 2 phrases: speaker introduces then retracts/corrects before the final trigger signal\n\n"

        "TIER 3 — ULTRA-HARD ADVERSARIAL (70 phrases total):\n"
        "These are the hardest cases. Signal MUST be present and verifiable, "
        "but hidden behind one of these NOVEL adversarial patterns:\n"
        "  • 8 phrases: trigger buried in a conditional or hypothetical "
        "    ('si on regarde les résultats de manière objective, on verrait que...')\n"
        "  • 8 phrases: negative assertion framing — speaker says what it is NOT, "
        "    but in doing so, reveals the trigger "
        "    ('ce n'est pas juste un pourcentage aléatoire — c'est 68% de...')\n"
        "  • 8 phrases: metadiscursive wrap — speaker announces what they're about to say "
        "    before saying it ('je vais te donner un chiffre qui va te surprendre, "
        "    et ce chiffre c'est...')\n"
        "  • 8 phrases: cultural/domain specificity — trigger expressed with vocabulary "
        "    from a specific domain (sport, médecine, cuisine, tech, finance) "
        "    that avoids standard trigger keywords\n"
        "  • 8 phrases: signal present only in the LAST sentence of a long anecdote "
        "    (3-5 sentences); the first sentences are about something else entirely\n"
        "  • 7 phrases: signal attributed to a third party then commented on "
        "    ('j'ai lu que selon [person/source], [trigger] — et honnêtement, ça m'a fait réfléchir')\n"
        "  • 7 phrases: temporal/aspectual distancing "
        "    ('ça fait maintenant X mois que je réalise que...', 'depuis X ans je savais que...')\n"
        "  • 7 phrases: minimal signal — shortest possible phrase that still classifies correctly; "
        "    forces the model to commit with limited evidence\n"
        "  • 7 phrases: speaker contradicts or expresses skepticism ABOUT the content "
        "    but still presents it ('je suis pas forcément d\\'accord, mais les chiffres montrent...')\n\n"

        "Critical rules:\n"
        "- NEVER use the style name in any phrase\n"
        "- Each phrase is 1-6 spoken sentences\n"
        "- Tier 3 phrases MUST NOT use the primary explicit trigger keyword of that style "
        "  (use synonyms, circumlocutions, domain-specific vocabulary instead)\n"
        "- Every phrase must be realistically speakable in a talking-head video\n"
        "- Mix FR and EN: approximately 60% FR, 40% EN across all tiers\n"
        "- Tier 3 minimal-signal phrases (last 7) may be shorter — 1-2 sentences max\n"
        "- Do NOT recycle phrases from standard bank v1 or v2 patterns "
        "  (avoid 'l'autre jour', 'mon pote m'a dit', 'si je me souviens bien' as openers)\n\n"
        f"Card types:\n{styles_list}\n\n"
        "Return ONLY valid JSON, no markdown:\n"
        '{"style_name": {"tier1": ["p1",...,"p15"], '
        '"tier2": ["p1",...,"p15"], '
        '"tier3": ["p1",...,"p70"]}, ...}'
    )

    for attempt in range(1, 4):  # up to 3 attempts per batch
        raw_parts: list[str] = []
        try:
            with client.messages.stream(
                model=model,
                max_tokens=32000,
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                for text in stream.text_stream:
                    raw_parts.append(text)
        except Exception as exc:
            print(f"  Streaming error (attempt {attempt}/3): {exc}", flush=True)
            if attempt == 3:
                raise
            time.sleep(2)
            continue

        raw = "".join(raw_parts).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        # Sanitise literal newlines/tabs inside JSON strings.
        # Long T3 "anecdote" phrases sometimes span multiple lines in the LLM
        # output, which produces an "Unterminated string" JSON parse error.
        raw = _sanitize_json_strings(raw)

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"  JSON parse error (attempt {attempt}/3): {exc}", flush=True)
            print(f"  Response start: {repr(raw[:400])}", flush=True)
            print(f"  Response end:   {repr(raw[-400:])}", flush=True)
            if attempt == 3:
                raise
            print(f"  Retrying…", flush=True)
            time.sleep(2)

    raise RuntimeError("unreachable")


def main() -> None:
    sample_only = "--sample" in sys.argv
    resume      = "--resume" in sys.argv or BANK_V3_PATH.exists()

    # Model override
    model = DEFAULT_MODEL
    for arg in sys.argv:
        if arg.startswith("--model="):
            model = arg.split("=", 1)[1]

    # Types override
    types_override: list[str] | None = None
    for arg in sys.argv:
        if arg.startswith("--types="):
            types_override = [t.strip() for t in arg.split("=", 1)[1].split(",") if t.strip()]

    print(f"Model: {model}", flush=True)
    print("Extracting style definitions from storyboard.py…", flush=True)
    style_defs = _extract_style_defs()
    all_styles = _extract_style_names()
    print(f"  {len(all_styles)} styles found.", flush=True)

    bank: dict = {}
    if BANK_V3_PATH.exists() and resume:
        bank = json.loads(BANK_V3_PATH.read_text(encoding="utf-8"))
        print(f"  Resuming: {len(bank)} styles already in bank v3.", flush=True)

    # Determine which styles to generate
    if types_override is not None:
        unknown = [t for t in types_override if t not in all_styles]
        if unknown:
            print(f"  WARNING: unknown types ignored: {unknown}", flush=True)
        remaining = [t for t in types_override if t in all_styles and t not in bank]
    else:
        already_done = set(bank.keys())
        remaining = [s for s in all_styles if s not in already_done]

    if sample_only:
        remaining = remaining[:2]
        print(f"\nSAMPLE MODE — {len(remaining)} styles: {remaining}\n", flush=True)

    if not remaining:
        print("Nothing to generate — all requested types already in bank v3.", flush=True)
    else:
        client  = Anthropic()
        batches = [remaining[i:i+BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
        print(f"  {len(remaining)} styles → {len(batches)} batch(es).\n", flush=True)

        for batch_num, batch in enumerate(batches, 1):
            print(f"Batch {batch_num}/{len(batches)}: {batch}", flush=True)

            try:
                result = _generate_batch(client, batch, style_defs, model)
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
                if total < 80:
                    print(
                        f"  WARNING: '{style}' short — "
                        f"{len(t1)} T1 / {len(t2)} T2 / {len(t3)} T3 (expected 15/15/70)",
                        flush=True,
                    )
                bank[style] = {"tier1": t1, "tier2": t2, "tier3": t3}
                print(
                    f"  OK {style}: {len(t1)} T1 + {len(t2)} T2 + {len(t3)} T3 "
                    f"= {total} phrases",
                    flush=True,
                )

            if not sample_only:
                BANK_V3_PATH.write_text(
                    json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"  -> saved ({len(bank)} styles done)\n", flush=True)

            if batch_num < len(batches):
                time.sleep(0.3)

    if sample_only and bank:
        print("\n" + "=" * 65)
        print("SAMPLE — 2 styles, 3 phrases per tier shown")
        print("=" * 65)
        for style, entry in list(bank.items())[:2]:
            print(f"\n-- {style} --")
            print("  TIER 1 — boundary-stress clean:")
            for p in entry.get("tier1", [])[:3]:
                print(f"    * {p}")
            print("  TIER 2 — compound trigger / dialogue-embedded:")
            for p in entry.get("tier2", [])[:3]:
                print(f"    * {p}")
            print("  TIER 3 — ultra-hard adversarial (first 5):")
            for p in entry.get("tier3", [])[:5]:
                print(f"    * {p}")
        print("\n-> Run WITHOUT --sample to generate all styles.")
    elif not sample_only and bank:
        total_phrases = sum(
            len(v["tier1"]) + len(v["tier2"]) + len(v["tier3"]) for v in bank.values()
        )
        print(f"\nBank v3: {len(bank)} styles, {total_phrases} phrases -> {BANK_V3_PATH}", flush=True)


if __name__ == "__main__":
    main()
