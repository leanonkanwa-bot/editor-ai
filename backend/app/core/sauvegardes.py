"""Instantanes de jobs.json, pour que la prochaine perte ne soit pas un drame.

Le 20/09, jobs.json a ete vide et reconstruit a la main depuis 179 videos
presentes sur le disque. Rien ne sauvegardait ce fichier : il n'existait
qu'un exemplaire, et la seule copie de secours etait le repertoire de
sorties.

Trois regles portent tout le module, et chacune repond a une facon
precise de perdre les donnees malgre des sauvegardes :

  1. L'instantane de demarrage est pris AVANT le triage. `JobStore._load`
     reecrit jobs.json avec le resultat du triage (`_save_locked` en fin
     de `_load`), et `store` est instancie a l'import du module, donc
     avant le hook de demarrage de FastAPI. Une sauvegarde posee dans
     `_on_startup` arriverait apres la reecriture.

  2. La rotation n'evince JAMAIS la reference, c'est-a-dire l'instantane
     portant le plus grand nombre de jobs jamais vu. Un systeme de
     sauvegarde qui copie fidelement un fichier vide, puis fait tourner
     la rotation, remplace vingt bonnes copies par vingt vides. C'est
     exactement le scenario du 20/09.

  3. Un fichier illisible est conserve tel quel, et n'est jamais pris
     pour reference. On ne sait pas ce qu'il contient : on ne le jette
     pas, et on ne s'y fie pas.

Rien ici ne doit jamais lever dans le chemin de demarrage : toute
fonction publique rattrape et journalise.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import time
from pathlib import Path

from app.core.config import settings


DOSSIER = settings._data_root / "sauvegardes"
FICHIER = settings._data_root / "jobs.json"

# gzip niveau 6 : mesure x9,7 sur un export reel de 2,57 Mo en 69 ms. Le
# niveau 9 ne gagne rien (x9,7 aussi) pour 2,5 fois le temps.
NIVEAU = 6
# A ~183 Ko par job (mesure sur 14 jobs reels), 179 jobs font ~33 Mo, soit
# ~3,4 Mo comprime. Vingt instantanes tiennent dans 70 Mo.
GARDE = 20
# Plafond ABSOLU, mais il n'est pas le seul : voir budget(). Un plafond fixe
# serait raisonnable sur un gros volume et deraisonnable sur un petit, et je
# ne peux pas lire la taille du volume Railway sans deployer d'abord quelque
# chose qui la rapporte. On le rend donc relatif a la place reelle.
BUDGET_OCTETS = 250 * 1024 * 1024
# Part maximale de la place TOTALE du volume que les sauvegardes peuvent
# prendre. Le volume porte surtout des videos sources et des sorties : les
# sauvegardes doivent rester une ligne de detail, pas un poste.
PART_DU_VOLUME = 0.02
# Place exigee avant d'ecrire. Proportionnelle a ce qu'on ecrit, pas un
# chiffre rond : un plancher absolu de 1 Go refusait une ecriture de 3,4 Mo
# alors qu'il restait 999 Mo libres — c'est-a-dire qu'il eteignait les
# sauvegardes au moment ou elles comptent le plus. Le facteur 3 laisse la
# place du fichier temporaire, de sa version finale, et une marge.
PLANCHER_LIBRE = 100 * 1024 * 1024
FACTEUR_MARGE = 3
REFERENCE = "jobs-reference.json.gz"
_PREFIXE = "jobs-"
_SUFFIXE = ".json.gz"


def place(chemin: Path | None = None) -> tuple[int, int]:
    """(libre, total) en octets sur le volume, ou (-1, -1) si illisible."""
    try:
        u = shutil.disk_usage(str(chemin or FICHIER.parent))
        return u.free, u.total
    except OSError:
        return -1, -1


def budget() -> int:
    """Le poids maximal des instantanes glissants, adapte au volume.

    Le plus petit des deux : le plafond absolu, et une part de la taille
    totale du volume. Si la place est illisible, on retombe sur le plafond
    absolu — ne pas savoir ne doit pas supprimer la borne.
    """
    _, total = place()
    if total <= 0:
        return BUDGET_OCTETS
    return min(BUDGET_OCTETS, int(total * PART_DU_VOLUME))


def _nombre_de_jobs(octets: bytes) -> int | None:
    """Combien de jobs ce contenu porte, ou None s'il est illisible."""
    try:
        d = json.loads(octets.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return len(d) if isinstance(d, dict) else None


def _n_txt(n: int | None) -> str:
    """« 227 jobs », ou « contenu illisible » quand le compte n'est pas su."""
    return "contenu illisible" if n is None else f"{n} jobs"


def _reference_actuelle() -> tuple[Path | None, int]:
    """(chemin, nombre de jobs) de la reference, ou (None, -1)."""
    p = DOSSIER / REFERENCE
    if not p.exists():
        return None, -1
    try:
        n = _nombre_de_jobs(gzip.decompress(p.read_bytes()))
    except OSError:
        return p, -1
    return p, (n if n is not None else -1)


def _glissants() -> list[Path]:
    """Les instantanes glissants, du plus ancien au plus recent."""
    if not DOSSIER.exists():
        return []
    return sorted(
        (p for p in DOSSIER.glob(f"{_PREFIXE}*{_SUFFIXE}") if p.name != REFERENCE),
        key=lambda p: p.name,
    )


def _rotation() -> int:
    """Supprime les instantanes en trop. Ne touche jamais la reference.

    Deux bornes : le nombre (GARDE) et le poids total (budget(), qui suit
    la taille du volume). Le plus recent n'est jamais supprime, meme s'il
    excede le budget a lui seul — sans quoi un fichier devenu enorme ne
    serait plus sauvegarde du tout.
    """
    retires = 0
    plafond = budget()
    glissants = _glissants()
    while len(glissants) > GARDE:
        glissants.pop(0).unlink(missing_ok=True)
        retires += 1
    while len(glissants) > 1:
        poids = sum(p.stat().st_size for p in glissants if p.exists())
        if poids <= plafond:
            break
        glissants.pop(0).unlink(missing_ok=True)
        retires += 1
    return retires


def instantane(raison: str) -> Path | None:
    """Copie jobs.json dans le dossier de sauvegardes. Ne leve jamais.

    `raison` apparait dans le nom du fichier : demarrage, periodique,
    avant-restauration. Rend le chemin ecrit, ou None.
    """
    try:
        if not FICHIER.exists():
            return None
        brut = FICHIER.read_bytes()
        n = _nombre_de_jobs(brut)
        DOSSIER.mkdir(parents=True, exist_ok=True)

        # Ne pas empiler des copies identiques : le fichier ne change pas
        # entre deux passages periodiques si personne ne rend de video.
        glissants = _glissants()
        if glissants:
            try:
                if gzip.decompress(glissants[-1].read_bytes()) == brut:
                    # Le saut se DIT. Muet, il etait indistinguable d'un
                    # echec : au deploiement de 1226f59 la production etait
                    # au repos, aucune ligne [SAUVEGARDE] n'est sortie, et
                    # rien dans les logs ne permettait de choisir entre
                    # « rien a sauvegarder » et « la sauvegarde est cassee ».
                    # La RAISON et le PID. Sans eux, quatre lignes
                    # « sautee » sont apparues dans une meme fenetre de
                    # logs sans qu'on puisse dire laquelle venait du
                    # chargement du store, du passage periodique ou d'un
                    # autre processus — la ligne ne nommait pas sa source.
                    print(f"[SAUVEGARDE] sautee ({raison}, pid "
                          f"{os.getpid()}) : jobs.json identique au dernier "
                          f"instantane ({_n_txt(n)}, "
                          f"{len(brut) / 1e6:.1f} Mo) — {glissants[-1].name}",
                          flush=True)
                    return None
            except OSError as _exc_lecture:
                # Le dernier instantane est illisible : on ne peut pas
                # comparer, donc on ecrit. Dit, parce que c'est le signe
                # d'un fichier abime dans le dossier de sauvegardes.
                print(f"[SAUVEGARDE] ({raison}, pid {os.getpid()}) dernier "
                      f"instantane illisible "
                      f"({glissants[-1].name}: {_exc_lecture}) — "
                      f"comparaison impossible, on ecrit", flush=True)

        # Les millisecondes font partie du nom : a la seconde pres, deux
        # instantanes du meme passage portaient le meme nom et le second
        # ecrasait le premier. Vingt-cinq appels ne laissaient que trois
        # fichiers, et la rotation devenait illusoire.
        _t = time.time()
        horo = (time.strftime("%Y%m%d-%H%M%S", time.gmtime(_t))
                + f".{int(_t * 1000) % 1000:03d}")

        # On comprime AVANT de decider : la place exigee depend de la
        # taille reelle de ce qu'on s'apprete a ecrire.
        comprime = gzip.compress(brut, NIVEAU)
        libre, total = place()
        exige = max(PLANCHER_LIBRE, len(comprime) * FACTEUR_MARGE)
        if 0 <= libre < exige:
            # Un volume vraiment sature : ecrire pourrait faire echouer un
            # rendu en cours. La reference deja ecrite reste la, et on ne
            # supprime rien.
            print(f"[SAUVEGARDE] sautee ({raison}, pid {os.getpid()}) — "
              f"{libre / 1e6:.0f} Mo libres, "
                  f"{exige / 1e6:.0f} Mo exiges pour une copie de "
                  f"{len(comprime) / 1e6:.1f} Mo", flush=True)
            return None
        etat = "illisible" if n is None else f"{n}jobs"
        cible = DOSSIER / f"{_PREFIXE}{horo}-{raison}-{etat}{_SUFFIXE}"
        tmp = cible.with_suffix(".tmp")
        tmp.write_bytes(comprime)
        tmp.replace(cible)

        # La reference : le plus grand nombre de jobs jamais vu. Un fichier
        # illisible (n is None) ne peut pas devenir reference.
        _, n_ref = _reference_actuelle()
        if n is not None and n > n_ref:
            tmp_r = (DOSSIER / REFERENCE).with_suffix(".tmp")
            tmp_r.write_bytes(comprime)
            tmp_r.replace(DOSSIER / REFERENCE)
            print(f"[SAUVEGARDE] reference portee a {n} jobs", flush=True)

        retires = _rotation()
        # La place est journalisee a chaque passage : c'est la seule facon
        # de connaitre la taille reelle du volume /data sans y deployer une
        # route exprès, et de voir le plafond s'y adapter.
        _occupe = sum(g.stat().st_size for g in _glissants() if g.exists())
        print(
            f"[SAUVEGARDE] (pid {os.getpid()}) {cible.name} — "
            f"{len(brut) / 1e6:.1f} Mo -> "
            f"{len(comprime) / 1e6:.1f} Mo"
            + (f", {retires} ancien(s) retire(s)" if retires else "")
            + f" · {len(_glissants())} copie(s) pour {_occupe / 1e6:.0f} Mo"
            f" / plafond {budget() / 1e6:.0f} Mo"
            + (f" · volume {libre / 1e9:.1f} Go libres sur "
               f"{total / 1e9:.1f} Go" if total > 0 else " · volume inconnu"),
            flush=True,
        )
        return cible
    except Exception as exc:                                  # noqa: BLE001
        print(f"[SAUVEGARDE] echec ({type(exc).__name__}: {exc}) — "
              f"le service continue", flush=True)
        return None


def liste() -> list[dict]:
    """Les sauvegardes disponibles, la plus recente d'abord."""
    out = []
    for p in list(_glissants()) + [DOSSIER / REFERENCE]:
        if not p.exists():
            continue
        try:
            n = _nombre_de_jobs(gzip.decompress(p.read_bytes()))
        except OSError:
            n = None
        out.append({
            "nom": p.name,
            "octets": p.stat().st_size,
            "jobs": n,
            "reference": p.name == REFERENCE,
        })
    out.sort(key=lambda d: (d["reference"], d["nom"]), reverse=True)
    return out


def restaure(nom: str, *, mode: str = "fusion", a_blanc: bool = True) -> dict:
    """Restaure une sauvegarde. A blanc par defaut : rien n'est ecrit.

    mode="fusion"   — union des deux jeux ; a id egal, l'entree dont
                      `created_at` est le plus recent gagne. C'est le mode
                      voulu apres une perte : les jobs crees depuis la
                      sauvegarde ne doivent pas disparaitre a leur tour.
    mode="remplace" — le fichier courant est ecrase par la sauvegarde.

    Rend toujours un rapport. N'ecrit qu'avec a_blanc=False, et prend un
    instantane du fichier courant avant d'ecrire.
    """
    src = DOSSIER / nom
    if not src.exists():
        return {"erreur": f"sauvegarde introuvable : {nom}"}
    if mode not in ("fusion", "remplace"):
        return {"erreur": f"mode inconnu : {mode}"}
    try:
        sauve = json.loads(gzip.decompress(src.read_bytes()).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"erreur": f"sauvegarde illisible : {exc}"}
    if not isinstance(sauve, dict):
        return {"erreur": "la sauvegarde ne porte pas un dictionnaire de jobs"}

    courant: dict = {}
    if FICHIER.exists():
        try:
            d = json.loads(FICHIER.read_text(encoding="utf-8"))
            courant = d if isinstance(d, dict) else {}
        except (OSError, json.JSONDecodeError):
            courant = {}

    if mode == "remplace":
        final = dict(sauve)
    else:
        final = dict(sauve)
        for jid, jd in courant.items():
            garde = final.get(jid)
            if garde is None:
                final[jid] = jd
                continue
            try:
                if float(jd.get("created_at") or 0) >= float(
                        garde.get("created_at") or 0):
                    final[jid] = jd
            except (TypeError, ValueError):
                final[jid] = jd

    rapport = {
        "sauvegarde": nom,
        "mode": mode,
        "jobs_dans_la_sauvegarde": len(sauve),
        "jobs_actuels": len(courant),
        "jobs_apres": len(final),
        "recuperes": len(set(sauve) - set(courant)),
        "perdus": sorted(set(courant) - set(final))[:20],
        "a_blanc": a_blanc,
    }
    if a_blanc:
        return rapport

    instantane("avant-restauration")
    tmp = FICHIER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(final), encoding="utf-8")
    tmp.replace(FICHIER)
    rapport["ecrit"] = str(FICHIER)
    print(f"[SAUVEGARDE] restauration {mode} depuis {nom} : "
          f"{len(courant)} -> {len(final)} jobs", flush=True)
    return rapport
