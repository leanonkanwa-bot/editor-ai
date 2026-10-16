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
import time

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

# ── L'envoi par e-mail ─────────────────────────────────────────────────────
# Trois etats a tenir, et aucun ne doit grandir sans fin :
#   _notifies   les jobs deja signales — au plus un message par job ;
#   _recents    les echecs de la derniere heure, pour le garde-fou ;
#   _groupe_le  l'instant du dernier message groupe.
FENETRE_GROUPE_S = 3600.0
SEUIL_GROUPE = 3
_notifies: set[str] = set()
_recents: list[tuple[float, str, str]] = []
_groupe_le: float = 0.0
_dit_sans_adresse = False


def _adresse() -> str:
    return os.environ.get("ALERT_EMAIL", "").strip()


def _purge(maintenant: float) -> None:
    """Ne garde que la derniere heure, et borne la memoire.

    Sans la borne, une panne systemique ferait grandir la liste autant que
    le nombre d'echecs — un module d'alerte ne doit pas devenir une fuite.
    """
    global _recents
    _recents = [x for x in _recents
                if maintenant - x[0] <= FENETRE_GROUPE_S][-200:]
    if len(_notifies) > 500:
        _notifies.clear()


def _corps(lignes: list[str]) -> str:
    """Le corps du message. Identifiant et cause, rien d'autre."""
    items = "".join(f"<li>{job} — {cause}</li>" for job, cause in
                    [(l.split(" || ")[0], l.split(" || ")[1]) for l in lignes])
    return (f"<p>Rendu(s) en echec sur leanretention.com :</p>"
            f"<ul>{items}</ul>"
            f"<p>Identifiant et cause seulement. Le detail est dans les "
            f"logs et dans le champ <code>error</code> du job.</p>")


def _envoie(sujet: str, lignes: list[str]) -> None:
    """Delegue a emails.py, qui envoie dans un fil demon. Ne leve jamais.

    emails._send rend la main tout de suite : le rendu n'attend pas, et
    une panne de Resend reste dans ce fil-la.
    """
    try:
        from app import emails as _em
        adr = _adresse()
        # emails.py retourne EN SILENCE quand EMAILS_ENABLED est faux ou
        # quand la cle manque. Pour une alerte, croire qu'elle part alors
        # qu'elle ne part pas est le pire des cas : on le dit.
        if not _em._enabled() or not _em._api_key():
            print(f"[ALERTE] e-mail NON envoye a {adr} — "
                  f"EMAILS_ENABLED={os.environ.get('EMAILS_ENABLED', '')!r} "
                  f"cle_resend={'oui' if _em._api_key() else 'non'}",
                  flush=True)
            return
        _em._send(adr, sujet, _em._shell(_corps(lignes)))
        print(f"[ALERTE] e-mail remis au fil d'envoi ({len(lignes)} echec(s))",
              flush=True)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[ALERTE] envoi impossible ({type(exc).__name__}: {exc}) — "
              f"le rendu n'est pas affecte", flush=True)


def notifie(job_id: str, etape: str, cause: str) -> None:
    """Decide s'il faut un message, et lequel. Ne leve jamais.

    Appelee depuis echec_rendu, donc deja sous son try/except ; le sien est
    la pour que la decision elle-meme ne puisse rien casser.
    """
    global _groupe_le, _dit_sans_adresse
    try:
        court = str(job_id)[:8]
        adr = _adresse()
        if not adr:
            if not _dit_sans_adresse:
                _dit_sans_adresse = True
                print("[ALERTE] ALERT_EMAIL n'est pas definie — aucune "
                      "alerte par e-mail ne sera envoyee (dit une seule "
                      "fois)", flush=True)
            return
        maintenant = time.time()
        _purge(maintenant)
        if court in _notifies:
            print(f"[ALERTE] job {court} deja signale — pas de second "
                  f"message", flush=True)
            return
        _recents.append((maintenant, court, cause))
        if len(_recents) >= SEUIL_GROUPE:
            # Au-dela du seuil, un seul message par fenetre : une panne
            # systemique enverrait sinon un e-mail par job.
            if maintenant - _groupe_le < FENETRE_GROUPE_S:
                print(f"[ALERTE] {len(_recents)} echecs dans l'heure — "
                      f"groupes, message deja envoye il y a "
                      f"{(maintenant - _groupe_le) / 60:.0f} min",
                      flush=True)
                _notifies.add(court)
                return
            _groupe_le = maintenant
            lot = [f"{j} || {c}" for _t, j, c in _recents]
            _notifies.update(j for _t, j, _c in _recents)
            _envoie(f"[LeanRetention] {len(lot)} rendus en echec", lot)
            return
        _notifies.add(court)
        _envoie(f"[LeanRetention] rendu en echec — {court}",
                [f"{court} || {cause}"])
    except Exception as exc:                                  # noqa: BLE001
        print(f"[ALERTE] notification impossible ({exc})", flush=True)


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
        _cause = cause_courte(exc)
        print(f"{etiquette} — job {str(job_id)[:8]} etape={etape} "
              f"cause={_cause} pid={os.getpid()} "
              f"occurrence={_compte[k]} depuis le demarrage", flush=True)
        if definitif:
            notifie(job_id, etape, _cause)
    except Exception:                                         # noqa: BLE001
        # Une alerte qui casse le rendu serait pire que pas d'alerte.
        pass
