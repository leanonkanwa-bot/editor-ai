"""Une erreur passagere se declare a la source, elle ne se devine pas.

`RuntimeError` servait aux deux : ffmpeg tue par signal (souvent la memoire,
donc passager) ET les assertions explicites de render.py (« BUG: ...
word-snap should have prevented this », definitif par nature). Classer sur
le TYPE etait donc impossible, et classer en LISANT le message est fragile
— un message reformule changerait la classification sans que personne ne le
voie.

D'ou une exception typee, levee la ou on sait que la cause est passagere.
Tout le reste est definitif PAR DEFAUT : on ne reprend que ce qu'on a
explicitement declare reprenable. Se tromper dans ce sens coute une alerte
de trop ; se tromper dans l'autre ferait boucler un rendu sur une erreur de
code.

ErreurPassagere herite de RuntimeError a dessein : tous les
`except RuntimeError` existants continuent de l'attraper, donc introduire
le type ne change aucun comportement.
"""

from __future__ import annotations


class ErreurPassagere(RuntimeError):
    """Une cause dont on sait qu'un second essai peut reussir.

    A lever SEULEMENT quand la cause est exterieure et transitoire :
    memoire epuisee, ressource momentanement indisponible, delai depasse.
    Jamais pour une donnee invalide ni pour un defaut de code.
    """


def est_passagere(exc: BaseException | None) -> bool:
    """Vrai seulement pour ErreurPassagere. Aucun message n'est lu.

    Volontairement aveugle au texte : c'est le site qui leve qui sait, pas
    celui qui rattrape.
    """
    return isinstance(exc, ErreurPassagere)
