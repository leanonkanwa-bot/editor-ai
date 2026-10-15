"""Ce que /data/outputs contient, et ce qu'une regle de conservation
supprimerait. CE MODULE NE SUPPRIME RIEN.

Il n'y a aucune fonction de suppression ici, volontairement : la regle doit
etre lue et approuvee sur des chiffres reels avant que quoi que ce soit
disparaisse. Le rapport s'ecrit dans les logs au demarrage, comme
[SAUVEGARDE], parce que c'est le seul moyen de connaitre le contenu du
volume sans ouvrir une route ni se connecter au conteneur.

Six familles de fichiers y vivent, toutes prefixees par l'identifiant du
job (releve dans le code, pas suppose) :

    {id}.mp4             la video livree
    {id}_landscape.mp4   la variante paysage
    {id}_thumb.jpg       la vignette
    {id}_cards.json      le diagnostic des cartes
    {id}_narrative.txt   la frise narrative
    {id}_report.pdf      le rapport d'edition

La purge de corbeille existante (`JobStore.purge_expired_trash`) n'en
supprime que QUATRE : elle oublie `_narrative.txt` et `_report.pdf`. Chaque
job purge laisse donc deux fichiers derriere lui, pour toujours.

La regle proposee, et le raisonnement derriere chaque borne :

  * jamais un job ACTIF — un rendu en cours ecrit dans ces fichiers ;
  * jamais rien de plus recent que PLANCHER_JOURS, quelle que soit la
    regle : c'est la marge contre une erreur de date ou d'horloge ;
  * au-dela de CONSERVATION_JOURS, les fichiers d'un job termine sont
    candidats, et la famille entiere part ensemble — supprimer la video en
    gardant la vignette laisse l'interface afficher une carte morte ;
  * les ORPHELINS, dont l'identifiant n'est dans aucun job, ne sont JAMAIS
    candidats. Le 20/09, jobs.json a ete vide et reconstruit depuis 179
    videos presentes sur le disque : ces orphelins etaient le materiel de
    la reparation. Une regle qui les efface rend la prochaine perte
    definitive. Ils sont comptes et nommes, rien de plus.
"""

from __future__ import annotations

import time
from pathlib import Path

# Au-dela, les fichiers d'un job termine deviennent candidats.
CONSERVATION_JOURS = 30.0
# Plancher absolu : rien de plus recent n'est jamais candidat.
PLANCHER_JOURS = 7.0

SUFFIXES = (".mp4", "_landscape.mp4", "_thumb.jpg", "_cards.json",
            "_narrative.txt", "_report.pdf")
# Statuts ou un worker ecrit encore. Importe de jobs.py a l'usage pour que
# les deux ensembles ne puissent pas diverger.
_CLASSES = ("actif", "recent", "corbeille", "expire", "orphelin", "inconnu")


def _job_id_de(nom: str) -> str | None:
    """L'identifiant porte par ce nom de fichier, ou None."""
    for suf in sorted(SUFFIXES, key=len, reverse=True):
        if nom.endswith(suf):
            tete = nom[: -len(suf)]
            # Un identifiant est un hexadecimal de 32 caracteres.
            if len(tete) == 32 and all(c in "0123456789abcdef" for c in tete):
                return tete
    return None


def inventaire(dossier: Path, jobs: dict, maintenant: float | None = None,
               conservation_jours: float = CONSERVATION_JOURS) -> dict:
    """Classe chaque fichier du dossier. Ne supprime rien, ne leve jamais.

    `jobs` est le dictionnaire du JobStore : {id: Job}. On lit `status`,
    `completed_at`, `created_at` et `trashed_at`.
    """
    t0 = maintenant if maintenant is not None else time.time()
    rapport = {c: {"fichiers": 0, "octets": 0, "jobs": set()}
               for c in _CLASSES}
    rapport["total"] = {"fichiers": 0, "octets": 0}
    rapport["regle"] = {"conservation_jours": conservation_jours,
                        "plancher_jours": PLANCHER_JOURS}
    rapport["erreurs"] = []
    try:
        from app.api.jobs import ACTIVE_STATUSES
    except Exception:                                         # noqa: BLE001
        ACTIVE_STATUSES = {"queued", "transcribing", "planning", "rendering"}
    try:
        entrees = list(dossier.iterdir()) if dossier.exists() else []
    except OSError as exc:
        rapport["erreurs"].append(f"lecture du dossier impossible : {exc}")
        return rapport

    for p in entrees:
        try:
            if not p.is_file():
                continue
            taille = p.stat().st_size
        except OSError as exc:
            rapport["erreurs"].append(f"{p.name} : {exc}")
            continue
        rapport["total"]["fichiers"] += 1
        rapport["total"]["octets"] += taille

        jid = _job_id_de(p.name)
        if jid is None:
            classe = "inconnu"
        else:
            job = jobs.get(jid)
            if job is None:
                classe = "orphelin"
            elif getattr(job, "status", "") in ACTIVE_STATUSES:
                classe = "actif"
            else:
                quand = (getattr(job, "completed_at", None)
                         or getattr(job, "created_at", None) or t0)
                try:
                    age_j = (t0 - float(quand)) / 86400.0
                except (TypeError, ValueError):
                    age_j = 0.0
                if age_j < PLANCHER_JOURS:
                    classe = "recent"
                elif getattr(job, "trashed_at", None) is not None:
                    classe = "corbeille"
                elif age_j >= conservation_jours:
                    classe = "expire"
                else:
                    classe = "recent"
        rapport[classe]["fichiers"] += 1
        rapport[classe]["octets"] += taille
        if jid:
            rapport[classe]["jobs"].add(jid)
    return rapport


def journalise(rapport: dict) -> None:
    """Le rapport, en clair. Dit aussi ce qu'il NE supprimerait pas."""
    try:
        r = rapport.get("regle", {})
        tot = rapport.get("total", {})
        print(f"[NETTOYAGE] inventaire de outputs : "
              f"{tot.get('fichiers', 0)} fichier(s), "
              f"{tot.get('octets', 0) / 1e9:.2f} Go — regle a l'etude : "
              f"conservation {r.get('conservation_jours')} j, plancher "
              f"{r.get('plancher_jours')} j. AUCUNE SUPPRESSION.", flush=True)
        for c in _CLASSES:
            d = rapport.get(c) or {}
            if not d.get("fichiers"):
                continue
            print(f"[NETTOYAGE]   {c:10s} {d['fichiers']:5d} fichier(s)  "
                  f"{d['octets'] / 1e9:6.2f} Go  "
                  f"{len(d.get('jobs') or ()):4d} job(s)", flush=True)
        exp = rapport.get("expire") or {}
        orph = rapport.get("orphelin") or {}
        corb = rapport.get("corbeille") or {}
        gagne = exp.get("octets", 0) + corb.get("octets", 0)
        print(f"[NETTOYAGE] la regle libererait {gagne / 1e9:.2f} Go "
              f"({exp.get('fichiers', 0)} expire(s) + "
              f"{corb.get('fichiers', 0)} en corbeille) et ne toucherait PAS "
              f"les {orph.get('fichiers', 0)} orphelin(s) "
              f"({orph.get('octets', 0) / 1e9:.2f} Go) — ils ont servi a "
              f"reconstruire jobs.json le 20/09", flush=True)
        for e in (rapport.get("erreurs") or [])[:5]:
            print(f"[NETTOYAGE] erreur : {e}", flush=True)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[NETTOYAGE] rapport indisponible ({exc})", flush=True)
