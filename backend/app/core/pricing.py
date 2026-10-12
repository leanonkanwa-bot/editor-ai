"""The one place a token price is written down.

Before this file, the only call site that printed a cost — LLM-EDIT in
pipeline.py — carried its own hardcoded table: Haiku 4.5 at $0.80/$4.00 or
"Sonnet 4.5" at $3/$15, chosen by looking for "haiku" in the model name. The
configured model is claude-opus-4-7 at $5/$25, so every `cost=$…` line it ever
printed under-reported by a factor of 1.67. A measuring tool that lies is
worse than no tool: it gives false confidence about spend.

So the rate lives here, once, keyed by the exact model id the SDK is called
with. Two rules follow from how the old table failed:

  * never infer a price from a substring of the model name;
  * an unknown model returns None and the caller says "tarif inconnu" rather
    than printing a number that happens to be wrong.

Prices are USD per million tokens, Anthropic first-party API rates. They are
a cached copy of a published table and will drift — the None that `prix()`
returns for an unlisted id is what makes that drift visible instead of silent.
"""
from __future__ import annotations

# $/1M tokens, (input, output). Exact model ids only — no prefix matching.
PRIX_PAR_MTOK: dict[str, tuple[float, float]] = {
    "claude-fable-5-1":  (10.00, 50.00),
    "claude-fable-5":    (10.00, 50.00),
    "claude-opus-5-5":   (4.00, 20.00),
    "claude-opus-5":     (5.00, 25.00),
    "claude-opus-4-8":   (5.00, 25.00),
    "claude-opus-4-7":   (5.00, 25.00),   # the configured model
    "claude-opus-4-6":   (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5":   (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-5-5":  (0.10, 0.50),
    "claude-haiku-4-5":  (1.00, 5.00),
}


def prix(modele: str) -> tuple[float, float] | None:
    """(input, output) $/1M for this exact model id, or None if unlisted."""
    return PRIX_PAR_MTOK.get(str(modele or "").strip())


def cout_usd(modele: str, tokens_in: int, tokens_out: int) -> float | None:
    """Cost in USD, or None when the model's rate is not listed here."""
    p = prix(modele)
    if p is None:
        return None
    return (tokens_in * p[0] + tokens_out * p[1]) / 1_000_000


def resume(modele: str, tokens_in: int, tokens_out: int) -> str:
    """One log-ready fragment: tokens, and the cost or an explicit unknown."""
    c = cout_usd(modele, tokens_in, tokens_out)
    if c is None:
        return (f"tokens={tokens_in}in+{tokens_out}out "
                f"cost=tarif inconnu pour {modele!r}")
    return f"tokens={tokens_in}in+{tokens_out}out cost=${c:.4f}"
