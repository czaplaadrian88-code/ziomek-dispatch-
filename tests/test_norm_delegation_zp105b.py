"""Z-P1-05 Faza B — proof that identity.normalize.norm is byte-equivalent to the
old inline ``(s or "").strip().rstrip(".,;:").lower()`` formula, and that the
delegating wrappers (courier_info._norm, panel_roster._norm_token) match it.

This pins the delegation of the 6 inline norm copies to a single contract with
ZERO behavior change (diacritics are NOT folded — the cid 376 landmine).
"""
import pytest

from dispatch_v2.identity.normalize import norm
from dispatch_v2 import courier_info, panel_roster


def _old_formula(s):
    """The exact inline formula that lived in the 6 copies before Faza B."""
    return (s or "").strip().rstrip(".,;:").lower()


CORPUS = [
    None, "", "   ", "\t", "\n x \n", "Ch.", "Bartek O,", "Bartek O.",
    "Adrian Cit", "Adrian Citko", "PAWEŁ ŚCIEPKO", "Paweł Sc", "Łódź;",
    "  Żaba:  ", "a.b.c.", "...", ",;:.", "Anna   Ko", "café", "naïve",
    "Kuba Olchowik", "Jakub OL", "  MiXeD CaSe  ;", "x;:,.",
]


@pytest.mark.parametrize("s", CORPUS)
def test_norm_matches_old_inline_formula(s):
    assert norm(s) == _old_formula(s)


@pytest.mark.parametrize("s", CORPUS)
def test_courier_info_norm_delegates(s):
    assert courier_info._norm(s) == norm(s)


@pytest.mark.parametrize("s", CORPUS)
def test_panel_roster_norm_token_delegates(s):
    assert panel_roster._norm_token(s) == norm(s)


def test_diacritics_not_folded():
    # Ś must stay ś (must not collapse to ascii 's') — reproduces cid 376 safety.
    assert norm("Ściepko") == "ściepko"
    assert norm("Ściepko") != norm("Sciepko")


def test_trailing_punct_and_whitespace():
    assert norm("  Grzegorz W. ") == "grzegorz w"
    assert norm("Bartek O,") == "bartek o"
    assert norm(",;:.") == ""
