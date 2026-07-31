#!/usr/bin/env python3
"""
Étape 2 — Test batch étendu sur la banque de phrases-déclencheurs.

Charge trigger_bank.json (96 styles × 30 phrases × 3 tiers), classe chaque
phrase via Claude en batches de 300, produit un rapport classé pire-premier
avec détail par tier (clean / oral / indirect).

Usage:
    python backend/tools/test_trigger_bank_coverage.py
    python backend/tools/test_trigger_bank_coverage.py --save results_bank.json
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT      = Path(__file__).parent.parent.parent
BANK_PATH = Path(__file__).parent / "trigger_bank.json"

# ── API key ──────────────────────────────────────────────────────────────────
import os
_env_file = ROOT / "backend" / ".env"
if _env_file.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if _line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = _line.split("=", 1)[1].strip()
            break

from anthropic import Anthropic  # noqa: E402

BATCH_SIZE = 300   # phrases per API call; ~4 000 output tokens, safe within 8 192


# ── Extraction ────────────────────────────────────────────────────────────────

def _extract_style_defs() -> str:
    src = (ROOT / "backend" / "app" / "engine" / "storyboard.py").read_text(encoding="utf-8")
    s = src.find("- CONTENT STYLE RULES (follow strictly, do not improvise):")
    e = src.find("- VERBATIM GROUNDING")
    if s == -1 or e == -1:
        raise RuntimeError("Cannot locate style-definition boundaries in storyboard.py")
    return src[s:e].strip()


def _load_cases() -> list[tuple[str, str, str]]:
    """Return list of (expected_style, tier, phrase)."""
    bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    cases = []
    for style, entry in bank.items():
        for tier in ("tier1", "tier2", "tier3"):
            for phrase in entry.get(tier, []):
                cases.append((style, tier, phrase))
    return cases


# ── Classifier ────────────────────────────────────────────────────────────────

def _classify_batch(
    client: Anthropic,
    batch: list[tuple[str, str, str]],
    style_defs: str,
    offset: int,
) -> list[dict]:
    numbered = "\n".join(
        f"{offset + i + 1}. {phrase}"
        for i, (_, _, phrase) in enumerate(batch)
    )

    system = [
        {
            "type": "text",
            "text": (
                "You are a precise content-style classifier.\n\n"
                "STYLE DEFINITIONS (source of truth — use these exactly):\n\n"
                f"{style_defs}\n\n"
                "Rules:\n"
                "- Choose the MOST SPECIFIC style whose trigger conditions are satisfied.\n"
                "- Never invent style names — only use names from the definitions above.\n"
                "- Return ONLY a compact JSON array, no markdown, no explanation:\n"
                '  [{"id":1,"style":"style_name"},{"id":2,"style":"style_name"},...]'
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    user = (
        f"Classify each of the following {len(batch)} phrases. "
        "Return exactly one JSON object per phrase, in order, "
        "with 'id' (matching the number shown) and 'style'.\n\n"
        + numbered
    )

    resp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    raw_results: list[dict] = json.loads(raw)

    if len(raw_results) != len(batch):
        print(
            f"  WARNING: expected {len(batch)} results, got {len(raw_results)}",
            flush=True,
        )

    results = []
    for i, (expected, tier, phrase) in enumerate(batch):
        assigned = raw_results[i]["style"] if i < len(raw_results) else "MISSING"
        results.append({
            "id":       offset + i + 1,
            "expected": expected,
            "tier":     tier,
            "assigned": assigned,
            "phrase":   phrase,
            "correct":  assigned == expected,
        })
    return results


# ── Report ────────────────────────────────────────────────────────────────────

def generate_report(all_results: list[dict]) -> None:
    # Per-style, per-tier tallies
    by_style: dict[str, dict] = defaultdict(lambda: {
        "tier1": {"total": 0, "correct": 0, "collisions": []},
        "tier2": {"total": 0, "correct": 0, "collisions": []},
        "tier3": {"total": 0, "correct": 0, "collisions": []},
    })

    for r in all_results:
        td = by_style[r["expected"]][r["tier"]]
        td["total"]   += 1
        td["correct"] += int(r["correct"])
        if not r["correct"]:
            td["collisions"].append(r["assigned"])

    # Build rows
    rows = []
    for style, tiers in by_style.items():
        total   = sum(t["total"]   for t in tiers.values())
        correct = sum(t["correct"] for t in tiers.values())
        rate    = correct / total * 100 if total else 0

        t1, t2, t3 = tiers["tier1"], tiers["tier2"], tiers["tier3"]

        all_coll = t1["collisions"] + t2["collisions"] + t3["collisions"]
        coll_str = ", ".join(
            f"{s}({all_coll.count(s)}x)" for s in sorted(set(all_coll))
        ) if all_coll else ""

        rows.append((rate, style, total, correct, t1, t2, t3, coll_str))

    rows.sort(key=lambda r: (r[0], r[1]))   # worst first

    total_correct = sum(1 for r in all_results if r["correct"])
    total         = len(all_results)
    n_styles      = len(by_style)

    W = 120
    print(f"\n{'='*W}")
    print(
        f"TRIGGER BANK COVERAGE REPORT  —  {total} phrases  |  "
        f"{n_styles} styles  |  model: claude-opus-4-7"
    )
    print(f"{'='*W}")
    hdr = f"{'STYLE':<36} {'TOT':>3} {'OK':>3} {'RATE':>5}   T1%  T2%  T3%   COLLISIONS"
    print(hdr)
    print(f"{'-'*W}")

    weak_styles = []
    for rate, style, total_s, correct_s, t1, t2, t3, coll_str in rows:
        r1 = t1["correct"] / t1["total"] * 100 if t1["total"] else 0
        r2 = t2["correct"] / t2["total"] * 100 if t2["total"] else 0
        r3 = t3["correct"] / t3["total"] * 100 if t3["total"] else 0
        flag = "⚠  " if rate < 80 else "   "
        print(
            f"{flag}{style:<33} {total_s:>3} {correct_s:>3} {rate:>4.0f}%"
            f"  {r1:>3.0f}% {r2:>3.0f}% {r3:>3.0f}%   {coll_str}"
        )
        if rate < 80:
            weak_styles.append((style, rate, t1, t2, t3, coll_str))

    print(f"{'-'*W}")
    print(f"OVERALL: {total_correct}/{total} correct ({total_correct/total*100:.1f}%)")
    print(f"{'='*W}\n")

    if weak_styles:
        print(f"⚠  STYLES BELOW 80% — candidates for definition enrichment ({len(weak_styles)} types):\n")
        for style, rate, t1, t2, t3, coll_str in weak_styles:
            print(f"  {style}  ({rate:.0f}%)  — collisions: {coll_str or 'none'}")
            # Show worst-tier failures (up to 3)
            failures = [
                r for r in all_results
                if r["expected"] == style and not r["correct"]
            ]
            for f in failures[:3]:
                preview = f["phrase"][:100] + ("…" if len(f["phrase"]) > 100 else "")
                print(f"    [{f['tier']}] → got [{f['assigned']}]  \"{preview}\"")
        print()
    else:
        print("All styles ≥ 80% — no enrichment needed.\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not BANK_PATH.exists():
        print(f"ERROR: {BANK_PATH} not found — run generate_trigger_bank.py first.")
        sys.exit(1)

    print("Loading trigger bank…", flush=True)
    cases = _load_cases()
    print(f"  {len(cases)} phrases across {len(set(c[0] for c in cases))} styles.", flush=True)

    print("Extracting style definitions from storyboard.py…", flush=True)
    style_defs = _extract_style_defs()

    client  = Anthropic()
    batches = [cases[i:i+BATCH_SIZE] for i in range(0, len(cases), BATCH_SIZE)]
    print(f"  Classifying in {len(batches)} batch(es) of ≤{BATCH_SIZE} phrases…\n", flush=True)

    all_results: list[dict] = []
    for batch_num, batch in enumerate(batches, 1):
        offset = (batch_num - 1) * BATCH_SIZE
        print(f"Batch {batch_num}/{len(batches)} ({len(batch)} phrases)…", flush=True)
        batch_results = _classify_batch(client, batch, style_defs, offset)
        correct = sum(1 for r in batch_results if r["correct"])
        print(f"  → {correct}/{len(batch)} correct ({correct/len(batch)*100:.1f}%)", flush=True)
        all_results.extend(batch_results)

    if "--save" in sys.argv:
        idx      = sys.argv.index("--save")
        out_path = Path(sys.argv[idx + 1])
        out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nRaw results saved → {out_path}")

    generate_report(all_results)


if __name__ == "__main__":
    main()
