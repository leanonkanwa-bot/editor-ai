# Réglages à revalider

Chaque nombre ci-dessous a été choisi en regardant **un seul discours**, rendu
trois fois : `37f76379`, `b17a54cc`, `6d9a6c80` — même locuteur, même sujet
(discipline divine, Lévitique 26), 533,7 s, paysage, pack `lean_craft`,
français. Plus les trois clips portrait de 6 à 16 s tirés du même contenu.

Un réglage ajusté sur un seul contenu n'est pas validé, il est **ajusté à ce
contenu**. Ce fichier existe pour que l'ajout d'une vidéo différente — autre
locuteur, autre langue, autre sujet, autre durée — soit l'occasion de les
reprendre un par un, au lieu de les découvrir en panne.

Chaque ligne porte son étiquette : **mesuré** (relevé sur un rendu réel) ou
**hypothèse** (choisi par raisonnement, jamais vérifié sur un second contenu).

## Ce qui manque pour lever les hypothèses

Aucun de ces nombres n'a vu : une **autre langue** (le planner a des exemples
français dans son prompt), un **autre locuteur** (débit, pauses, élisions), un
**autre registre** que le prêche (une démo produit n'a ni verset ni aveu), une
durée **entre 30 s et 500 s** (le trou le plus large : le court mesuré s'arrête
à 16,5 s, le long commence à 533,7 s), et un **autre pack** que `lean_craft`
pour les réglages visuels.

---

## R1 — contrôle du plan (`backend/app/engine/controle_plan.py`)

| Réglage | Valeur | Étiquette | Ce qui l'a justifié |
|---|---|---|---|
| `MAX_MOTS` = 12 | plafond de mots sur une carte | **mesuré** | 2 cartes sur 41 dépassent sur `b17a54cc`, 2 sur 27 sur `37f76379`. Sous 12 mots, aucune carte n'a été jugée illisible. Le seuil tient parce que le locuteur fait des phrases courtes — un locuteur plus ample ferait exploser le compteur et condenserait partout. |
| `MIN_MOTS` = 4 | plancher d'une condensation | **hypothèse** | Choisi pour qu'une condensation reste une phrase. Jamais contredit, jamais vérifié. |
| `_MULTI_ITEM` (24 styles à 3,5 s) | plancher de lecture | **mesuré, repris du moteur** | Recopié de `_ENDSEC_MULTI_ITEM_STYLES` (`storyboard.py:4526`). Ce n'est pas mon chiffre : c'est celui qu'ENDSEC-CLAMP se promet déjà. À resynchroniser si cette liste bouge. |
| plancher hors-champ 3,0 s / 3,5 s | carte coupée par la fin | **mesuré** | `card-02` de `4e5b4dbf` démarre à 6,33 s sur 6,1 s ; `card-04` de `694af187` garde 1,13 s sur 3,0. Zéro cas sur les 41 cartes du long format — ce défaut est propre au format court. |
| `SEUIL_ZOOM` = 0,02 /s · `FENETRE_S` = 12 s | ce que « rien ne bouge » veut dire | **mesuré, mais contesté** | Repris du filet. Le ZOOM-SAFETY-NET utilise un autre critère (0,040 de course, soit 0,0067/s sur 6 s). Sur `b17a54cc` : 75 entrées sur 102 passent le critère de course, 47 seulement celui de vitesse. L'écart de 28 entrées est entièrement du `drift` (médiane 9,70 s à 0,0063/s). **Les deux filets ne s'accordent pas sur « perceptible ».** Un contenu plus calme fera diverger les deux comptages davantage. |

## R3 — mot d'accent (`backend/app/engine/accent.py`)

| Réglage | Valeur | Étiquette | Ce qui l'a justifié |
|---|---|---|---|
| `MOTS_VIDES` ≈ 110 entrées | mots jamais accentués | **hypothèse** | Liste FR + EN écrite à la main. Rien ne garantit la couverture d'une autre langue : en espagnol ou en allemand, le mot le plus rare d'une carte serait un article. |
| `PAS_EN_TETE` | mots qui ouvrent mal | **hypothèse** | Même remarque. |
| longueur minimale 4 caractères | candidat accentuable | **hypothèse** | Écarte « est », « pas », « Dieu » (4 → gardé). Un contenu riche en sigles ou en noms courts (« IA », « SEO ») n'aurait aucun candidat. |
| comptage des élisions | `table_frequences` | **mesuré** | « abandonne », prononcé « t'abandonne », comptait 0 et passait donc pour plus rare que « Levitique », dit deux fois. Spécifique aux langues à élision : inutile en anglais, nécessaire en français. |
| rejet d'un `accent_word` absent du texte | — | **mesuré** | 1 cas sur 7 mots fournis par le modèle : `card-25` portait « relever » quand son titre disait « Dieu va utiliser ça ». Le taux de 1/7 vient d'un seul discours ; il peut être bien pire ailleurs. |

## R1b — lecture du texte par style (`backend/app/engine/champs_texte.py`)

| Réglage | Valeur | Étiquette | Ce qui l'a justifié |
|---|---|---|---|
| 27 styles cartographiés | `CHAMPS_AFFICHES` | **mesuré, incomplet par construction** | Couvre les 25 styles que ces deux rendus ont employés. Le moteur en connaît beaucoup plus. **Chaque style jamais rencontré est un trou** : le diagnostic le verra vide et R3 n'aura aucun texte pour y poser un accent. Un contenu d'un autre genre emploiera d'autres styles et élargira ce trou avant de le combler. |
| `attributed_quote` → `attribution` | champ d'ancrage | **hypothèse** | Un nom propre s'ancre mal ; volontairement exclu de la carte d'ancrage. Non vérifié. |
| `emoji_reaction` exclu de l'ancrage | — | **hypothèse** | Une étiquette d'un mot s'ancre mal. Non vérifié. |

## R9 — carte verset (maquette seulement, aucun code)

| Réglage | Valeur | Étiquette | Ce qui l'a justifié |
|---|---|---|---|
| motif `(versets?\|chapitres?)\s+\d+` | déclencheur | **mesuré** | 9 occurrences sur 1892 mots, 0 faux positif, fusionnées en 8 annonces. |
| noms de livres **écartés** | déclencheur | **mesuré** | La transcription écrit « 1 roi », « Hébreu 12 », « Le roman 8 » pour Romains : les 59 noms de livres n'attrapent que 2 cas sur 9. **Ce réglage est propre au français parlé transcrit par ce moteur de reconnaissance** — un autre moteur, ou l'anglais, changerait le verdict. |
| ≤ 12 mots sur la carte | — | **hypothèse** | Hérité de `MAX_MOTS`. Les 8 cas mesurés tiennent entre 6 et 10 mots, mais un verset long cité en entier n'a jamais été rencontré. |
| fenêtre de fusion 6 s | une annonce = un événement | **hypothèse** | À 108,8 s, « chapitre 9 » et « Verset 6 » sont à 2 s l'un de l'autre et forment bien une seule annonce. Un seul cas observé. |

## R2 — budget de primitives (non codé, mesuré seulement)

| Réglage | Valeur | Étiquette | Ce qui l'a justifié |
|---|---|---|---|
| `_SB_PRIMITIVE_EVERY_S` = 480 s | cadence | **mesuré comme contradictoire** | Donne 1 primitive pour 534 s, alors que l'écart minimal livré dans la même phrase de prompt (`_SB_PRIMITIVE_MIN_GAP_S` = 240 s) en autorise 3, et que les gardes d'éviction du code en gardent jusqu'à 5. |
| budget **non appliqué en code** | — | **mesuré** | `_sb_budget_for` n'alimente qu'un texte de prompt. Les seuls plafonds réels sont les gardes post-fusion (1 PCR+PAR, 1 PCF, 1 shatter, 2 split_stage) ; `prim_split_compare` et `prim_anecdote_frame` ne sont plafonnés par rien. |

---

## Réglages déjà déployés et jamais revalidés ailleurs

Pour mémoire, parce qu'ils viennent du même unique contenu :

- densité de cartes `base_pace` (31e7cc9), filet 28 s (776dcea), filet 20 s puis
  12 s — tous calibrés sur ce discours ;
- échelle typographique portrait, facteur 0,361 et repère sous-titres 62 px
  (5a3be88) — **mesuré sur une seule capture portrait** ;
- `lean_paper` : listes ≥ 5 items → zones hautes (fbcb43e) — mesuré sur une
  liste de 7 items, la seule rencontrée.
