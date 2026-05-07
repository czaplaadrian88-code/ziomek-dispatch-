"""Parse pickup street + number out of corporate-account uwagi free-text.

Used for panel address_id=161 (Nadajesz.pl) where the actual pickup address is
embedded in uwagi rather than panel address fields. Pure / deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class ParsedPickup:
    street: str
    number: str
    company: Optional[str]
    raw_pickup_line: str
    confidence: float


# Locate keyword "Odbior" / "Odbierasz" (case-insensitive, polish chars).
_RE_ODBIOR_KEYWORD = re.compile(r"(?i)(odbi[oó]r|odbierasz)")

# Tail terminators that close pickup line (newline + delivery keyword).
_RE_TAIL_NEWLINE_DOSTAWA = re.compile(r"(?i)\r?\n\s*(dostawa|dor[eę]czenie)")

# Same-line narrative terminator (", doreczenie ..." or ", dostawa ...").
_RE_TAIL_INLINE = re.compile(r"(?i),\s*(dor[eę]czenie|dostawa)\b")

# Time prefixes (try in order: longest/most-specific first).
_RE_TIME_PREFIXES: Tuple[re.Pattern, ...] = (
    re.compile(r"^do\s+\d{1,2}:\d{2}\s*[:,]\s*"),
    re.compile(r"^do\s+\d{1,2}:\d{2}\s+"),
    re.compile(r"^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\s*:\s*"),
    re.compile(r"^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\s+"),
    re.compile(r"^\d{1,2}\s*-\s*\d{1,2}\s*:\s*"),
    re.compile(r"^\d{1,2}\s*-\s*\d{1,2}\s+"),
    re.compile(r"^\d{1,2}:\d{2}\s*:\s*"),
    re.compile(r"^\d{1,2}:\d{2}\s+"),
)

# Narrative connectors stripped from the front of pickup body.
_RE_NARRATIVE_CONNECTORS: Tuple[re.Pattern, ...] = (
    re.compile(r"(?i)^walizki\s+z\s+adresu\s+"),
    re.compile(r"(?i)^przesy[lł]ki\s+z\s+"),
    re.compile(r"(?i)^ze\s+sklepu\s+"),
    re.compile(r"(?i)^ze\s+"),
    re.compile(r"(?i)^z\s+"),
    re.compile(r"(?i)^do\s+"),
)

# Allowed alphabetic chars (Latin + extended ranges incl. Polish diacritics).
_LETTER_CLASS = r"A-Za-zÀ-ɏͰ-ϿЀ-ӿ"
_UPPER_LETTER_CLASS = r"A-ZÀ-ÞĀ-ɏͰ-ϿЀ-ӿ"

# P1 STRUCTURED: tight street + number per comma-token.
# Allows: "Mickiewicza 50", "Wyszynskiego 2/75", "Kijowska 7/lok.1",
# "Boruty 17", "Gen. Gustawa Orlicz-Dreszera 3 Lokal 1", "Wierzbowa 2/u12",
# "Mickiewicza 43C", "Mieszka I 1/51".
_RE_P1_STREET_NUMBER = re.compile(
    r"^(?:ul\.\s*|al\.\s*)?"
    r"(?P<street>[" + _UPPER_LETTER_CLASS + r"][" + _LETTER_CLASS + r"\.\-]*"
    r"(?:\s+[" + _LETTER_CLASS + r"\.\-]+){0,4}?)"
    r"\s+"
    r"(?P<num>\d+[A-Za-z]?(?:/\d+|/lok\.?\s*\d+|/u\d+|\s+Lokal\s+\d+)?)"
    r"\s*$"
)

# P2 NARRATIVE: looser scan inside whole pickup line.
_RE_P2_STREET_NUMBER = re.compile(
    r"(?:ul\.\s*|al\.\s*)?"
    r"(?P<street>[" + _LETTER_CLASS + r"\.\-]+"
    r"(?:\s+[" + _LETTER_CLASS + r"\.\-]+){0,3})"
    r"\s+"
    r"(?P<num>\d+[A-Za-z]?(?:/\d+)?)"
)

# Capitalized-words company candidate (no digits).
_RE_COMPANY_CANDIDATE = re.compile(
    r"^[" + _LETTER_CLASS + r"][" + _LETTER_CLASS + r"\s\.\-]*$"
)

# Used to test "must contain at least one alphabetic char".
_RE_HAS_LETTER = re.compile(r"[" + _LETTER_CLASS + r"]")

# Strip ul./al./gen. prefix (case-insensitive) for plausibility alpha-count.
_RE_STRIP_PREFIXES = re.compile(r"(?i)^(?:ul\.|al\.|gen\.)\s*")

# Strip ul./al. prefix (case-insensitive) for canonicalization.
_RE_CANONICAL_STRIP = re.compile(r"(?i)^(?:ul\.|al\.)\s*")


def _load_stoplist() -> frozenset:
    # Lazy import so tests can monkeypatch dispatch_v2.common.
    try:
        from dispatch_v2.common import UWAGI_PARSER_COMPANY_STOPLIST
    except ImportError:
        import os
        import sys
        sys.path.insert(
            0,
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        from dispatch_v2.common import UWAGI_PARSER_COMPANY_STOPLIST
    return UWAGI_PARSER_COMPANY_STOPLIST


def _extract_pickup_line(text: str) -> Optional[str]:
    # Locate first Odbior / Odbierasz keyword.
    m = _RE_ODBIOR_KEYWORD.search(text)
    if not m:
        return None
    body = text[m.end():]

    # Cut at newline + Dostawa / Doreczenie.
    cut1 = _RE_TAIL_NEWLINE_DOSTAWA.search(body)
    end_idx = cut1.start() if cut1 else len(body)

    # Cut at same-line narrative terminator earlier than newline cut.
    cut2 = _RE_TAIL_INLINE.search(body, 0, end_idx)
    if cut2:
        end_idx = cut2.start()

    line = body[:end_idx]

    # Strip leading separators (colon, space, tab).
    line = line.lstrip(": \t")

    # Strip leading time prefix (try each variant in priority order).
    for pat in _RE_TIME_PREFIXES:
        m2 = pat.match(line)
        if m2:
            line = line[m2.end():]
            break

    # Strip leading narrative connector (one round; they don't chain).
    for pat in _RE_NARRATIVE_CONNECTORS:
        m3 = pat.match(line)
        if m3:
            line = line[m3.end():]
            break

    return line.strip()


def _has_digit(s: str) -> bool:
    return any(ch.isdigit() for ch in s)


def _plausible(street: str, number: str, stoplist: frozenset) -> bool:
    if not number or not _has_digit(number):
        return False
    if street.casefold().strip() in stoplist:
        return False
    stripped = _RE_STRIP_PREFIXES.sub("", street)
    alpha_count = len(_RE_HAS_LETTER.findall(stripped))
    if alpha_count < 3:
        return False
    if not _RE_HAS_LETTER.search(street):
        return False
    return True


def _canonicalize_street(street: str) -> str:
    s = _RE_CANONICAL_STRIP.sub("", street).strip()
    # Title-case ONLY if entirely uppercase (preserve mixed case like
    # "Wyszynskiego" or "Gen. Gustawa Orlicz-Dreszera").
    if s and s == s.upper():
        s = " ".join(tok.capitalize() if tok else tok for tok in s.split(" "))
    return s


def _is_company_candidate(token: str, stoplist: frozenset) -> bool:
    t = token.strip().rstrip(".").strip()
    if not t:
        return False
    if _has_digit(t):
        return False
    if not _RE_COMPANY_CANDIDATE.match(t):
        return False
    if t.casefold() in stoplist:
        return False
    return True


def _looks_like_stoplisted(token: str, stoplist: frozenset) -> bool:
    t = token.strip().rstrip(".").strip().casefold()
    if t in stoplist:
        return True
    # Multi-token compound — strip trailing digit-suffix and re-check.
    no_trailing_num = re.sub(r"\s+\d+\S*\s*$", "", t).strip()
    if no_trailing_num and no_trailing_num in stoplist:
        return True
    # Token may carry trailing narrative ("Matka Polka Hybrydowa. dopytaj...")
    # — check if any stoplist entry is a word-boundary prefix of token.
    for entry in stoplist:
        if not entry:
            continue
        if t.startswith(entry):
            tail = t[len(entry):]
            if not tail or tail[0] in (" ", ".", ",", "\t"):
                return True
    return False


def parse_pickup_from_uwagi(text: Optional[str]) -> Optional[ParsedPickup]:
    """Pure function. Deterministic. No I/O. Returns None when not parseable."""
    if text is None or not isinstance(text, str) or not text.strip():
        return None

    stoplist = _load_stoplist()

    pickup_line = _extract_pickup_line(text)
    if pickup_line is None or not pickup_line.strip():
        return None

    raw_pickup_line = pickup_line

    # Step 2 — P1 STRUCTURED extraction.
    tokens = [t.strip() for t in pickup_line.split(",") if t.strip()]
    street_candidates: List[Tuple[str, str]] = []
    company_candidates: List[str] = []
    stoplisted_tokens: List[str] = []
    other_tokens: List[str] = []

    for tok in tokens:
        tok_clean = tok.rstrip(". ").strip()
        m = _RE_P1_STREET_NUMBER.match(tok_clean)
        if m:
            street_raw = m.group("street").strip()
            num_raw = m.group("num").strip()
            if _plausible(street_raw, num_raw, stoplist):
                street_candidates.append((street_raw, num_raw))
                continue
        if _looks_like_stoplisted(tok_clean, stoplist):
            stoplisted_tokens.append(tok_clean)
            continue
        if _is_company_candidate(tok_clean, stoplist):
            company_candidates.append(tok_clean)
            continue
        other_tokens.append(tok_clean)

    if street_candidates:
        street_raw, num_raw = street_candidates[0]
        street = _canonicalize_street(street_raw)
        company = company_candidates[0] if company_candidates else None
        only_one_street = len(street_candidates) == 1
        # 1.0 when exactly one street + (real company candidate found OR
        # no noise tokens). Stoplisted tokens don't count as "company
        # candidates" — they're filtered noise but still present.
        clean_other = len(other_tokens) == 0
        if only_one_street and (company is not None or clean_other):
            confidence = 1.0
        else:
            confidence = 0.8
        return ParsedPickup(
            street=street,
            number=num_raw,
            company=company,
            raw_pickup_line=raw_pickup_line,
            confidence=confidence,
        )

    # Step 3 — P2 NARRATIVE fallback.
    for m in _RE_P2_STREET_NUMBER.finditer(pickup_line):
        street_raw = m.group("street").strip()
        num_raw = m.group("num").strip()
        if _plausible(street_raw, num_raw, stoplist):
            street = _canonicalize_street(street_raw)
            return ParsedPickup(
                street=street,
                number=num_raw,
                company=None,
                raw_pickup_line=raw_pickup_line,
                confidence=0.5,
            )

    return None
