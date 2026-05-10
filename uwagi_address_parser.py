import re
import unicodedata
from dataclasses import dataclass
from typing import Optional, Tuple

# Regex patterns compiled at module level for speed
# Pattern to extract pickup line from uwagi text
# Pickup line starts after "Odbiór" or "Odbierasz" keyword, ends at
# "\r\n" + "Dostawa:" / "Doręczenie" / "doręczenie" or end of string
_PICKUP_LINE_PATTERN = re.compile(
    r'(?:Odbiór|Odbierasz)\s*[:\-]?\s*(.*?)(?:\r?\n\s*(?:Dostawa:|Doręczenie|doręczenie)|$)',
    re.DOTALL | re.IGNORECASE
)

# Time prefix patterns to strip from beginning of pickup line
_TIME_PREFIX_PATTERN = re.compile(
    r'^(?:\d{1,2}:\d{2}(?::\d{2})?\s*[:\-]?\s*'  # HH:MM or HH:MM:SS
    r'|\d{1,2}:\d{2}-\d{1,2}:\d{2}\s*[:\-]?\s*'  # HH:MM-HH:MM
    r'|\d{1,2}-\d{1,2}\s*[:\-]?\s*'               # HH-HH
    r'|do\s+\d{1,2}:\d{2}\s*[:\-]?\s*)'           # do HH:MM
)

# Narrative connectors to strip after time prefix
_NARRATIVE_PREFIX_PATTERN = re.compile(
    r'^(?:z\s+|ze\s+|ze\s+sklepu\s+|przesyłki\s+z\s+|walizki\s+z\s+adresu\s+)',
    re.IGNORECASE
)

# P1 strict pattern for street+number in a token
_P1_STREET_NUMBER_PATTERN = re.compile(
    r'^\s*(?:ul\.\s*|al\.\s*)?'
    r'([A-ZŁŚĆŹŻ][A-Za-złśćźżóęąńĄĘĆŁŃŚŻŹÓ\.\-\s]{2,40}?)'
    r'\s+(\d+[A-Za-z]?(?:/\d+|/lok\.\s*\d+|\s+Lokal\s+\d+)?)\s*$'
)

# P2 narrative fallback pattern
_P2_NARRATIVE_PATTERN = re.compile(
    r'(?:ul\.\s*|al\.\s*)?'
    r'([A-ZŁŚĆŹŻa-złśćźżóęąńĄĘĆŁŃŚŻŹÓ\.\-]+(?:\s+[A-ZŁŚĆŹŻa-złśćźżóęąńĄĘĆŁŃŚŻŹÓ\.\-]+){0,3})'
    r'\s+(\d+[A-Za-z]?(?:/\d+)?)'
)

# Stop-list for company names (will be imported lazily)
# We'll define a default minimal set; actual stop-list from common module
_DEFAULT_STOPLIST = frozenset({
    "drtusz", "dzielne zuchy", "mali wojownicy", "matka polka hybrydowa",
    "7 kick", "drapieżnik", "kick", "zuchy", "wojownicy", "polka",
    "hybrydowa",
})


@dataclass(frozen=True)
class ParsedPickup:
    street: str           # canonical: "Wyszyńskiego" or "Gen. Stanisława Maczka"
    number: str           # canonical: "2/75" or "43C" or "3 Lokal 1"
    company: Optional[str]    # if extractable, e.g. "Drtusz"
    raw_pickup_line: str  # for audit trail
    confidence: float     # 0.0-1.0


def _normalize(text: str) -> str:
    """Normalize unicode characters (NFKD) and strip."""
    return unicodedata.normalize('NFKD', text).strip()


def _is_plausible_street(street: str, stoplist: frozenset) -> bool:
    """Check plausibility of a street candidate."""
    if not street:
        return False
    # Must contain at least one letter (not purely numeric)
    if not any(c.isalpha() for c in street):
        return False
    # Normalize for stoplist check
    norm = _normalize(street).lower()
    if norm in stoplist:
        return False
    # Strip common prefixes for length check
    stripped = norm
    for prefix in ("ul. ", "al. ", "gen. "):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    if len(stripped) < 3:
        return False
    return True


def _canonicalize_street(street: str) -> str:
    """Apply canonicalization rules."""
    s = street.strip()
    # Strip "ul. " or "al. " prefix (preserve "Gen. ")
    if s.lower().startswith("ul. "):
        s = s[4:]
    elif s.lower().startswith("al. "):
        s = s[4:]
    # Title-case if all-uppercase (preserve mixed case)
    if s.isupper():
        s = s.title()
    return s


def _extract_pickup_line(text: str) -> Optional[str]:
    """Extract the pickup line from uwagi text."""
    m = _PICKUP_LINE_PATTERN.search(text)
    if not m:
        return None
    line = m.group(1).strip()
    if not line:
        return None
    # Strip time prefix
    line = _TIME_PREFIX_PATTERN.sub('', line).strip()
    # Strip narrative connectors
    line = _NARRATIVE_PREFIX_PATTERN.sub('', line).strip()
    return line


def _try_p1(pickup_line: str, stoplist: frozenset) -> Optional[ParsedPickup]:
    """Try strict P1 extraction."""
    tokens = [t.strip() for t in pickup_line.split(',')]
    street = None
    number = None
    company = None
    for token in tokens:
        if not token:
            continue
        m = _P1_STREET_NUMBER_PATTERN.match(token)
        if m:
            cand_street = m.group(1).strip()
            cand_number = m.group(2).strip()
            if _is_plausible_street(cand_street, stoplist):
                street = cand_street
                number = cand_number
                continue
        # Not a street+number token -> potential company
        # Only consider if it's a single word or short phrase
        if company is None:
            # Simple heuristic: if token is not too long and not a number
            if len(token) < 50 and not token.replace(' ', '').isdigit():
                company = token
    if street is not None and number is not None:
        confidence = 1.0 if company is not None else 0.8
        return ParsedPickup(
            street=_canonicalize_street(street),
            number=number,
            company=company,
            raw_pickup_line=pickup_line,
            confidence=confidence,
        )
    return None


def _try_p2(pickup_line: str, stoplist: frozenset) -> Optional[ParsedPickup]:
    """Try P2 narrative fallback."""
    m = _P2_NARRATIVE_PATTERN.search(pickup_line)
    if not m:
        return None
    cand_street = m.group(1).strip()
    cand_number = m.group(2).strip()
    if not _is_plausible_street(cand_street, stoplist):
        return None
    return ParsedPickup(
        street=_canonicalize_street(cand_street),
        number=cand_number,
        company=None,
        raw_pickup_line=pickup_line,
        confidence=0.5,
    )


def parse_pickup_from_uwagi(text: Optional[str]) -> Optional[ParsedPickup]:
    """
    Pure function. No I/O. Deterministic.

    Returns ParsedPickup or None.

    None when:
    - text is None / empty / whitespace
    - no pickup line extractable
    - pickup line contains only company name (P3 edge — defense gate path)
    - extracted street fails plausibility (no digit, in stop-list, etc)

    Confidence:
    - 1.0: P1 structured, time prefix + clear "STREET NUM, COMPANY"
    - 0.8: P1 structured without company OR ambiguous order
    - 0.5: P2 narrative regex extraction
    - 0.0: fail (returns None)
    """
    if not text or not text.strip():
        return None

    # Lazy import stoplist to allow test override
    try:
        from dispatch_v2.common import UWAGI_PARSER_COMPANY_STOPLIST
        stoplist = UWAGI_PARSER_COMPANY_STOPLIST
    except ImportError:
        stoplist = _DEFAULT_STOPLIST

    pickup_line = _extract_pickup_line(text)
    if not pickup_line:
        return None

    # Try P1 first
    result = _try_p1(pickup_line, stoplist)
    if result is not None:
        return result

    # Try P2
    result = _try_p2(pickup_line, stoplist)
    if result is not None:
        return result

    return None
