"""Czysty kanon pisowni ulic i budynkowego klucza adresowego.

Moduł nie czyta flag, plików ani zegara.  Jest wspólnym, lekkim ownerem dla
geokodowania oraz fizycznego punktu odbioru; konsumenci mogą go bezpiecznie
używać w gorącej pętli i w warstwach renderujących.
"""
from __future__ import annotations

import re


# Jedyny rejestr równoważnych pisowni ulic. Klucz i wartość opisują wyłącznie
# część uliczną, bez numeru domu. Wartości zachowują oficjalną tożsamość ulicy.
STREET_ALIASES = {
    # Marii Skłodowskiej-Curie variants
    "skłodowskiej": "skłodowskiej-curie marii",
    "skłodowskiej-curie": "skłodowskiej-curie marii",
    "curie-skłodowskiej": "skłodowskiej-curie marii",
    "marii curie-skłodowskiej": "skłodowskiej-curie marii",
    "marii skłodowskiej-curie": "skłodowskiej-curie marii",
    "m. skłodowskiej-curie": "skłodowskiej-curie marii",
    "m. curie-skłodowskiej": "skłodowskiej-curie marii",
    # Władysława Bełzy variants
    "bełzy": "władysława bełzy",
    "wł. bełzy": "władysława bełzy",
    "władysława bełzy": "władysława bełzy",
    # Feliksa Filipowicza variants
    "filipowicza": "feliksa filipowicza",
    "f. filipowicza": "feliksa filipowicza",
    "feliksa filipowicza": "feliksa filipowicza",
    # Aleja i Plac Jana Pawła II są różnymi obiektami.
    "jana pawła ii": "aleja jana pawła ii",
    "aleja jana pawła ii": "aleja jana pawła ii",
    "pl. jana pawła ii": "plac jana pawła ii",
    "plac jana pawła ii": "plac jana pawła ii",
    # Jeden fizyczny adres z case 491870: wariant skrócony i oficjalny.
    "kilińskiego": "jana kilińskiego",
    "jana kilińskiego": "jana kilińskiego",
}


# Marker lokalu jest ważny tylko wtedy, gdy bezpośrednio prowadzi do CYFRY.
# To wymaganie jest kotwicą NEW-1: gołe ``m`` przed literą w ``Miłosza`` albo
# ``Matejki`` nigdy nie może uruchomić końcowego ``.*$``.
UNIT_SUFFIX_RE = re.compile(
    r"(?:[/,]\s*|\s+)"
    r"(?:lok(?:al)?|mieszkanie|pi[eę]tro|m)"
    r"\.?\s*"
    r"(?P<unit_number>\d+[a-z-]*)\b.*$",
    re.IGNORECASE,
)

_POSTAL_CODE_RE = re.compile(r"\b\d{2}-\d{3}\b")
_LEADING_UL_RE = re.compile(r"^(?:ul(?:ica)?\.?\s+)")
_SLASH_UNIT_RE = re.compile(r"/[^\s]+$")
_HOUSE_SUFFIX_RE = re.compile(r"^(.*?)(\d+[a-z]?)$")
_NON_IDENTITY_PREFIXES = ("ul. ", "ul ", "ulica ", "al. ", "al ", "aleja ")


def strip_unit_suffix(text: str) -> str:
    """Usuń końcowy lokal/piętro, ale wyłącznie marker związany z cyfrą."""
    if not text or not isinstance(text, str):
        return text
    return UNIT_SUFFIX_RE.sub("", text)


def normalize_street_for_matching(address_lower: str) -> str:
    """Zastosuj alias do części ulicznej, zachowując numeryczny sufiks."""
    if not address_lower:
        return address_lower
    digit_index = next(
        (index for index, char in enumerate(address_lower) if char.isdigit()),
        None,
    )
    if digit_index is None:
        street = address_lower.strip().rstrip(",.")
        suffix = ""
    else:
        space_before_digit = address_lower.rfind(" ", 0, digit_index)
        if space_before_digit < 0:
            street = address_lower[:digit_index].strip().rstrip(",.")
            suffix = address_lower[digit_index:]
        else:
            street = address_lower[:space_before_digit].strip().rstrip(",.")
            suffix = address_lower[space_before_digit:]
    canonical = STREET_ALIASES.get(street)
    return f"{canonical}{suffix}" if canonical is not None else address_lower


def canonicalize_street_address(address: str) -> str:
    """Zwróć jedną zarejestrowaną pisownię ulicy z niezmienionym sufiksem."""
    if not address or not isinstance(address, str):
        return address
    cleaned = " ".join(address.strip().split())
    lowered = cleaned.lower()
    candidates = [lowered]
    for prefix in _NON_IDENTITY_PREFIXES:
        if lowered.startswith(prefix):
            candidates.append(lowered[len(prefix):])
            break

    for candidate in candidates:
        normalized = normalize_street_for_matching(candidate)
        digit_index = next(
            (index for index, char in enumerate(candidate) if char.isdigit()),
            None,
        )
        if digit_index is None:
            street = candidate.strip().rstrip(",.")
        else:
            space_index = candidate.rfind(" ", 0, digit_index)
            end = space_index if space_index >= 0 else digit_index
            street = candidate[:end].strip().rstrip(",.")
        if street in STREET_ALIASES:
            return normalized
    return cleaned


def normalize_physical_building(address: str, city: str = "") -> str:
    """Znormalizuj pickup do budynku: ulica+numer, bez lokalu i GPS."""
    if not address:
        return ""
    text = " ".join(str(address).strip().casefold().split())
    text = " ".join(_POSTAL_CODE_RE.sub("", text).split())
    text = _LEADING_UL_RE.sub("", text)
    text = strip_unit_suffix(text)
    text = _SLASH_UNIT_RE.sub("", text)
    text = " ".join(text.strip(" ,./").split())
    if _HOUSE_SUFFIX_RE.match(text):
        text = canonicalize_street_address(text)
    city_part = " ".join(str(city or "").strip().casefold().split())
    return f"{text}|{city_part}" if city_part else text
