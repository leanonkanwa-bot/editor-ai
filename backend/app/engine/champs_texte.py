"""R1b — what text a card actually draws, per style.

Two consumers wanted this and they do not want the same thing, which is why
one map was the wrong shape:

  * the ANCHORING wants the prose the speaker SAYS, in speech order, so a card
    can be moved onto the word that states it. That is _DATA_ANCHOR_FIELDS in
    storyboard.py, consulted only when title is empty — it can add anchoring
    where there was none but never displace a correct one.

  * the DIAGNOSTIC, the R1 control and the accent word want whatever ends up
    on screen, in any order, including a name or a label that would make a
    poor anchor.

Measured on b17a54cc: card-26 (myth_vs_fact) and card-34 (warning_soft) have
an empty `text` in the diagnostic while carrying myth_text/fact_text and
warning_text. They are not blank cards — the premise that they were is what
this file corrects. Everything keyed on `text` simply could not see them: the
diagnostic showed nothing, the anchoring had nothing to match, and R3 had no
text to pick an accent word from.

Across the 25 styles those two renders actually used, 14 read a text field
that no map listed.
"""
from __future__ import annotations

# Per style, the fields it draws text from, in the order they are SPOKEN where
# that is knowable (myth before fact, before before after, left before right).
# `title`, `detail` and `kicker` are tried first for every style, so they are
# not repeated here.
CHAMPS_AFFICHES: dict[str, tuple[str, ...]] = {
    "action_step_cta":          ("cta_text",),
    "attributed_quote":         ("attribution",),
    "audience_question":        ("question_text",),
    "before_after_image":       ("before_label", "after_label",
                                 "before_badge", "after_badge"),
    "cause_effect":             ("cause_text", "effect_text"),
    "comparison":               ("left_label", "left_value",
                                 "right_label", "right_value"),
    "contrarian_take":          ("take_text",),
    "definition":               ("term", "definition"),
    "dialogue":                 ("line_a", "line_b"),
    "emoji_reaction":           ("emoji_label",),
    "hand_written_note":        ("note_text",),
    "list":                     ("items",),
    "myth_vs_fact":             ("myth_text", "fact_text"),
    "poll_question":            ("poll_question",),
    "prim_confession_frame":    ("confession_text",),
    "prim_shatter_truth":       ("truth_text", "myth_text"),
    "prim_split_compare":       ("left_label", "right_label"),
    "prim_split_stage":         ("steps",),
    "prim_stat_counter":        ("stat_value",),
    "question_answer_pair":     ("qa_question", "qa_answer"),
    "recap_summary":            ("recap_items",),
    "speech_bubble_thought":    ("thought_text",),
    "star_rating_review":       ("review_text",),
    "story_chapter_transition": ("transition_label",),
    "testimonial":              ("testimonial_text",),
    "verse_reference":          ("verse_book", "verse_locus", "verse_text"),
    "timeline":                 ("steps",),
    "warning_soft":             ("warning_text",),
}

# Tried for every style, before the per-style fields.
CHAMPS_COMMUNS: tuple[str, ...] = ("title", "detail", "text", "kicker")

# Styles that draw no text by design (compose.py _STYLES_SANS_TEXTE).
STYLES_SANS_TEXTE = frozenset({"prim_anecdote_frame"})


def _plat(v) -> str:
    if isinstance(v, list):
        return " ".join(str(x) for x in v if str(x).strip()).strip()
    return str(v or "").strip()


def texte_affiche(hints: dict, joindre: bool = False) -> str:
    """The text this card puts on screen, or "" when it draws none.

    joindre=False returns the FIRST field that has content — what a single
    label or an anchor wants. joindre=True returns every field joined, which
    is what an audit asking "does this card say anything" needs: a
    myth_vs_fact says two things and reporting only the myth would under-count
    its text.
    """
    if not isinstance(hints, dict):
        return ""
    morceaux: list[str] = []
    for f in CHAMPS_COMMUNS + CHAMPS_AFFICHES.get(hints.get("style", ""), ()):
        v = _plat(hints.get(f))
        if not v:
            continue
        if not joindre:
            return v
        if v not in morceaux:
            morceaux.append(v)
    return " ".join(morceaux)


def dessine_du_texte(hints: dict) -> bool:
    """True when this card will put readable text on screen."""
    if not isinstance(hints, dict):
        return False
    if hints.get("style") in STYLES_SANS_TEXTE:
        return False
    return bool(texte_affiche(hints))
