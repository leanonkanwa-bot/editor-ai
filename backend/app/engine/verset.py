"""R9 — reconnaitre une annonce de reference et la dessiner.

Mesure sur b17a54cc (1892 mots, 533,7 s) : le locuteur annonce huit fois une
reference et **cinq de ces huit annonces ne portent aucune carte**. La ou le
planner en pose une, il ecrit deja la bonne chose — « Levitique 26:44 »,
« 1 Rois 9:6 », en corrigeant lui-meme ce que la transcription a mal entendu.
Ce qui manque n'est donc pas le savoir-faire, c'est la regularite.

Deux choix viennent des donnees, pas du raisonnement :

  * **Pas de liste de noms de livres comme declencheur.** La transcription
    ecrit « 1 roi », « Hebreu 12 » et « Le roman 8 » pour Romains : les 59
    noms de livres n'attrapent que 2 cas sur 9, alors que le mot « verset »
    ou « chapitre » suivi d'un nombre les attrape 9 sur 9, sans un seul faux
    positif.

  * **La fusion se fait en distance de MOTS, pas en temps.** « chapitre 9,
    Verset 6 » sont deux mots voisins et forment une seule annonce, mais
    leurs horodatages sont a 30 s l'un de l'autre dans le relevé. Fusionner
    sur le temps aurait coupé cette annonce en deux.

Le nom du livre n'est normalise que lorsque la table le connait. « roman »
n'y est pas : il est affiche tel qu'il a ete dit, parce qu'inventer
« Romains » a partir de « roman » serait deviner une ecriture a la place du
locuteur.
"""
from __future__ import annotations

import re
import unicodedata

# ── le declencheur ───────────────────────────────────────────────────────────
# Mesure : 9 occurrences sur 1892 mots, 0 faux positif.
_DECLENCHEUR = re.compile(r"(?i)^(versets?|chapitres?)$")
_NOMBRE = re.compile(r"^(\d{1,3})")

# Deux hits separes par au plus ce nombre de mots appartiennent a la meme
# annonce. Mesure : « chapitre 9, Verset 6 » sont a 2 mots d'ecart.
FUSION_MOTS = 6

# Le livre se cherche dans les mots qui precedent le numero de chapitre, et
# s'arrete des qu'on tombe sur un de ces mots : ils ne font jamais partie
# d'un nom de livre.
_ARRET = frozenset({
    "le", "la", "les", "l", "un", "une", "des", "du", "de", "d", "dans",
    "et", "ou", "que", "qui", "quoi", "ce", "cette", "ces", "son", "sa",
    "ses", "mon", "ma", "mes", "ton", "ta", "tes", "notre", "votre", "leur",
    "a", "au", "aux", "sur", "sous", "avec", "pour", "par", "selon", "chez",
    "dit", "disait", "ecrit", "lit", "lire", "regarde", "regardez", "voir",
    "bible", "parole", "texte", "passage", "verset", "versets", "chapitre",
    "chapitres", "the", "in", "of", "and", "says", "said", "read", "look",
})

# Le nom du livre, normalise UNIQUEMENT quand la table le connait. Les cles
# sont comparees sans accent et sans casse ; les formes au singulier sont la
# parce que la transcription les produit (« 1 roi », « Hebreu 12 »).
_LIVRES: dict[str, str] = {}


def _sans_accent(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t or ""))
                   if unicodedata.category(c) != "Mn").casefold()


def _enregistre(canonique: str, *variantes: str) -> None:
    _LIVRES[_sans_accent(canonique)] = canonique
    for v in variantes:
        _LIVRES[_sans_accent(v)] = canonique


for _c, _v in [
    ("Genèse", ("genese",)), ("Exode", ()), ("Lévitique", ("levitique",)),
    ("Nombres", ("nombre",)), ("Deutéronome", ("deuteronome",)),
    ("Josué", ("josue",)), ("Juges", ("juge",)), ("Ruth", ()),
    ("Samuel", ()), ("Rois", ("roi",)), ("Chroniques", ("chronique",)),
    ("Esdras", ()), ("Néhémie", ("nehemie",)), ("Esther", ()), ("Job", ()),
    ("Psaumes", ("psaume",)), ("Proverbes", ("proverbe",)),
    ("Ecclésiaste", ("ecclesiaste",)), ("Ésaïe", ("esaie", "isaie")),
    ("Jérémie", ("jeremie",)), ("Lamentations", ("lamentation",)),
    ("Ézéchiel", ("ezechiel",)), ("Daniel", ()), ("Osée", ("osee",)),
    ("Joël", ("joel",)), ("Amos", ()), ("Abdias", ()), ("Jonas", ()),
    ("Michée", ("michee",)), ("Nahum", ()), ("Habacuc", ()),
    ("Sophonie", ()), ("Aggée", ("aggee",)), ("Zacharie", ()),
    ("Malachie", ()), ("Matthieu", ()), ("Marc", ()), ("Luc", ()),
    ("Jean", ()), ("Actes", ("acte",)), ("Romains", ()),
    ("Corinthiens", ("corinthien",)), ("Galates", ("galate",)),
    ("Éphésiens", ("ephesien", "ephesiens")),
    ("Philippiens", ("philippien",)), ("Colossiens", ("colossien",)),
    ("Thessaloniciens", ("thessalonicien",)), ("Timothée", ("timothee",)),
    ("Tite", ()), ("Philémon", ("philemon",)),
    ("Hébreux", ("hebreu", "hebreux")), ("Jacques", ()), ("Pierre", ()),
    ("Jude", ()), ("Apocalypse", ()),
]:
    _enregistre(_c, *_v)


# Confusions de transcription connues : ce que le moteur de reconnaissance
# ecrit, et le livre que le locuteur a reellement dit. Cette table n'est
# JAMAIS consultee en dehors d'une annonce complete — un declencheur
# « verset », un chapitre ET un verset. « un roman de 8 cents pages » ne
# declenche rien et ne passe donc jamais ici.
#
# Une seule entree est mesuree : « Le roman 8 verset 28 » dans b17a54cc, ou
# le locuteur dit Romains 8:28. Les autres seraient des suppositions, et une
# supposition fausse ecrit un faux verset a l'ecran. La table est faite pour
# grandir a mesure que des cas sont OBSERVES, pas imagines.
_CONFUSIONS: dict[str, str] = {
    "roman": "Romains",      # mesure : b17a54cc, 485,6 s
    "romans": "Romains",     # meme confusion au pluriel
}


def _texte(mot) -> str:
    if isinstance(mot, dict):
        return str(mot.get("text") or "")
    return str(getattr(mot, "text", "") or "")


def _debut(mot) -> float:
    try:
        return float(mot.get("start") if isinstance(mot, dict)
                     else getattr(mot, "start", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _nu(t: str) -> str:
    return str(t or "").strip(".,:;!?«»\"'()[]…")


def normalise_livre(brut: str) -> tuple[str, bool]:
    """(nom affiche, est-il reconnu).

    Un livre inconnu est rendu tel qu'il a ete dit : « roman » reste
    « roman ». Inventer « Romains » serait deviner une ecriture a la place
    du locuteur, et la transcription se trompe assez souvent pour que cette
    supposition finisse par ecrire un faux verset a l'ecran.
    """
    brut = str(brut or "").strip()
    if not brut:
        return "", False
    prefixe = ""
    m = re.match(r"^([1-3])\s+(.*)$", brut)
    if m:
        prefixe, brut = m.group(1) + " ", m.group(2)
    canon = _LIVRES.get(_sans_accent(_nu(brut)))
    if canon:
        return prefixe + canon, True
    return (prefixe + brut).strip(), False


_FIN_DE_PHRASE = (".", "!", "?", "…")


def _cherche_livre(mots, i: int) -> str:
    """Les mots de nom de livre qui precedent l'indice i, au plus deux.

    La recherche s'arrete a la fin de la phrase precedente. Sans cette borne
    elle remontait dans la phrase d'avant et rendait « se passe » comme nom
    de livre pour « C'est litteralement ce qui se passe. Verset 38 », et
    « discipliner Levitique » pour « ... de te discipliner. Levitique 26 ».
    """
    pris: list[str] = []
    j = i - 1
    while j >= 0 and len(pris) < 2:
        brut = _texte(mots[j]).rstrip("\"'»)]")
        t = _nu(brut)
        if not t:
            break
        if brut.endswith(_FIN_DE_PHRASE):
            break
        if re.fullmatch(r"[1-3]", t) and pris:
            pris.insert(0, t)         # « 1 » de « 1 Rois »
            break
        if not re.fullmatch(r"[0-9A-Za-zÀ-ÿ'’\-]+", t):
            break
        if _sans_accent(t) in _ARRET or t.isdigit():
            break
        pris.insert(0, t)
        j -= 1
    return " ".join(pris)


def annonces(mots) -> list[dict]:
    """Chaque annonce de reference trouvee dans la parole.

    Rend, par annonce : l'indice du premier et du dernier mot, le temps, le
    livre affiche et s'il a ete reconnu, le chapitre et le verset.
    """
    hits: list[tuple[int, str, int]] = []
    liste = list(mots or [])
    for i, w in enumerate(liste[:-1]):
        if not _DECLENCHEUR.fullmatch(_nu(_texte(w))):
            continue
        m = _NOMBRE.match(_nu(_texte(liste[i + 1])))
        if not m:
            continue
        hits.append((i, _sans_accent(_nu(_texte(w)))[:4], int(m.group(1))))

    # Un groupe ne contient jamais deux fois le meme mot declencheur : deux
    # « verset » voisins sont deux annonces, pas une. Sans cette condition,
    # « ce que le verset 44 dit. Le roman 8 verset 28 dit » fusionnait en une
    # seule annonce et Romains 8:28 disparaissait purement et simplement.
    groupes: list[list[tuple[int, str, int]]] = []
    for h in hits:
        if (groupes and h[0] - groupes[-1][-1][0] <= FUSION_MOTS
                and h[1] not in {k for _i, k, _n in groupes[-1]}):
            groupes[-1].append(h)
        else:
            groupes.append([h])

    out: list[dict] = []
    for g in groupes:
        chap = next((n for i, k, n in g if k == "chap"), None)
        vers = next((n for i, k, n in g if k == "vers"), None)
        i0 = g[0][0]
        # Le chapitre peut aussi etre le nombre colle devant « verset » :
        # « Levitique 26 verset 44 ».
        chap_ecrit = chap is not None
        if chap is None and i0 >= 1:
            m = re.fullmatch(r"(\d{1,3})", _nu(_texte(liste[i0 - 1])))
            if m:
                chap = int(m.group(1))
                chap_ecrit = True
                i0 -= 1
        # Un nom de livre n'est cherche que derriere un numero de chapitre.
        # « Verset 38 » n'a pas de chapitre : les mots qui le precedent sont
        # la phrase d'avant, jamais un nom de livre.
        livre, reconnu = (normalise_livre(_cherche_livre(liste, i0))
                          if chap_ecrit else ("", False))
        # La correction des confusions de transcription ne s'applique qu'ici :
        # annonce complete, chapitre ET verset presents, declencheur
        # « verset ». Hors de ces trois conditions le mot garde son sens
        # ordinaire.
        if (not reconnu and chap is not None and vers is not None
                and any(k == "vers" for _i, k, _n in g)):
            _c = _CONFUSIONS.get(_sans_accent(_nu(livre)))
            if _c:
                livre, reconnu = _c, True
        out.append({
            "i_debut": i0,
            "i_fin": g[-1][0] + 1,
            "debut": _debut(liste[i0]),
            "livre": livre,
            "livre_reconnu": reconnu,
            "chapitre": chap,
            "verset": vers,
        })
    return out


def reference(a: dict) -> tuple[str, str]:
    """(ligne 1, ligne 2) de la reference, telle qu'elle sera dessinee.

    Deux lignes, comme la maquette que KAN a validee : le livre au-dessus,
    le reperage chiffre en dessous. Sans livre, la ligne 1 porte le mot qui
    a ete dit — « Verset », « Chapitre » — parce que « Verset 38 dit » est
    une annonce complete pour celui qui ecoute.
    """
    chap, vers = a.get("chapitre"), a.get("verset")
    if chap is not None and vers is not None:
        reperage = f"{chap}:{vers}"
    elif chap is not None:
        reperage = str(chap)
    elif vers is not None:
        reperage = str(vers)
    else:
        return "", ""
    if a.get("livre"):
        return a["livre"], reperage
    if chap is not None and vers is None:
        return "Chapitre", reperage
    return "Verset", reperage


# ── de l'annonce a la carte ──────────────────────────────────────────────────
MAX_MOTS_VERSET = 8         # a la taille du sous-titre, 10 mots
                            # passaient a trois lignes
# Le vrai plafond est en CARACTERES, pas en mots : c'est la largeur qui
# decide du nombre de lignes. Mesure dans le navigateur, sur la composition
# reelle, guillemets compris : en portrait 49 caracteres tiennent sur deux
# lignes et 53 passent a trois et debordent sous les sous-titres ; en
# paysage 61 tiennent encore sur deux lignes.
MAX_CAR_PORTRAIT = 50
MAX_CAR_PAYSAGE = 62
DUREE_S = 4.0               # au-dessus du plancher de lisibilite de 3,0 s
PLANCHER_S = 3.0            # ENDSEC-CLAMP : en dessous, la carte ne se lit pas
FENETRE_MOTS = 26           # ou chercher la citation apres l'annonce
ECART_MIN_S = 20.0          # deux cartes verset ne se touchent pas
REPETITION_MIN_S = 120.0    # la meme reference peut revenir, pas coup sur coup
VOISINAGE_S = 2.0           # une carte du planner dans cette fenetre = doublon

STYLE = "verse_reference"
ZONE_PAYSAGE = "landscape-verse"   # tiers droit, 41,7 % de la largeur
ZONE_PORTRAIT = "portrait-verse-band"    # bandeau bas pleine largeur, la
                                         # position validee sur la maquette

# Mots qui introduisent la citation et n'en font pas partie.
_AMORCE = frozenset({"dit", "disait", "ecrit", "declare", "says", "said"})


def tient(texte: str, portrait: bool) -> bool:
    """Ce texte tient-il en deux lignes dans la carte de ce format ?"""
    return len(str(texte or "")) <= (MAX_CAR_PORTRAIT if portrait
                                     else MAX_CAR_PAYSAGE)


def ajuste(texte: str, portrait: bool) -> str:
    """Le texte ramene au budget en retirant des mots par la fin, ou "".

    Jamais tronque au milieu d'un mot et jamais dessine trop long : si
    quatre mots ne tiennent toujours pas, la carte renonce a sa citation et
    n'affiche que la reference. Un debordement doit etre signale, pas
    dessine.
    """
    entier = " ".join(str(texte or "").split())
    m = entier.split()

    def rendu(mots_: list[str]) -> str:
        # Le point de suspension suit la coupe : il annonce que la phrase
        # continue, et il n'a plus de sens si la coupe a disparu.
        bout = " ".join(mots_)
        if bout and bout != entier:
            bout = bout.rstrip(",;:…") + "…"
        return bout

    # Le budget se mesure sur la forme FINALE, point de suspension compris :
    # le mesurer avant l'ajout rendait un texte de 51 caracteres pour un
    # plafond de 50, et c'est bien le texte final qui est dessine.
    while m and not tient(rendu(m), portrait):
        if len(m) <= 4:
            return ""
        m.pop()
    return rendu(m)


def citation(mots, a: dict) -> str:
    """La suite CONTIGUE de 4 a 10 mots qui suit l'annonce, ou "".

    Jamais une paraphrase : les mots sont ceux qui ont ete prononces, dans
    l'ordre, sans trou. Seules les bornes sont choisies, par la meme fonction
    qui raccourcit une carte trop longue — une seule implementation du
    classement, pas deux.
    """
    from app.engine.controle_plan import condense
    liste = list(mots or [])
    j = a["i_fin"] + 1
    # Il faut une amorce de citation. Les mots qui suivent une annonce sont
    # aussi souvent le commentaire du locuteur que le verset lui-meme, et
    # rien ne les distingue dans la forme : « Levitique 26 verset 44, elle
    # montre que Dieu a laisse son peuple... » est un commentaire, « Verset
    # 38 dit, Vous disparaitrez... » est une citation. Le seul signe fiable
    # est le verbe d'enonciation. Sans lui, la reference s'affiche seule.
    amorce = False
    while j < len(liste) and _sans_accent(_nu(_texte(liste[j]))) in _AMORCE:
        amorce = True
        j += 1
    if not amorce:
        return ""
    # La citation est la PHRASE qui suit l'annonce, pas la meilleure phrase
    # de la fenetre. Sur une fenetre de 26 mots, le classement allait chercher
    # une phrase vingt mots plus loin et mettait « Tu vas etre detruit » sous
    # « Verset 38 », alors que le locuteur venait de citer autre chose.
    phrase: list[str] = []
    for k in range(j, min(j + FENETRE_MOTS, len(liste))):
        brut = _texte(liste[k]).strip()
        if not brut:
            continue
        phrase.append(brut)
        if brut.rstrip("\"'»)]").endswith(_FIN_DE_PHRASE):
            break
    if len(phrase) < 4:
        return ""
    if len(phrase) <= MAX_MOTS_VERSET:
        return " ".join(phrase)
    # Trop longue : la meme fonction que pour une carte trop longue choisit
    # une suite contigue bornee par la ponctuation.
    propre = condense(" ".join(phrase), max_mots=MAX_MOTS_VERSET, forcer=True)
    if propre:
        return propre
    # Mesure : aucune des trois citations de b17a54cc n'a de ponctuation
    # interne dans ses dix premiers mots — « Vous disparaitrez parmi les
    # nations et le pays de vos ennemis vous devorera. » fait quatorze mots
    # d'un seul tenant. Exiger une borne de ponctuation rendait la citation
    # impossible et la carte se reduisait toujours a sa reference.
    #
    # Le repli coupe donc a dix mots et le dit : les mots restent ceux qui
    # ont ete prononces, dans l'ordre, sans un seul ajout, et les points de
    # suspension annoncent que la phrase continue. Rien n'est paraphrase ;
    # ce qui est montre est exactement ce qui a ete dit.
    from app.engine.accent import MOTS_VIDES, PAS_EN_TETE, cle
    coupe = phrase[:MAX_MOTS_VERSET]
    # Ne pas finir sur « et », « de », « vos » : un mot-outil en bout de
    # coupe se lit comme une phrase cassee, pas comme une citation tronquee.
    # Le court-mot est la vraie regle : « de », « vos », « au » ne finissent
    # jamais une citation, et la liste des mots vides ne porte pas les
    # possessifs parce qu'elle sert a choisir un mot d'accent, ou tout ce qui
    # fait moins de quatre lettres est deja ecarte par la longueur.
    while len(coupe) > 4 and (cle(coupe[-1]) in (PAS_EN_TETE | MOTS_VIDES)
                              or len(cle(coupe[-1])) <= 3):
        coupe.pop()
    if len(coupe) < 4:
        return ""
    return " ".join(coupe).rstrip(",;:") + "…"


def cartes_verset(mots, cartes_existantes, duree: float,
                  portrait: bool = False) -> tuple[list[dict], list[dict]]:
    """(cartes a ajouter, journal de ce qui a ete decide pour chaque annonce).

    Rien n'est modifie : la fonction rend de nouvelles cartes et un journal.

    Trois raisons de ne rien poser, toutes mesurees sur b17a54cc :

      * **le planner a deja une carte sur cette annonce** — il en pose trois
        sur huit et il les ecrit bien. Deux cartes sur le meme temps sont le
        doublon qu'on veut eviter, et la sienne est au moins aussi bonne :
        elle vient d'une lecture du passage, la mienne d'un motif.
      * **un rappel** — « on arrive au verset 44 », « ce que le verset 44
        dit » : pas de livre, pas de chapitre, et ce numero a deja ete
        affiche. C'est un renvoi, pas une annonce.
      * **la carte ne tiendrait pas 3,0 s** avant la fin de la video.
    """
    liste = list(mots or [])
    deja = [c for c in (cartes_existantes or []) if c.get("type") != "caption"]
    out: list[dict] = []
    journal: list[dict] = []
    vus: set[str] = set()
    derniere: dict[str, float] = {}
    for n, a in enumerate(annonces(liste)):
        l1, l2 = reference(a)
        note = {"debut": round(a["debut"], 1), "ref": f"{l1} {l2}".strip(),
                "livre_reconnu": a["livre_reconnu"]}
        if not l2:
            note["decision"] = "ignoree : aucun reperage chiffre"
            journal.append(note)
            continue
        # Un rappel n'a ni livre ni chapitre et renvoie a un numero deja
        # affiche. Le numero seul doit etre compare : « Verset 44 » renvoie
        # bien a « Levitique 26:44 », et comparer les chaines entieres
        # laissait passer les deux rappels de b17a54cc.
        nu_vers = str(a.get("verset") or "")
        rappel = (not a["livre"] and a["chapitre"] is None
                  and nu_vers and nu_vers in vus)
        vus.add(l2)
        if nu_vers:
            vus.add(nu_vers)
        if rappel:
            note["decision"] = "ignoree : rappel d'un verset deja affiche"
            journal.append(note)
            continue

        debut = float(a["debut"])
        voisine = next(
            (c for c in deja
             if float(c.get("startSec", -99)) - VOISINAGE_S <= debut
             <= float(c.get("endSec", -99)) + VOISINAGE_S), None)
        if voisine is not None:
            note["decision"] = (
                f"ignoree : {voisine.get('id')} "
                f"({(voisine.get('contentHints') or {}).get('style', '?')}) "
                f"couvre deja cette annonce — la carte du planner est gardee")
            journal.append(note)
            continue

        fin = min(debut + DUREE_S, float(duree or 0) or debut + DUREE_S)
        if fin - debut < PLANCHER_S - 0.01:
            note["decision"] = (f"ignoree : {fin - debut:.2f}s avant la fin, "
                                f"sous le plancher de {PLANCHER_S:.1f}s")
            journal.append(note)
            continue
        cle = f"{l1}|{l2}"
        if debut - derniere.get(cle, -1e9) < REPETITION_MIN_S:
            note["decision"] = (
                f"ignoree : meme reference affichee il y a "
                f"{debut - derniere[cle]:.0f}s")
            journal.append(note)
            continue
        if out and debut - float(out[-1]["startSec"]) < ECART_MIN_S:
            note["decision"] = (f"ignoree : {debut - float(out[-1]['startSec']):.1f}s "
                                f"apres la precedente carte verset")
            journal.append(note)
            continue

        brut = citation(liste, a)
        txt = ajuste(brut, portrait)
        if brut and not txt:
            note["citation_refusee"] = brut
        carte = {
            "id": f"vrs-{n + 1:02d}",
            "type": "graphic",
            "startSec": round(debut, 3),
            "endSec": round(fin, 3),
            "zone": ZONE_PORTRAIT if portrait else ZONE_PAYSAGE,
            "contentHints": {"style": STYLE, "verse_book": l1,
                             "verse_locus": l2, "verse_text": txt},
        }
        out.append(carte)
        derniere[cle] = debut
        note["decision"] = (
            f"posee {carte['id']} {debut:.1f}-{fin:.1f}s"
            + (f" + citation {len(txt.split())} mots, {len(txt)} car"
               if txt else
               (f" (reference seule : citation de {len(brut)} car ne tient "
                f"pas en deux lignes)" if brut else " (reference seule)")))
        note["citation"] = txt
        journal.append(note)
    return out, journal
