#!/usr/bin/env python3
"""
Standalone test: load latest storyboard + run compose(), print first 50 lines of HTML.
No HyperFrames render is launched.

Usage (on Railway shell or locally):
    python test_compose.py [job_id]

If job_id is omitted, the most-recently-modified job dir under /data/work/ is used.
"""
import sys
import json
import shutil
import tempfile
from pathlib import Path

WORK_ROOT = Path("/data/work")


def find_job_dir(job_id: str | None) -> Path:
    if job_id:
        # Un chemin complet est accepte tel quel : sans cela, le seul moyen
        # de jouer le test etait d'avoir /data/work, donc d'etre dans le
        # conteneur.
        _p = Path(job_id)
        if _p.is_dir():
            return _p
        d = WORK_ROOT / job_id
        if not d.is_dir():
            sys.exit(f"ERROR: {d} does not exist")
        return d

    # WORK_ROOT n'existe que dans le conteneur. Hors de la, iterdir levait
    # un FileNotFoundError brut : le test paraissait casse alors qu'il
    # manquait seulement ses donnees. On le dit, comme
    # test_zone_timing_audit le fait deja.
    if not WORK_ROOT.is_dir():
        sys.exit(
            f"IMPOSSIBLE A JOUER ICI : {WORK_ROOT} n'existe pas.\n"
            "Ce test lit un repertoire de travail reel, qui ne vit que dans\n"
            "le conteneur. Donnez-lui un chemin :\n"
            "    python test_compose.py /chemin/vers/un/job_dir\n"
            "Ce n'est pas un echec du code : il n'y a rien a mesurer."
        )
    candidates = [d for d in WORK_ROOT.iterdir() if d.is_dir()]
    if not candidates:
        sys.exit(f"ERROR: no job dirs found under {WORK_ROOT}")
    return max(candidates, key=lambda d: d.stat().st_mtime)


def main():
    job_id = sys.argv[1] if len(sys.argv) > 1 else None
    job_dir = find_job_dir(job_id)
    print(f"[TEST] Using job dir: {job_dir}")

    storyboard_path = job_dir / "storyboard.json"
    if not storyboard_path.exists():
        sys.exit(f"ERROR: {storyboard_path} not found")

    storyboard = json.loads(storyboard_path.read_text())
    cards = storyboard.get("cards", [])
    graphic = sum(1 for c in cards if c.get("type") != "caption")
    caption = sum(1 for c in cards if c.get("type") == "caption")
    print(f"[TEST] Storyboard loaded: {graphic} graphic + {caption} caption cards")

    trimmed_video = job_dir / "trimmed.mp4"
    if not trimmed_video.exists():
        sys.exit(f"ERROR: {trimmed_video} not found (pretrim not yet run for this job)")

    # Use a temp work dir so we don't pollute the real job dir
    with tempfile.TemporaryDirectory(prefix="test_compose_") as tmp:
        work_dir = Path(tmp)

        # compose() copies trimmed.mp4 into hf_project/public/ — we need the
        # engine package on sys.path first
        engine_dir = Path(__file__).resolve().parent
        if str(engine_dir) not in sys.path:
            sys.path.insert(0, str(engine_dir))

        from app.engine.compose import compose

        project_dir = compose(
            storyboard=storyboard,
            trimmed_video=trimmed_video,
            work_dir=work_dir,
        )

        index_html = project_dir / "public" / "index.html"
        if not index_html.exists():
            sys.exit(f"ERROR: {index_html} was not created by compose()")

        lines = index_html.read_text(encoding="utf-8").splitlines()
        print(f"\n[TEST] index.html — first 50 lines ({len(lines)} total):")
        print("─" * 72)
        for i, line in enumerate(lines[:50], 1):
            print(f"{i:3d}  {line}")
        print("─" * 72)
        print(f"[TEST] Done. Project dir was: {project_dir}")


if __name__ == "__main__":
    main()
