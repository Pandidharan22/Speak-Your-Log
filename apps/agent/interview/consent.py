"""Classify the student's reply to the read-back as confirm / edit / cancel / unclear.

Deliberately rule-based, with NO language model (ADR-005 addendum): the student's speech is
untrusted input, and a model in the consent path could be talked into "confirm". Rules cannot be
talked into anything, and doubt always resolves to UNCLEAR, which only ever re-asks.

Handles English, Tamil script, Tanglish (Tamil in Latin letters) and mixes. Guiding rules:
  * CONFIRM needs an explicit yes or an explicit "post it" AND nothing that blocks it
    (a negation, an edit request, a condition like "but ..."). "okay" alone is not enough.
  * "post it, but change X" is EDIT; "don't post it" is CANCEL; "no" alone is UNCLEAR
    (the agent then asks whether to post, redo or cancel).
  * Anything unrecognised is UNCLEAR. This function never raises.
"""

import re
import unicodedata

from interview.flow import Intent

# ---- vocabulary -------------------------------------------------------------------------------
# Tokens are matched as whole words after lower-casing and replacing punctuation with spaces.
# "Prefix" lists match Tamil / Tanglish word stems, because those languages inflect their verbs.

AFFIRM = {
    # English ("correct" is deliberately absent: "that's correct" vs "please correct it")
    "yes", "yeah", "yep", "yup", "yea", "sure", "definitely", "absolutely",
    # Tamil
    "ஆமா", "ஆம்", "ஆமாம்", "ஆமாங்க", "ஆமாம்ங்க",
    # Tanglish
    "aama", "aamam", "amam", "aamaanga", "aamanga",
}  # fmt: skip
AFFIRM_PHRASES = ("go ahead", "do it")

POST_TOKENS = {"post", "posted", "publish", "submit", "send", "upload"}
POST_PREFIXES = ("போட", "பதிவு", "அனுப்", "podu", "poda", "pottu", "pathivu", "anuppu")

CANCEL_TOKENS = {
    "cancel", "stop", "forget", "nevermind", "abort", "quit",
    "வேண்டாம்", "வேணாம்", "வேண்டா", "நிறுத்து", "நிறுத்துங்க",
    "venam", "vendam", "venaam", "niruthu", "niruthunga",
}  # fmt: skip
CANCEL_PHRASES = ("never mind", "forget it")

EDIT_TOKENS = {
    "change", "edit", "fix", "wrong", "incorrect", "redo", "again", "restart", "rewrite", "retry",
}  # fmt: skip
EDIT_PREFIXES = (
    "மாத்", "மாற்", "திருத்", "மறுபடி", "திரும்ப", "தப்ப", "தவற",
    "maathu", "mathu", "thirutha", "thiruthu", "marupadi", "marubadi", "thirumba",
    "thappu", "thavaru",
)  # fmt: skip
EDIT_PHRASES = ("start over", "start again", "say it again", "let me redo")

# "no," as an opener is not an auxiliary negation: "no, change it" is an edit request.
INTERJECTIONS = {"no", "nope", "nah", "இல்ல", "இல்லை", "illa", "illai"}
# English negations (n't contractions are rewritten to "not" in _tokens; the apostrophe-less
# forms below are for typed text). They come BEFORE the verb: "don't post".
ENGLISH_NEGATIONS = {
    "no", "nope", "nah", "not", "never", "dont", "wont", "cant", "doesnt", "isnt", "didnt",
    "shouldnt",
}  # fmt: skip
NEGATION = ENGLISH_NEGATIONS | INTERJECTIONS | {"neither", "இல்லீங்க", "illeenga"}
# Tamil negative-imperative endings: போடாதீங்க (don't post), பண்ணாதீங்க, pannadhinga, maathaathe ...
NEGATIVE_SUFFIXES = (
    "ாதீங்க", "ாதீங்கள்", "ாதே", "ாதீர்கள்",
    "adheenga", "aadheenga", "athinga", "aathinga", "adhinga", "aadhinga",
    "athe", "aathe", "adhe", "aadhe",
)  # fmt: skip

CONDITIONAL = {
    "pretend", "suppose", "imagine", "but", "however", "actually", "instead", "unless", "if",
    "maybe", "perhaps", "wait", "hold", "though", "except", "only",
    "ஆனா", "ஆனால்", "ஆனாலும்", "aana", "aanaal", "aanalum",
}  # fmt: skip
HOLD_PHRASES = ("hang on", "one second", "one moment", "ஒரு நிமிடம்", "oru nimisham")

MAX_LEN = 600  # a reply to a yes/no question is short; anything longer is not a clear answer


# ---- normalisation ----------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    """Lower-case, turn punctuation/symbols into spaces, split on whitespace. Tamil letters and
    their vowel signs are kept intact (word-boundary regexes would cut Tamil words apart)."""
    text = unicodedata.normalize("NFC", text).casefold()
    # Phones and speech-to-text emit curly apostrophes (don’t); fold them first, then turn every
    # n't contraction into "not" (don't, won't, can't, isn't, ...) so none can slip past.
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = re.sub(r"n't\b", " not", text)
    cleaned = "".join(" " if unicodedata.category(ch)[0] in "PSZC" else ch for ch in text)
    return cleaned.split()


def _is_post(tok: str) -> bool:
    return tok in POST_TOKENS or tok.startswith(POST_PREFIXES)


def _is_negative(tok: str) -> bool:
    return tok in NEGATION or tok.endswith(NEGATIVE_SUFFIXES)


def _is_edit(tok: str) -> bool:
    return tok in EDIT_TOKENS or tok.startswith(EDIT_PREFIXES)


def _negation_applies(toks: list[str], neg: int, post: int) -> bool:
    """English negations come BEFORE the verb ("don't post"); Tamil/Tanglish ones come after it
    ("post pannaadheenga", "post venam"). Accept the natural order for each."""
    if toks[neg] in ENGLISH_NEGATIONS:
        return neg < post
    return post <= neg


# ---- the classifier ---------------------------------------------------------------------------


def classify(text: str) -> Intent:
    """The meaning of the student's reply to "shall I post this?". UNCLEAR when in any doubt."""
    try:
        return _classify(text)
    except Exception:  # noqa: BLE001 - consent handling must never crash; doubt means "re-ask"
        return Intent.UNCLEAR


def _classify(text: str) -> Intent:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_LEN:
        return Intent.UNCLEAR
    toks = _tokens(text)
    joined = " ".join(toks)

    has_affirm = any(t in AFFIRM for t in toks) or any(p in joined for p in AFFIRM_PHRASES)
    post_idx = [i for i, t in enumerate(toks) if _is_post(t)]
    neg_idx = [i for i, t in enumerate(toks) if _is_negative(t)]
    hard_neg = [i for i in neg_idx if toks[i] not in INTERJECTIONS]
    has_cancel = any(t in CANCEL_TOKENS for t in toks) or any(p in joined for p in CANCEL_PHRASES)
    has_edit = any(_is_edit(t) for t in toks) or any(p in joined for p in EDIT_PHRASES)
    has_conditional = any(t in CONDITIONAL for t in toks) or any(p in joined for p in HOLD_PHRASES)

    if has_cancel:
        return Intent.CANCEL
    if has_edit and not hard_neg:  # "no, change it" is an edit; "don't change it" is not
        return Intent.EDIT
    # "don't post" / "not post" / "post ... venam": a negation right next to a post word. Only when
    # nothing is conditional: "if I say no then post it" is not a cancellation.
    if not has_conditional and any(
        abs(n - p) <= 3 for n in neg_idx for p in post_idx if _negation_applies(toks, n, p)
    ):
        return Intent.CANCEL
    if neg_idx or has_conditional:
        return Intent.UNCLEAR  # "no", "post it, don't change anything", "yes but ..." -> ask again
    if has_affirm or post_idx:
        return Intent.CONFIRM
    return Intent.UNCLEAR
