"""R1 — control the plan BEFORE rendering, in code.

A render costs 45 minutes (measured: 2898 s for a 534 s video, x5.4 real time)
and one of the two render slots. Every defect this pass can name is nameable
from the storyboard, the words and the zoom plan alone, in about 13 seconds and
no model call. Finding it afterwards costs a second render; finding it here
costs nothing.

What it is NOT: a machine for rewriting cards. That was the plan until the
numbers came in. "Cards that repeat the speech word for word" sounded like the
dominant defect; measured on b17a54cc it is 18 of 41, median 7 words, and the
styles are `quote` (6), `key_phrase` (4), `attributed_quote` — quoting the
speaker verbatim is what those styles are FOR. On 37f76379 it is 5 of 27, all
quote/key_phrase, none over 12 words. So being verbatim is reported as a fact,
never repaired.

The only unambiguous repair is length: a card over 12 words does not read at
the size a video-overlay draws, and the text can be shortened to a contiguous
run of the words already there. Measured: 2 such cards on b17a54cc, 2 on
37f76379. Everything else is REPORTED with a named repair for a human to
decide — deleting or demoting a card is visible on screen, and the budget pass
that would do it is not approved.

Never raises: a control that can break a render is worse than no control.
"""
from __future__ import annotations

import os
import re

from app.engine.accent import mot_distinctif, ouvre_mal, table_frequences

# Same bar the safety net uses to decide "is anything moving" — measured from
# breath 0.0706/s, pulse 0.0393/s, drift 0.0055/s.
SEUIL_ZOOM = float(os.environ.get("SAFETY_NET_MIN_ZOOM_SPEED", 0.02))
FENETRE_S = float(os.environ.get("SAFETY_NET_WINDOW_S", 12.0))
MIN_MOTS, MAX_MOTS = 4, 12

# Styles that draw no text at all (compose.py _STYLES_SANS_TEXTE).
STYLES_SANS_TEXTE = frozenset({"prim_anecdote_frame"})

_FORTE = (".", "!", "?", "…")

# The floor ENDSEC-CLAMP already promises (storyboard.py): a card carrying
# several items needs longer to read. Kept in step with that list; a style
# missing here only gets the ordinary 3.0 s floor.
_MULTI_ITEM = frozenset({
    "list", "timeline", "checklist", "pros_cons", "data_bar_chart",
    "carousel", "mindmap", "roadmap_milestone", "tool_stack",
    "revenue_breakdown", "tool_comparison", "decision_matrix",
    "day_in_life_schedule", "milestone_recap", "content_calendar",
    "weekly_review", "ingredient_list", "resource_allocation",
    "platform_stats", "cost_comparison", "broken_promise_tracker",
    "percentage_split", "habit_tracker", "recap_summary", "prim_split_stage",
})


def style_de(carte) -> str:
    h = carte.get("contentHints")
    return str(h.get("style", "")) if isinstance(h, dict) else ""


def _fin(mot) -> str:
    t = str(getattr(mot, "text", mot) or "").rstrip("\"'»)]")
    if t.endswith(_FORTE):
        return "."
    if t.endswith((",", ";", ":")):
        return ","
    return ""


def condense(texte: str, max_mots: int = MAX_MOTS,
             forcer: bool = False) -> str | None:
    """The best contiguous 4-max_mots word run of this text, bounded by punctuation.

    Never a paraphrase: the words are the ones already in the card, in order,
    unbroken. Only the boundaries are chosen. Same ranking as the safety net's
    shortening — both boundaries first, then a full stop over a comma, then a
    distinctive word, then the longest.

    max_mots lets another caller ask for a shorter run without a second copy
    of this ranking: the verse card wants 10 words where a card tolerates 12.
    forcer asks for the run even when the text already fits, which is what
    picking a quotation out of running speech needs.
    """
    mots = str(texte or "").split()
    if len(mots) <= max_mots and not forcer:
        return None
    debuts = [i + 1 for i, w in enumerate(mots[:-1]) if _fin(w)]
    debuts.insert(0, 0)
    best = None
    for d in debuts:
        if d >= len(mots) or ouvre_mal(mots[d]):
            continue
        for f in range(d + MIN_MOTS - 1, min(d + max_mots, len(mots))):
            k = _fin(mots[f])
            if not k:
                continue
            score = (1 if k == "." else 0) + (1 if (d > 0 and _fin(mots[d - 1])) else 0)
            cand = (score, 1 if k == "." else 0, f - d, -d)
            if best is None or cand > best[0]:
                best = (cand, d, f + 1)
    if best is None:
        return None
    return " ".join(mots[best[1]:best[2]])


def _texte_dessine(hints: dict, champs_style: dict | None = None) -> str:
    """The text this card will actually draw.

    Delegates to champs_texte.py so the control, the diagnostic and the accent
    word read the same fields. champs_style (the anchoring map) is still
    accepted and tried as a last resort, so a style listed there but not here
    is not lost.
    """
    from app.engine.champs_texte import texte_affiche
    t = texte_affiche(hints)
    if t:
        return t
    fld = (champs_style or {}).get(hints.get("style", ""), "")
    v = hints.get(fld) if fld else ""
    if isinstance(v, list):
        v = " ".join(str(x) for x in v)
    return str(v or "").strip()


def _intervalles(cartes) -> list[tuple[float, float]]:
    out = []
    for c in cartes:
        try:
            s, e = float(c.get("startSec", 0)), float(c.get("endSec", 0))
        except (TypeError, ValueError):
            continue
        if e > s:
            out.append((s, e))
    return sorted(out)


def _zones_mortes(cartes, zoom, duree) -> list[tuple[float, float]]:
    ivs = _intervalles(cartes)
    rapides = []
    for z in zoom or []:
        try:
            v = z.get("scale_per_second")
            if v is None:
                v = abs(float(z.get("to", 1)) - float(z.get("from", 1))) / max(
                    float(z.get("end", 0)) - float(z.get("start", 0)), 1e-3)
            if float(v) >= SEUIL_ZOOM:
                rapides.append((float(z.get("start", 0)), float(z.get("end", 0))))
        except (TypeError, ValueError):
            continue
    trous, t = [], 0.0
    while t < duree:
        a, b = t, min(t + FENETRE_S, duree)
        t += 0.25
        # A window clamped by the end of the video was never a full uncovered
        # window: without this the last 0.2 s is reported beside a real 27 s one.
        if b - a < FENETRE_S:
            continue
        if any(s < b and e > a for s, e in ivs) or any(s < b and e > a for s, e in rapides):
            continue
        if trous and a <= trous[-1][1]:
            trous[-1] = (trous[-1][0], max(trous[-1][1], b))
        else:
            trous.append((a, b))
    return trous


def controle_du_plan(storyboard: dict, mots, zoom, duree: float,
                     champs_style: dict | None = None,
                     reparer: bool = True) -> dict:
    """Name every defect this plan carries; repair only what is unambiguous.

    Returns a report. Mutates the storyboard ONLY to shorten a text over 12
    words, and only when a contiguous run of its own words fits.
    """
    rapport = {
        "cartes": 0, "zones_mortes": [], "sans_texte": [], "texte_invisible": [],
        "trop_longues": [], "condensees": [], "verbatim": [], "sans_accent": [],
        "plein_cadre": 0, "primitives": [], "ecarts_primitives": [],
        "hors_champ": [],
    }
    try:
        cartes = [c for c in (storyboard.get("cards") or [])
                  if c.get("type") != "caption"]
        rapport["cartes"] = len(cartes)
        freq = table_frequences(mots)
        parole = re.sub(r"\s+", " ", " ".join(
            (w.get("text") if isinstance(w, dict) else getattr(w, "text", ""))
            for w in (mots or []))).strip().casefold()

        for c in cartes:
            cid = str(c.get("id", "?"))
            # A card can be written, paid for and never read. ENDSEC-CLAMP
            # (storyboard.py) promises every card 3.0 s on screen, 3.5 s when
            # it carries several items — but it clamps against the end of the
            # SPEECH SEGMENT and never against the end of the video, so the
            # last card of a clip can fall below the floor the engine itself
            # guarantees. Measured: card-02 of 4e5b4dbf starts at 6.33 s on a
            # 6.1 s clip (never shown, half that plan); card-04 of 694af187 is
            # left 1.13 s of its 3.0 s. Invisible on 534 s, fatal on 6 s.
            try:
                s_, e_ = float(c.get("startSec", 0)), float(c.get("endSec", 0))
                vu = min(e_, duree) - s_
                plancher = 3.5 if style_de(c) in _MULTI_ITEM else 3.0
                # Only what the END OF THE VIDEO causes. A card that is
                # simply short inside the video is another matter and would
                # flood every plan: card-01 of 4656ef5f runs 0.3-3.0 s on a
                # 6.1 s clip, under the floor but not cut by anything. And a
                # truncation that still leaves the floor intact is harmless.
                if duree > 0 and s_ >= duree:
                    rapport["hors_champ"].append(
                        (cid, round(s_, 2), round(e_, 2), 0.0, "jamais affichee"))
                elif duree > 0 and e_ > duree and vu < plancher - 0.01:
                    rapport["hors_champ"].append(
                        (cid, round(s_, 2), round(e_, 2), round(vu, 2),
                         f"coupee par la fin, {vu:.2f}s vus "
                         f"pour un plancher de {plancher:.1f}s"))
            except (TypeError, ValueError):
                pass
            h = c.get("contentHints")
            if not isinstance(h, dict):
                rapport["sans_texte"].append((cid, None))
                continue
            style = h.get("style", "")
            txt = _texte_dessine(h, champs_style)
            if not txt:
                if style in STYLES_SANS_TEXTE:
                    continue        # by design: it shows no text
                # The card may still draw from a field nobody mapped — that is
                # R1b's subject. Reported apart from "really empty".
                autres = [k for k, v in h.items()
                          if k not in ("style", "accent_word") and str(v or "").strip()]
                (rapport["texte_invisible"] if autres
                 else rapport["sans_texte"]).append((cid, style, autres))
                continue
            n = len(txt.split())
            if n > MAX_MOTS:
                court = condense(txt)
                rapport["trop_longues"].append((cid, style, n))
                if reparer and court:
                    # Written back to the field the text came from.
                    if str(h.get("title") or "").strip():
                        h["title"] = court
                    else:
                        fld = (champs_style or {}).get(style, "")
                        if fld:
                            h[fld] = court
                    rapport["condensees"].append((cid, n, len(court.split())))
            if n >= MIN_MOTS and txt.casefold() in parole:
                rapport["verbatim"].append((cid, style, n))
            aw = str(h.get("accent_word") or "").strip()
            if not aw or aw.lower() not in _texte_dessine(h, champs_style).lower():
                m = mot_distinctif(_texte_dessine(h, champs_style), freq)
                rapport["sans_accent"].append((cid, style, m or ""))

        GRANDES = {"fullscreen"}
        rapport["plein_cadre"] = sum(1 for c in cartes if c.get("zone") in GRANDES)
        prims = sorted((c for c in cartes
                        if str((c.get("contentHints") or {}).get("style", ""))
                        .startswith("prim_")),
                       key=lambda c: float(c.get("startSec", 0)))
        rapport["primitives"] = [(str(c.get("id")), (c.get("contentHints") or {}).get("style"),
                                  round(float(c.get("startSec", 0)), 1)) for c in prims]
        rapport["ecarts_primitives"] = [
            round(float(b.get("startSec", 0)) - float(a.get("startSec", 0)), 1)
            for a, b in zip(prims, prims[1:])]
        rapport["zones_mortes"] = _zones_mortes(cartes, zoom, duree)
    except Exception as exc:                        # noqa: BLE001
        rapport["erreur"] = f"{type(exc).__name__}: {exc}"
    return rapport


def journalise(rapport: dict) -> None:
    """One line per defect class, with the repair applied or recommended."""
    try:
        if rapport.get("erreur"):
            print(f"[CONTROLE] interrompu ({rapport['erreur']}) — le rendu continue",
                  flush=True)
            return
        n = rapport.get("cartes", 0)
        print(f"[CONTROLE] {n} carte(s) au plan", flush=True)
        for cid, *reste in rapport.get("sans_texte", []):
            print(f"[CONTROLE] DEFAUT {cid} : aucun texte — reparation : "
                  f"remplacer par un mot en encre ou supprimer (a decider)", flush=True)
        for cid, style, champs in rapport.get("texte_invisible", []):
            print(f"[CONTROLE] DEFAUT {cid} ({style}) : texte present dans "
                  f"{champs} mais pas la ou le diagnostic et l'ancrage le "
                  f"lisent — reparation : mapper le champ", flush=True)
        for cid, avant, apres in rapport.get("condensees", []):
            print(f"[CONTROLE] REPARE {cid} : {avant} mots -> {apres} "
                  f"(suite contigue, jamais une paraphrase)", flush=True)
        restantes = [x for x in rapport.get("trop_longues", [])
                     if x[0] not in {c[0] for c in rapport.get("condensees", [])}]
        for cid, style, nb in restantes:
            print(f"[CONTROLE] DEFAUT {cid} ({style}) : {nb} mots et aucune "
                  f"suite de 4-12 mots bornee par la ponctuation — "
                  f"reparation : mot en encre ou zoom (a decider)", flush=True)
        sa = rapport.get("sans_accent", [])
        trouvables = [x for x in sa if x[2]]
        print(f"[CONTROLE] {len(sa)} carte(s) sans mot d'accent utilisable, "
              f"dont {len(trouvables)} ou un mot existe", flush=True)
        for cid, s_, e_, _vu, quoi in rapport.get("hors_champ", []):
            print(f"[CONTROLE] DEFAUT {cid} : {s_}-{e_}s depasse la video "
                  f"({quoi}) — reparation : recaler ou supprimer (a decider)",
                  flush=True)
        for a, b in rapport.get("zones_mortes", []):
            print(f"[CONTROLE] DEFAUT zone morte {a:.1f}-{b:.1f}s "
                  f"({b-a:.1f}s) — le filet aurait du la couvrir", flush=True)
        print(f"[CONTROLE] verbatim de la parole : {len(rapport.get('verbatim', []))} "
              f"(information, pas un defaut : citer est le metier de quote et "
              f"attributed_quote)", flush=True)
        print(f"[CONTROLE] plein cadre {rapport.get('plein_cadre')}/{n}, "
              f"primitives {len(rapport.get('primitives', []))} "
              f"ecarts {rapport.get('ecarts_primitives')} "
              f"(budget non applique — R2 non valide)", flush=True)
    except Exception:
        pass
