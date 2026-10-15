"""Aucune carte ne depasse la fin de la video.

Mesure sur l'export du 11/10 : cinq jobs sur treize portaient une carte qui
depasse la duree, et les deux seuls jobs longs n'en portaient aucune. Le
pire cas, 4e5b4dbf (6,08 s), avait une carte planifiee de 6,33 a 9,33 s —
jamais affichee, soit une carte sur deux perdue.

La cause est en amont, dans la passe TITLE-ANCHOR : elle repousse une carte
derriere la precedente (`_prev_end + 0,05`) puis lui reapplique un plancher
de 3,0 s, sans borne haute. 6,28 + 0,05 = 6,33, puis 6,33 + 3,0 = 9,33, a
l'unite pres. C'est corrige a la source ; ce module est la garde en aval,
parce qu'une seule des deux ne suffit pas : d'autres passes deplacent des
cartes, et aucune ne revalide la duree.

Pourquoi compose ne rattrapait rien : il n'ecrete que les CHEVAUCHEMENTS,
jamais la duree. Le storyboard compose portait les memes temps que le
diagnostic livre.

Lifted dans son propre module pour etre rejouable hors ligne sur les
donnees d'un vrai job, sans appeler le planner.
"""

from __future__ import annotations

# Temps visible minimum pour qu'une carte serve a quelque chose. En dessous,
# la raccourcir ne la sauve pas : elle clignote.
MIN_VU_S = 1.5
# Ce qu'on cherche a rendre a une carte qu'on deplace.
CIBLE_DEPLACEE_S = 3.0


def _plafond(duree: float) -> float:
    """La fin de video, arrondie VERS LE BAS au millieme.

    round() arrondit au plus proche : sur une video de 6,082993 s il rendait
    6,083, soit 7 microsecondes APRES la fin. La carte « recalee » depassait
    encore, et le test le voyait. Un plafond doit arrondir vers le bas.
    """
    return int(duree * 1000) / 1000.0


def _bornes(carte) -> tuple[float, float] | None:
    try:
        return float(carte["startSec"]), float(carte["endSec"])
    except (KeyError, TypeError, ValueError):
        return None


def cale_dans_la_duree(cartes: list[dict], duree: float) -> dict:
    """Ramene chaque carte dans [0, duree]. Rend un rapport, ne leve jamais.

    Dans cet ordre, parce qu'il va du moins destructif au plus :
      1. raccourcie — la carte commence dans la video et garde MIN_VU_S ;
      2. deplacee   — elle commence trop tard, mais un creneau libre existe
                      avant la fin ;
      3. retiree    — ni l'un ni l'autre. Mieux vaut une carte en moins
                      qu'une carte payee et jamais affichee.

    Mute `cartes` en place (retire les cartes a retirer).
    """
    rapport = {"raccourcies": [], "deplacees": [], "retirees": [],
               "intactes": 0, "duree": duree}
    if not cartes or duree <= 0:
        return rapport

    tri = sorted(cartes, key=lambda c: float(c.get("startSec", 0) or 0))
    gardees: list[tuple[float, float]] = []
    a_retirer: list[int] = []

    def libre(s: float, e: float) -> bool:
        return not any(gs < e and ge > s for gs, ge in gardees)

    for carte in tri:
        b = _bornes(carte)
        if b is None:
            rapport["intactes"] += 1
            continue
        s, e = b
        cid = str(carte.get("id", "?"))

        if e <= duree + 1e-6:
            rapport["intactes"] += 1
            gardees.append((s, e))
            continue

        # 1. raccourcir
        if s <= duree - MIN_VU_S:
            carte["endSec"] = _plafond(duree)
            rapport["raccourcies"].append((cid, round(e, 3),
                                           carte["endSec"]))
            gardees.append((s, carte["endSec"]))
            continue

        # 2. deplacer : on lui rend sa duree d'origine si elle tient, sinon
        # ce qui reste avant la fin.
        voulu = min(e - s, CIBLE_DEPLACEE_S) if e > s else CIBLE_DEPLACEE_S
        voulu = max(voulu, MIN_VU_S)
        ne = _plafond(duree)
        ns = round(max(0.0, ne - voulu), 3)
        if ne - ns >= MIN_VU_S and libre(ns, ne):
            carte["startSec"], carte["endSec"] = ns, ne
            rapport["deplacees"].append((cid, round(s, 3), ns))
            gardees.append((ns, ne))
            continue

        # 3. retirer
        rapport["retirees"].append((cid, round(s, 3), round(e, 3)))
        a_retirer.append(id(carte))

    if a_retirer:
        restantes = [c for c in cartes if id(c) not in a_retirer]
        cartes[:] = restantes
    return rapport


def journalise(rapport: dict) -> None:
    """Une ligne par action, et une seule ligne quand il n'y a rien a dire."""
    try:
        duree = rapport.get("duree", 0.0)
        for cid, avant, apres in rapport.get("raccourcies", []):
            print(f"[FIN-VIDEO] {cid} raccourcie {avant:.2f}s -> {apres:.2f}s "
                  f"(fin de video {duree:.2f}s)", flush=True)
        for cid, avant, apres in rapport.get("deplacees", []):
            print(f"[FIN-VIDEO] {cid} deplacee {avant:.2f}s -> {apres:.2f}s "
                  f"— elle commencait apres la fin ({duree:.2f}s)", flush=True)
        for cid, s, e in rapport.get("retirees", []):
            print(f"[FIN-VIDEO] {cid} RETIREE {s:.2f}-{e:.2f}s — hors de la "
                  f"video ({duree:.2f}s) et aucun creneau libre avant la fin",
                  flush=True)
        n = (len(rapport.get("raccourcies", []))
             + len(rapport.get("deplacees", []))
             + len(rapport.get("retirees", [])))
        if n == 0:
            print(f"[FIN-VIDEO] {rapport.get('intactes', 0)} carte(s), "
                  f"aucune ne depasse {duree:.2f}s", flush=True)
        else:
            print(f"[FIN-VIDEO] {n} carte(s) recalee(s) : "
                  f"{len(rapport.get('raccourcies', []))} raccourcie(s), "
                  f"{len(rapport.get('deplacees', []))} deplacee(s), "
                  f"{len(rapport.get('retirees', []))} retiree(s)", flush=True)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[FIN-VIDEO] journal indisponible ({exc})", flush=True)
