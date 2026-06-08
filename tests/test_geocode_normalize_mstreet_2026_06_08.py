r"""Regression: _normalize nie może zjadać nazwy ulicy na „M" (bug 2026-06-08).

Root cause: `\b(...|m|...)\.?\s*\w+` matchował „m"+reszta słowa = całą nazwę ulicy
zaczynającej się na M (Magazynowa, Malachitowa, Mickiewicza…). „Magazynowa 3" i
„Malachitowa 3" kolidowały w kluczu „3, białystok" → geo-poison (cache zwracał
cudze współrzędne; Magazynowa 3/31 → Malachitowa NE, 7.56 km błędu).

Diagnoza: propozycja 479375 (Kebab Król→Sobieskiego doklejone do Grill Kebab→
Magazynowa) wyglądała na korytarz (cosine +0.984) zamiast cross-direction (−0.976).
"""
from dispatch_v2.geocoding import _normalize, _is_streetless_key

CITY = "Białystok"


def k(addr):
    return _normalize(addr, CITY)


def test_m_streets_do_not_collide():
    # Sedno buga: te dwa MUSZĄ mieć różne klucze.
    assert k("Magazynowa 3/31") != k("Malachitowa 3")
    assert k("Magazynowa 3/31") == "magazynowa 3, białystok"
    assert k("Malachitowa 3") == "malachitowa 3, białystok"


def test_m_street_name_preserved():
    # Żadna ulica na „M" nie może zostać zredukowana do samego numeru.
    for addr, expect in [
        ("Mickiewicza 47", "mickiewicza 47, białystok"),
        ("Marczukowska 30", "marczukowska 30, białystok"),
        ("Marmurowa 3a", "marmurowa 3a, białystok"),
        ("Młynowa 60", "młynowa 60, białystok"),
        ("Magnoliowa 9", "magnoliowa 9, białystok"),
        ("Mieszka 11", "mieszka 11, białystok"),
        ("Mokra 11", "mokra 11, białystok"),
        ("Mroźna 8", "mroźna 8, białystok"),
    ]:
        assert k(addr) == expect, f"{addr} -> {k(addr)!r}"
        assert not _is_streetless_key(k(addr), CITY)


def test_apartment_markers_still_stripped():
    # Markery lokalu/mieszkania (zawsze z numerem) nadal usuwane.
    assert k("Lipowa 12 m 3") == "lipowa 12, białystok"
    assert k("Wiejska 55 m3") == "wiejska 55, białystok"
    assert k("Lipowa 12 m. 5") == "lipowa 12, białystok"
    assert k("Sienkiewicza 52 mieszkanie 7") == "sienkiewicza 52, białystok"
    assert k("Sienkiewicza 52 lokal 5") == "sienkiewicza 52, białystok"
    assert k("Lipowa 12 piętro 2") == "lipowa 12, białystok"


def test_leading_number_street_preserved():
    # „3 Maja" to realna ulica — numer wiodący nie może być potraktowany jak dom.
    assert k("3 Maja 5") == "3 maja 5, białystok"
    assert not _is_streetless_key(k("3 Maja 5"), CITY)


def test_non_m_streets_unaffected():
    assert k("Lipowa 12") == "lipowa 12, białystok"
    assert k("Sienkiewicza 52") == "sienkiewicza 52, białystok"
    assert k("Jana III Sobieskiego 6/44") == "jana iii sobieskiego 6, białystok"


def test_streetless_detection():
    assert _is_streetless_key("3, białystok", CITY) is True
    assert _is_streetless_key("47 białystok", CITY) is True
    assert _is_streetless_key("3a, białystok", CITY) is True
    assert _is_streetless_key("magazynowa 3, białystok", CITY) is False
    assert _is_streetless_key("3 maja 5, białystok", CITY) is False
