"""The distinctive word of a card's own text — in one place.

compose.py draws lean_craft's ink line, and every pack's accent swipe, only on
the span _split_title_accent() builds, and only when contentHints.accent_word
is non-empty AND found in the rendered text. The safety net learned to supply
that word; the planner and GAP-FILL never did, so on job b17a54cc only 7 of 41
cards carried one (measured) while 32 more had a usable word sitting in their
title.

The selector used to live inside inject_safety_cards as a closure, which meant
an offline audit had to re-implement it — and a re-implementation agrees with
itself, not with the code that ships. It is here now so the net, the planner
and any audit ask the same function.

"Distinctive" is measured against THIS transcript, not a dictionary: the rarest
word of the card's own text is the one that carries it. Function words are the
most frequent by construction, so frequency does most of the work; the stop
list is for a short video where a function word happens to be rare, and for
deictics ("ici", "chose") that are content-shaped but say nothing on a card.
"""
from __future__ import annotations

import re

# fonction, FR + déictiques + fonction EN (une vidéo anglaise passe par le même
# chemin). Déplacée telle quelle depuis render.py — toute modification change
# le comportement du filet, qui est couvert par un test.
MOTS_VIDES: frozenset[str] = frozenset({
    "alors", "aussi", "autre", "autres", "avec", "avoir", "bien", "cela",
    "cette", "ceux", "chez", "comme", "dans", "donc", "elle", "elles",
    "encore", "etre", "être", "fait", "faire", "faut", "leur", "leurs",
    "mais", "meme", "même", "moins", "notre", "nous", "parce", "pour",
    "pourquoi", "quand", "quelque", "quelqu'un", "sans", "sont", "sous",
    "tous", "tout", "toute", "toutes", "très", "tres", "vais", "veut",
    "veux", "voir", "vont", "votre", "vous", "était", "etait", "plus",
    "peut", "dire", "dit", "deja", "déjà", "toujours", "jamais", "juste",
    "puis", "ensuite", "enfin", "avant", "apres", "après", "depuis",
    "pendant", "surtout", "vraiment", "beaucoup", "peut-etre", "peut-être",
    "quoi", "trop",
    "ici", "chose", "choses", "truc", "trucs", "machin", "ça", "cas",
    "about", "after", "also", "because", "been", "before", "being",
    "could", "does", "every", "from", "have", "here", "into", "just",
    "like", "more", "much", "only", "other", "same", "should", "some",
    "such", "than", "that", "their", "them", "then", "there", "these",
    "they", "thing", "things", "this", "those", "very", "what", "when",
    "which", "will", "with", "would", "your",
})

# Mots qui ne peuvent pas OUVRIR une proposition : la ponctuation seule ne
# distingue pas une proposition d'une continuation, une virgule en coupe
# souvent une. Utilisée par le raccourcissement du filet.
PAS_EN_TETE: frozenset[str] = frozenset({
    "et", "mais", "ou", "donc", "or", "ni", "car", "que", "qu", "qui",
    "quoi", "dont", "parce", "puisque", "comme", "alors", "ainsi", "puis",
    "ensuite", "de", "du", "des", "d", "a", "à", "au", "aux", "en", "dans",
    "sur", "sous", "pour", "par", "avec", "sans", "chez", "vers", "entre",
    "pendant", "afin", "sinon", "tandis", "lorsque", "quand",
    "and", "but", "so", "because", "that", "which", "who", "then", "of",
    "to", "in", "on", "for", "with", "at", "by", "from", "while",
})

_JETON = re.compile(r"[0-9A-Za-zÀ-ÿ'’\-]+")
_ELISION = re.compile(r"['’]")


def cle(mot: str) -> str:
    """The form a frequency count is kept under: case-folded, bare."""
    return str(mot or "").strip(".,!?…:;«»\"'()[]").casefold()


def table_frequences(mots) -> dict[str, int]:
    """Count every spoken word once, over the whole transcript.

    Accepts WordTiming-likes (.text) or dicts ({"text": …}), because the
    planner holds one shape and the renderer the other.

    The elision parts are counted TOO, because mot_distinctif offers them as
    candidates: counting only whole tokens made "abandonne" (spoken as
    "t'abandonne") score 0 and therefore look rarer than "Levitique", which is
    said twice. Measured on job b17a54cc - the selector preferred the common
    word over the proper noun. Counting both forms removes that bias.
    """
    freq: dict[str, int] = {}
    for w in mots or []:
        t = w.get("text") if isinstance(w, dict) else getattr(w, "text", "")
        k = cle(t)
        if not k:
            continue
        freq[k] = freq.get(k, 0) + 1
        if "'" in k or "’" in k or "-" in k:
            for part in re.split(r"['’\-]", k):
                if len(part) >= 4 and part != k:
                    freq[part] = freq.get(part, 0) + 1
    return freq


def mot_distinctif(texte: str, freq: dict[str, int]) -> str:
    """The rarest real word of this text, returned AS SPELLED in it.

    The surface form matters: _split_title_accent() locates the span with
    texte.lower().find(accent_word.lower()), so a normalised word that is not a
    substring of the text produces no span and no accent — the failure would be
    silent and look exactly like having no word at all.
    """
    candidats: list[tuple[int, int, str]] = []
    for brut in _JETON.findall(str(texte or "")):
        # French elision: "l'éternel" must offer "éternel", not the article
        # glued to it — and "éternel" is still a substring of the text.
        for part in _ELISION.split(brut):
            if len(part) < 4:
                continue
            k = cle(part)
            if not k or k in MOTS_VIDES:
                continue
            # rarest first; on a tie the longer word, which reads as the more
            # specific one.
            candidats.append((freq.get(k, 1), -len(part), part))
    if not candidats:
        return ""
    candidats.sort()
    return candidats[0][2]


def ouvre_mal(mot) -> bool:
    """True when this word cannot open a readable clause."""
    t = str(getattr(mot, "text", mot) or "")
    t = re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", t).casefold()
    return (not t) or t in PAS_EN_TETE
