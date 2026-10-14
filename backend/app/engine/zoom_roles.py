"""R4 — des zooms qui ont un role, comme un monteur en donne un.

Le plan de zoom produisait 102 entrees sur 533 s dont 20 `hold` a
amplitude nulle, et seulement 47 passaient le critere de vitesse. Six
sortes de mouvement cohabitaient sans qu'aucune ne dise POURQUOI elle est
la. Ici il n'y en a que trois, et chacune repond a une intention :

  punch  — une frappe courte sur un mot d'emphase ou une chute
  pousse — une poussee lente pendant une explication
  large  — un retour au plan large a un changement de sujet

Deux grandeurs nommees SEPAREMENT, parce qu'elles ne repondent pas a la
meme question :

  vitesse (scale/s) — « l'image bouge-t-elle maintenant ? »
  course  (amplitude) — « le cadre a-t-il change sur la fenetre ? »

Un seul seuil melangeait les deux et mentait dans les deux sens. Mesure
sur b17a54cc : 47 entrees sur 102 passaient le critere de vitesse mais 75
celui de course, et 26 ne passaient ni l'un ni l'autre.
"""
from __future__ import annotations

import os
import re
from bisect import bisect_left

from app.engine.accent import cle

# ── Les trois roles ────────────────────────────────────────────────────────
# Les valeurs partent de ce que le moteur produisait DEJA et qui marchait :
# punch_in 0,20 s a 0,28/s, drift 9,70 s a 0,0046/s. Seule la duree du punch
# est allongee — a 0,20 s il echappe autant a la perception d'un spectateur
# qu'a celle d'une mesure. C'est une hypothese, pas une mesure.
ROLES: dict[str, dict[str, float | str]] = {
    "punch":  {"duree": 0.45, "course": 0.075},
    "pousse": {"duree": 8.00, "course": 0.060},
    "large":  {"duree": 2.00, "course": 0.070},
}
for _r in ROLES.values():
    _r["vitesse"] = float(_r["course"]) / float(_r["duree"])

V_MIN = 0.02          # vitesse : l'image bouge maintenant
C_MIN = 0.040         # course  : le cadre a change sur la fenetre
FENETRE_VIDE = 12.0   # au-dela, une fenetre sans rien est une zone morte

ECART_PUNCH = 25.0    # hypothese : deux frappes trop proches ne frappent plus
PAUSE_MIN = 0.45      # s — mesure : 9 instants sur b17a54cc
RESPIRE = 1.0         # s entre deux entrees posees dans un meme trou

# Mots qui signalent une insistance. Table courte et assumee : elle vaut
# pour le francais parle, et une autre langue demandera la sienne.
INSISTANCE = frozenset({
    "jamais", "toujours", "vraiment", "surtout", "exactement", "absolument",
    "totalement", "enorme", "immense", "incroyable", "attention", "ecoute",
    "regarde", "comprends", "crois", "ecoutez", "regardez",
})
_FORTE = (".", "!", "?", "…")

# La fin de phrase n'EST PAS une emphase. Mesure : 144 sur 533 s, une
# toutes les 3,7 s. S'en servir pour declencher un punch donnait 42 punchs,
# un toutes les 12 s — un monteur n'appuie pas a chaque point. Elle ne sert
# qu'a placer un retour au plan large.
SIGNAUX_FORTS = ("insistance", "apres-pause", "rupture-de-debit")


def actif() -> bool:
    """ZOOM_ROLES coupe la passe sans renvoyer de code."""
    return str(os.environ.get("ZOOM_ROLES", "1")).strip().lower() \
        not in ("0", "false", "no", "off", "")


def _txt(mot) -> str:
    if isinstance(mot, dict):
        return str(mot.get("text") or "")
    return str(getattr(mot, "text", "") or "")


def _t(mot, champ: str) -> float:
    try:
        v = mot.get(champ) if isinstance(mot, dict) else getattr(mot, champ, 0.0)
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _fin_de_phrase(mot) -> bool:
    return _txt(mot).rstrip("\"'»)]").endswith(_FORTE)


def emphases(mots, energie=None) -> list[tuple[float, str]]:
    """Chaque instant d'emphase, avec le signal qui l'a designe.

    Tous ces signaux sont deja calcules ou lisibles sans rien ajouter au
    pipeline. Un seul manque a l'appel : rms_db du profil d'energie vaut
    -60,0 dB sur ses 164 entrees — une seule valeur distincte, il ne mesure
    rien. `level`, qui en derive, ne sort jamais HIGH. speech_rate, lui,
    vit : 0,3 a 6,7, mediane 4,0.
    """
    out: list[tuple[float, str]] = []
    liste = list(mots or [])

    for a, b in zip(liste, liste[1:]):
        if _t(b, "start") - _t(a, "end") >= PAUSE_MIN:
            out.append((_t(b, "start"), "apres-pause"))

    for m in liste:
        k = cle(_txt(m))
        if k in INSISTANCE:
            out.append((_t(m, "start"), "insistance"))
        if _fin_de_phrase(m):
            out.append((_t(m, "end"), "fin-de-phrase"))

    niveaux = [e for e in (energie or []) if isinstance(e, dict)]
    if niveaux:
        taux = sorted(float(e.get("speech_rate") or 0) for e in niveaux)
        med = taux[len(taux) // 2] if taux else 0.0
        for a, b in zip(niveaux, niveaux[1:]):
            r1 = float(a.get("speech_rate") or 0)
            r2 = float(b.get("speech_rate") or 0)
            if med > 0 and r1 >= med * 1.15 and r2 <= med * 0.6:
                out.append((float(b.get("at", 0)), "rupture-de-debit"))

    return sorted(out)


def _bornes_cartes(cartes) -> list[tuple[float, float]]:
    out = []
    for c in cartes or []:
        if c.get("type") == "caption":
            continue
        try:
            s, e = float(c.get("startSec", 0)), float(c.get("endSec", 0))
        except (TypeError, ValueError):
            continue
        if e > s:
            out.append((s, e))
    return sorted(out)


def zones_vides(cartes, zoom, duree: float) -> list[tuple[float, float]]:
    """Fenetres de FENETRE_VIDE sans carte ET sans mouvement perceptible."""
    ivs = _bornes_cartes(cartes)
    bouge = []
    for z in zoom or []:
        try:
            v = abs(float(z.get("scale_per_second") or 0))
            c = abs(float(z.get("amplitude") or 0))
        except (TypeError, ValueError):
            continue
        if v >= V_MIN or c >= C_MIN:
            bouge.append((float(z.get("start", 0)), float(z.get("end", 0))))
    trous: list[tuple[float, float]] = []
    t = 0.0
    while t < duree:
        a, b = t, min(t + FENETRE_VIDE, duree)
        t += 0.25
        if b - a < FENETRE_VIDE:
            continue
        if any(s < b and e > a for s, e in ivs):
            continue
        if any(s < b and e > a for s, e in bouge):
            continue
        if trous and a <= trous[-1][1]:
            trous[-1] = (trous[-1][0], max(trous[-1][1], b))
        else:
            trous.append((a, b))
    return trous


def plan_par_roles(mots, cartes, duree: float, energie=None) -> list[dict]:
    """Le plan de zoom, un role par entree. Ne rend que des entrees reelles.

    Trois regles, dans cet ordre :
      * jamais de punch sous une carte — il deplace le cadre sous une carte
        posee en coordonnees fixes, et la carte parait sauter alors qu'elle
        n'a pas bouge. Sous une carte on pousse lentement, c'est invisible
        relativement a elle ;
      * on ne pose jamais deux entrees qui se chevauchent ;
      * on repasse sur les trous APRES les emphases, pas seulement avant :
        la premiere version laissait une zone morte de 13,5 s parce qu'elle
        ne revenait pas sur ce qu'elle avait laisse.
    """
    liste = list(mots or [])
    if not liste or duree <= 0:
        return []
    debuts = [_t(m, "start") for m in liste]
    ivs = _bornes_cartes(cartes)

    def sous_carte(t: float) -> bool:
        return any(s <= t <= e for s, e in ivs)

    def cale_sur_mot(t: float, borne: tuple[float, float] | None = None) -> float:
        """Un zoom commence sur un mot, pas sur un temps rond.

        Un mouvement qui demarre entre deux mots se voit comme un defaut ;
        cale sur l'attaque d'un mot, il se lit comme une intention.

        `borne` restreint le calage a un intervalle. Sans elle, combler un
        trou devenait impossible : le trou 139,2-152,8 de b17a54cc ne
        contient AUCUN mot, et celui de 117-138 en a dix mais si espaces
        que le mot le plus proche de 117,5 est a 109,18 — le calage
        ejectait l'entree hors du trou qu'elle devait combler. Dans un
        silence il n'y a rien a caler : on garde le temps demande.
        """
        if borne is not None:
            a, b = borne
            dedans = [d for d in debuts if a <= d <= b]
            if not dedans:
                return t
            return min(dedans, key=lambda d: abs(d - t))
        i = bisect_left(debuts, t)
        cands = [x for x in (i - 1, i, i + 1) if 0 <= x < len(debuts)]
        if not cands:
            return t
        return min((debuts[x] for x in cands), key=lambda d: abs(d - t))

    poses: list[tuple[float, float, str, str]] = []

    def libre(a: float, b: float) -> bool:
        return not any(x < b - 0.001 and y > a + 0.001 for x, y, _, _ in poses)

    def pose(t: float, role: str, source: str,
             borne: tuple[float, float] | None = None) -> bool:
        t = cale_sur_mot(t, borne)
        du = float(ROLES[role]["duree"])
        if t < 0.2 or t + du > duree - 0.05 or not libre(t, t + du):
            return False
        poses.append((t, t + du, role, source))
        return True

    # ── 1. les emphases ────────────────────────────────────────────────────
    sig = emphases(liste, energie)
    dernier_punch = -1e9
    for t, nom in sorted(sig, key=lambda x: (x[1] not in SIGNAUX_FORTS, x[0])):
        if nom in SIGNAUX_FORTS and not sous_carte(t) \
                and t - dernier_punch >= ECART_PUNCH:
            role = "punch"
        elif sous_carte(t):
            role = "pousse"
        else:
            role = "large"
        if pose(t, role, nom) and role == "punch":
            dernier_punch = t

    # ── 2. les trous, APRES les emphases ───────────────────────────────────
    # Deux passes : la seconde rattrape ce que la premiere a laisse une fois
    # les emphases posees. Sans elle il restait une zone morte de 13,5 s.
    for _ in range(2):
        zoom_actuel = [{"start": a, "end": b,
                        "amplitude": ROLES[r]["course"],
                        "scale_per_second": ROLES[r]["vitesse"]}
                       for a, b, r, _s in poses]
        trous = zones_vides(cartes, zoom_actuel, duree)
        if not trous:
            break
        for a, b in trous:
            t = a + 0.3
            # Alterner DANS le trou : deux combleurs de suite du meme role
            # etaient la source de la plupart des repetitions, et les
            # reparer apres coup echouait faute de place.
            precedent = None
            # le role du voisin immediat avant le trou, pour ne pas le
            # repeter non plus
            avant_trou = [x for x in poses if x[1] <= a + 0.01]
            if avant_trou:
                precedent = max(avant_trou, key=lambda x: x[1])[2]
            while t < b:
                assez = (b - t) >= float(ROLES["pousse"]["duree"])
                role = "pousse" if assez else "large"
                if role == precedent:
                    role = "large" if role == "pousse" else "pousse"
                    if role == "pousse" and not assez:
                        role = "punch" if not sous_carte(t) else "large"
                if pose(t, role, "trou", borne=(a, b)):
                    precedent = role
                    t += float(ROLES[role]["duree"]) + RESPIRE
                else:
                    t += 1.0

    # ── 3. l'alternance, sur l'ordre CHRONOLOGIQUE ─────────────────────────
    # KAN accepte les repetitions residuelles : avec trois roles et une
    # frise dense, l'alternance stricte n'est pas toujours satisfiable.
    # Deux passes : avec trois roles et des voisins contraints des deux
    # cotes, une seule laissait six repetitions au lieu des deux que KAN
    # accepte.
    poses.sort()
    for _tour in range(2):
      for i in range(1, len(poses)):
        a, b, r, src = poses[i]
        prec = poses[i - 1][2]
        suiv = poses[i + 1][2] if i + 1 < len(poses) else None
        if r != prec:
            continue
        # Par duree croissante : un remplacant court trouve une place la
        # ou un long se heurte a l'entree suivante. L'ordre inverse
        # laissait cinq repetitions au lieu de deux.
        for cand in ("punch", "large", "pousse"):
            if cand in (prec, suiv):
                continue
            if cand == "punch" and sous_carte(a):
                continue
            du = float(ROLES[cand]["duree"])
            if a + du > duree - 0.05:
                continue
            if any(x < a + du - 0.001 and y > a + 0.001
                   for k, (x, y, _r2, _s2) in enumerate(poses) if k != i):
                continue
            poses[i] = (a, a + du, cand, src)
            break

    poses.sort()
    return [{"start": round(a, 3), "end": round(b, 3), "kind": r,
             "amplitude": float(ROLES[r]["course"]),
             "scale_per_second": float(ROLES[r]["vitesse"]),
             "role_source": src}
            for a, b, r, src in poses]


def journalise(avant: list, apres: list, cartes, duree: float) -> None:
    """Une ligne par classe, et les deux comptages separes."""
    try:
        from collections import Counter

        def perceptibles(z):
            nv = sum(1 for x in z
                     if abs(float(x.get("scale_per_second") or 0)) >= V_MIN)
            nc = sum(1 for x in z
                     if abs(float(x.get("amplitude") or 0)) >= C_MIN)
            return nv, nc

        va, ca = perceptibles(avant)
        vb, cb = perceptibles(apres)
        ta = zones_vides(cartes, avant, duree)
        tb = zones_vides(cartes, apres, duree)
        mix = ", ".join(f"{k}x{v}" for k, v in
                        Counter(x.get("kind") for x in apres).most_common())
        print(f"[ZOOM-ROLES] {len(avant)} entrees -> {len(apres)} ({mix})",
              flush=True)
        print(f"[ZOOM-ROLES] vitesse >= {V_MIN}/s : {va} -> {vb}   "
              f"course >= {C_MIN} : {ca} -> {cb}", flush=True)
        print(f"[ZOOM-ROLES] zones >= {FENETRE_VIDE:.0f}s sans carte ni "
              f"mouvement : {len(ta)} -> {len(tb)}", flush=True)
        for a, b in tb:
            print(f"[ZOOM-ROLES] DEFAUT zone vide {a:.1f}-{b:.1f}s "
                  f"({b-a:.1f}s) — non comblee", flush=True)
    except Exception as exc:                                # noqa: BLE001
        print(f"[ZOOM-ROLES] journal indisponible ({exc})", flush=True)
