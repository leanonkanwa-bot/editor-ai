#!/usr/bin/env python3
"""
export_profiles.py — dump all coach profiles to terminal + optional CSV.

Usage:
    python backend/scripts/export_profiles.py
    python backend/scripts/export_profiles.py --csv profiles_export.csv

Profiles are stored in  <data_root>/profiles/*.json
where data_root defaults to  backend/storage/  (local dev)
or /data/  on Railway (set via DATA_DIR env var).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

# ── Locate data root ──────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]  # …/EDITOR AI
data_dir = os.environ.get("DATA_DIR", "storage")
data_root = Path(data_dir) if Path(data_dir).is_absolute() else (REPO_ROOT / "backend" / data_dir).resolve()
profiles_dir = data_root / "profiles"


def load_profiles() -> list[dict]:
    if not profiles_dir.exists():
        print(f"[ERROR] Profiles directory not found: {profiles_dir}", file=sys.stderr)
        sys.exit(1)
    profiles = []
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_profile_id"] = path.stem
            profiles.append(data)
        except Exception as e:
            print(f"[WARN] Could not read {path.name}: {e}", file=sys.stderr)
    return profiles


# ── Display helpers ───────────────────────────────────────────────────────────

COL = 22  # label column width

def row(label: str, value: str) -> str:
    return f"  {label:<{COL}}{value}"

def fmt_list(items: list) -> str:
    cleaned = [str(i).strip() for i in items if str(i).strip()]
    return ", ".join(cleaned) if cleaned else "—"


def print_profile(p: dict) -> None:
    sep = "-" * 60
    print(sep)
    print(f"  {p.get('name') or p.get('email') or p['_profile_id']}")
    print(sep)
    print(row("Profile ID :", p["_profile_id"]))
    print(row("Email :", p.get("email") or "—"))
    print(row("Marque :", p.get("brandName") or "—"))
    print(row("Rôle :", p.get("role") or "—"))
    print(row("Plan :", p.get("plan") or "free"))
    print(row("Plateformes :", fmt_list(p.get("platforms") or [])))
    icp = (p.get("icp") or "").strip()
    if len(icp) > 80:
        icp = icp[:77] + "..."
    print(row("ICP :", icp or "—"))
    pillars = [str(x).strip() for x in (p.get("pillars") or []) if str(x).strip()]
    for i, pil in enumerate(pillars, 1):
        print(row(f"Pilier {i} :", pil))
    print()


def print_summary(profiles: list[dict]) -> None:
    print("=" * 60)
    print(f"  RESUME -- {len(profiles)} profil(s)")
    print("=" * 60)

    plan_counter: Counter = Counter()
    plat_counter: Counter = Counter()
    pillar_counter: Counter = Counter()

    for p in profiles:
        plan_counter[p.get("plan") or "free"] += 1
        for pl in (p.get("platforms") or []):
            if pl:
                plat_counter[str(pl)] += 1
        for pil in (p.get("pillars") or []):
            if str(pil).strip():
                pillar_counter[str(pil).strip().lower()] += 1

    print("\n  Répartition par plan :")
    for plan, count in plan_counter.most_common():
        print(f"    {plan:<20} {count}")

    if plat_counter:
        print("\n  Plateformes les plus choisies :")
        for plat, count in plat_counter.most_common():
            bar = "#" * count
            print(f"    {plat:<14} {bar} ({count})")

    if pillar_counter:
        print("\n  Piliers de contenu les plus fréquents :")
        for pil, count in pillar_counter.most_common(10):
            print(f"    {pil:<30} ({count})")

    print()


# ── CSV export ────────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "_profile_id", "name", "email", "brandName", "role", "plan",
    "platforms", "icp", "pillar_1", "pillar_2", "pillar_3",
]


def export_csv(profiles: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for p in profiles:
            pillars = p.get("pillars") or []
            writer.writerow({
                "_profile_id": p["_profile_id"],
                "name":        p.get("name") or "",
                "email":       p.get("email") or "",
                "brandName":   p.get("brandName") or "",
                "role":        p.get("role") or "",
                "plan":        p.get("plan") or "free",
                "platforms":   " | ".join(x for x in (p.get("platforms") or []) if x),
                "icp":         (p.get("icp") or "").strip(),
                "pillar_1":    pillars[0] if len(pillars) > 0 else "",
                "pillar_2":    pillars[1] if len(pillars) > 1 else "",
                "pillar_3":    pillars[2] if len(pillars) > 2 else "",
            })
    print(f"[CSV] Exported {len(profiles)} profil(s) → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Export LeanRetention coach profiles")
    parser.add_argument("--csv", metavar="FILE", help="also export to CSV at this path")
    args = parser.parse_args()

    print(f"\nProfiles dir : {profiles_dir}\n")
    profiles = load_profiles()

    if not profiles:
        print("No profiles found.")
        return

    for p in profiles:
        print_profile(p)

    print_summary(profiles)

    if args.csv:
        export_csv(profiles, args.csv)


if __name__ == "__main__":
    main()
