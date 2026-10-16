"""Un echec de rendu ne doit pas passer inapercu.

Avant ce module, le chemin d'echec de la phase 2 (`pipeline.py`, le
`except Exception` qui pose status="error") n'ecrivait PAS une ligne. Il
posait l'etat et l'exception dans le champ du job, et c'est tout : aucune
trace dans les logs, donc rien sur quoi une alerte puisse se brancher.
Trois autres chemins etaient muets de la meme facon.

Ce module ne fait qu'une chose pour l'instant : la ligne. L'envoi d'un
e-mail et le regroupement viendront dans leurs propres commits.

Regle de contenu, qui ne bougera pas : **l'identifiant et la cause,
jamais le contenu**. Pas de transcription, pas de titre de video, pas de
nom de fichier source, pas d'adresse. Un message d'alerte part vers un
service tiers ; il ne doit rien emporter d'un client.
"""

from __future__ import annotations

import os

# Longueur maximale du message d'exception reporte. Une trace complete
# dans une ligne de log la rend illisible, et le champ `error` du job la
# porte deja en entier.
MAX_CAUSE = 200


def cause_courte(exc: BaseException | str | None) -> str:
    """« TypeName: premiere ligne du message », tronque.

    La premiere ligne seulement : les traces multilignes noient la ligne
    de log, et c'est le type plus le premier message qui identifient la
    panne.
    """
    if exc is None:
        return "cause inconnue"
    if isinstance(exc, str):
        txt = exc.strip()
        nom = ""
    else:
        nom = type(exc).__name__
        txt = str(exc).strip()
    premiere = txt.splitlines()[0] if txt else ""
    plein = f"{nom}: {premiere}" if nom else premiere
    plein = plein.strip() or (nom or "cause inconnue")
    return plein[:MAX_CAUSE] + ("…" if len(plein) > MAX_CAUSE else "")


# Combien de fois chaque cause est arrivee depuis le demarrage. Compte en
# memoire, remis a zero a chaque redemarrage : il sert a donner un ORDRE DE
# GRANDEUR dans les logs, pas une comptabilite. KAN decidera des reprises
# avec ces chiffres.
_compte: dict[str, int] = {}


def _cle(etape: str, exc: BaseException | str | None) -> str:
    """L'etape et le TYPE, sans le message : deux pannes identiques dont le
    message differe par un chemin de fichier doivent compter ensemble."""
    nom = type(exc).__name__ if isinstance(exc, BaseException) else "str"
    return f"{etape}/{nom}"


def reprise(job_id: str, etape: str, essai: int, total: int,
            cause: BaseException | str | None) -> None:
    """Une tentative de plus. Dite, jamais alertee.

    Une reprise qui reussit ne doit pas reveiller qui que ce soit ; une
    reprise qui echoue finira par appeler echec_rendu.
    """
    try:
        print(f"[REPRISE] job {str(job_id)[:8]} etape={etape} "
              f"tentative {essai}/{total} cause={cause_courte(cause)}",
              flush=True)
    except Exception:                                         # noqa: BLE001
        pass


def echec_rendu(job_id: str, etape: str, exc: BaseException | str | None,
                *, definitif: bool = True) -> None:
    """La ligne d'alerte. Ne leve jamais, ne ralentit rien.

    `etape` nomme l'endroit, avec les memes noms que les lignes [TIMING] :
    transcription, planning, render, llm_editorial, etc. Sans cela, « un
    rendu a echoue » n'aide personne a chercher.

    `definitif=False` sert aux echecs qui seront repris : ils sont dits,
    mais ne constituent pas encore une alerte.
    """
    try:
        etiquette = "[ALERTE] rendu echoue" if definitif else "[ALERTE] echec repris"
        k = _cle(etape, exc)
        _compte[k] = _compte.get(k, 0) + 1
        print(f"{etiquette} — job {str(job_id)[:8]} etape={etape} "
              f"cause={cause_courte(exc)} pid={os.getpid()} "
              f"occurrence={_compte[k]} depuis le demarrage", flush=True)
    except Exception:                                         # noqa: BLE001
        # Une alerte qui casse le rendu serait pire que pas d'alerte.
        pass
