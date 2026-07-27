#!/usr/bin/env python3
"""
Zone-timing audit — hypothesis b (zone) vs c (early timing) for desync.

Reads the storyboard from a job directory and prints a correlation table:
  - card ID, type, startSec, zone, position index
  - flags: EARLY (<15s) vs LATE (>=15s), TOP-CORNER vs CENTER zone

Usage:
    python test_zone_timing_audit.py [job_dir]

If job_dir is omitted, the most recently modified job under /data/work/ is used.
Run on Railway shell, or point to a local job dir if available.

HOW TO READ THE OUTPUT:
  If any LATE + TOP-CORNER card shows no desync → hypothesis (c) wins
  (timing is the cause; the zone doesn't matter once out of the first 15s).

  If a LATE + TOP-CORNER card still feels desynced → hypothesis (b) wins
  (top-corner zones are systematically affected regardless of timestamp).
"""

import sys
import json
from pathlib import Path

WORK_ROOT = Path("/data/work")

TOP_CORNER_ZONES = {
    "landscape-tl", "landscape-tr",
    "landscape-tl-tall", "landscape-tr-tall",
    "upper-left-data-sm", "upper-data",
    "upper-left-data", "upper-right-data-tall",
}

EARLY_THRESHOLD_S = 15.0   # cards before this are "early" (Whisper bias window)


def find_job_dir(arg: str | None) -> Path:
    if arg:
        d = Path(arg)
        if d.is_dir():
            return d
        sys.exit(f"ERROR: {d} is not a directory")
    if WORK_ROOT.exists():
        dirs = [d for d in WORK_ROOT.iterdir() if d.is_dir()]
        if dirs:
            return max(dirs, key=lambda d: d.stat().st_mtime)
    sys.exit(
        "ERROR: no job dir found under /data/work/\n"
        "Pass the path explicitly: python test_zone_timing_audit.py /path/to/job_dir"
    )


def main() -> None:
    job_dir = find_job_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    sb_path = job_dir / "storyboard.json"
    if not sb_path.exists():
        sys.exit(f"ERROR: {sb_path} not found")

    storyboard = json.loads(sb_path.read_text())
    all_cards = storyboard.get("cards", [])
    graphic = [c for c in all_cards if c.get("type") != "caption"]

    print(f"\n[AUDIT] Job: {job_dir.name}")
    print(f"[AUDIT] Graphic cards: {len(graphic)}\n")

    hdr = f"{'IDX':>3}  {'CARD-ID':<22}  {'STYLE':<28}  {'ZONE':<22}  {'START':>7}  {'FLAGS'}"
    print(hdr)
    print("─" * len(hdr))

    data_idx = 0  # only increments for data-panel types (same counter as compose)
    for card in graphic:
        cid   = card.get("id", "?")
        ctype = card.get("contentHints", {}).get("style", card.get("type", "?"))
        zone  = card.get("zone", "?")
        start = float(card.get("startSec", -1))

        is_top_corner = zone in TOP_CORNER_ZONES
        is_early      = 0 <= start < EARLY_THRESHOLD_S

        flags = []
        if is_early:
            flags.append("EARLY(<15s)")
        else:
            flags.append("LATE(>=15s)")
        if is_top_corner:
            flags.append("TOP-CORNER")
        else:
            flags.append("CENTER")

        marker = " ◄ CHECK" if is_top_corner and not is_early else ""

        print(
            f"{data_idx:>3}  {cid:<22}  {ctype:<28}  {zone:<22}  "
            f"{start:>7.2f}s  {' | '.join(flags)}{marker}"
        )
        data_idx += 1

    print()
    print("KEY: '◄ CHECK' = LATE card in TOP-CORNER zone — watch this card for desync.")
    print("     If it feels fine → early-timing bias (Whisper) is the cause (hyp. c).")
    print("     If it still feels off → zone position is the cause (hyp. b).\n")


if __name__ == "__main__":
    main()
