"""Lister et restaurer les sauvegardes de jobs.json.

A lancer dans le conteneur, la ou le volume est monte :

    railway run python backend/tools/outil_sauvegardes.py liste
    railway run python backend/tools/outil_sauvegardes.py restaure <nom>
    railway run python backend/tools/outil_sauvegardes.py restaure <nom> --ecris

Sans `--ecris`, la restauration est un essai a blanc : elle dit ce qu'elle
ferait et n'ecrit rien. Le mode par defaut est la fusion, qui garde les
jobs crees depuis la sauvegarde ; `--remplace` ecrase tout, et annonce
alors nommement les jobs qu'il perd.

Aucun appel reseau, aucun cout. La restauration prend un instantane du
fichier courant avant d'ecrire, donc une restauration malheureuse est
elle-meme reversible.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import sauvegardes as S                        # noqa: E402


def _liste() -> int:
    copies = S.liste()
    if not copies:
        print(f"aucune sauvegarde dans {S.DOSSIER}")
        return 1
    print(f"{len(copies)} sauvegarde(s) dans {S.DOSSIER}\n")
    print(f"  {'nom':52s} {'jobs':>6s} {'poids':>9s}")
    for c in copies:
        jobs = "illisible" if c["jobs"] is None else str(c["jobs"])
        marque = "  <- reference" if c["reference"] else ""
        print(f"  {c['nom']:52s} {jobs:>6s} "
              f"{c['octets'] / 1e6:8.2f}M{marque}")
    return 0


def _restaure(args: list[str]) -> int:
    if not args:
        print("usage : restaure <nom> [--remplace] [--ecris]")
        return 2
    nom = args[0]
    mode = "remplace" if "--remplace" in args else "fusion"
    a_blanc = "--ecris" not in args
    r = S.restaure(nom, mode=mode, a_blanc=a_blanc)
    if "erreur" in r:
        print(f"ERREUR : {r['erreur']}")
        return 1
    print(f"  sauvegarde            {r['sauvegarde']}")
    print(f"  mode                  {r['mode']}")
    print(f"  jobs dans la copie    {r['jobs_dans_la_sauvegarde']}")
    print(f"  jobs actuels          {r['jobs_actuels']}")
    print(f"  jobs apres            {r['jobs_apres']}")
    print(f"  recuperes             {r['recuperes']}")
    if r["perdus"]:
        print(f"  PERDUS, {len(r['perdus'])} nomme(s)"
              f"{' (liste tronquee a 20)' if len(r['perdus']) == 20 else ''} :"
              f" {', '.join(r['perdus'])}")
    if r["a_blanc"]:
        print("\n  essai a blanc — rien n'a ete ecrit. Ajoutez --ecris.")
    else:
        print(f"\n  ecrit dans {r['ecrit']}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "liste":
        return _liste()
    if args[0] == "restaure":
        return _restaure(args[1:])
    print(f"commande inconnue : {args[0]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
