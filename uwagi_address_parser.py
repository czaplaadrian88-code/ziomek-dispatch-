import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from dispatch_v2.uwagi_bridge_envelope import verify_bridge_envelope

# Regex patterns compiled at module level for speed
# Pattern to extract pickup line from uwagi text.
# Pickup line starts after "Odbiór"/"Odbierasz", ends przy markerze DOSTAWY
# ("Dostawa"/"Doręczenie"/"doręcz...") poprzedzonym przecinkiem LUB nową linią,
# albo na końcu stringa.
# 2026-05-21 (fix bug P2): wcześniej granica wymagała `\r?\n` przed markerem →
# narracja inline "Odbiór ze sklepu X, ul. Y 64, doręczenie do Z, Zambrowska 86"
# NIE była ucinana → cała linia (z ulicą DOSTAWY) wpadała do P1, które brało
# ostatnie dopasowanie ulicy = adres dostawy zamiast odbioru. Teraz `[,\r\n]`
# łapie też przecinek inline. Sam przecinek (separator ulica/firma) NIE ucina —
# tylko przecinek/newline BEZPOŚREDNIO przed markerem dostawy.
_PICKUP_LINE_PATTERN = re.compile(
    r'(?:Odbiór|Odbierasz)\s*[:\-]?\s*(.*?)(?:[,\r\n]\s*(?:Dostawa|Doręcz|dostawa|doręcz)|$)',
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

# P1 strict pattern for street+number in a token.
# 2026-06-08 (FAZA 2 item 6): opcjonalny numer wiodący ulicy — „3 Maja",
# „11 Listopada", „26 Kwietnia" (realne ulice). Wcześniej street wymagał startu
# od WIELKIEJ litery → „3" było gubione, „3 Maja 5" → street=„Maja" (zły geocode).
_P1_STREET_NUMBER_PATTERN = re.compile(
    r'^\s*(?:ul\.\s*|al\.\s*)?'
    r'((?:\d{1,3}\s+)?[A-ZŁŚĆŹŻ][A-Za-złśćźżóęąńĄĘĆŁŃŚŻŹÓ\.\-\s]{2,40}?)'
    r'\s+(\d+[A-Za-z]?(?:/\d+|/lok\.\s*\d+|\s+Lokal\s+\d+)?)\s*$'
)

# P2 narrative fallback pattern (też z opcjonalnym numerem wiodącym).
_P2_NARRATIVE_PATTERN = re.compile(
    r'(?:ul\.\s*|al\.\s*)?'
    r'((?:\d{1,3}\s+)?[A-ZŁŚĆŹŻa-złśćźżóęąńĄĘĆŁŃŚŻŹÓ\.\-]+(?:\s+[A-ZŁŚĆŹŻa-złśćźżóęąńĄĘĆŁŃŚŻŹÓ\.\-]+){0,3})'
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
    city: Optional[str] = None  # bridge-NADAWCA: miasto z kodu pocztowego (np. "Białystok")


@dataclass(frozen=True)
class BridgeNadawcaAttempt:
    """Czysty wynik walidacji koperty mostu, także dla odrzuceń."""

    pickup: Optional[ParsedPickup]
    reason: str
    envelope_seen: bool = False
    version: Optional[int] = None


# --- Format mostu epaki (verbose_uwagi, drtusz_bridge/bridge.py) -----------------
# `<Firma> #<id> | NADAWCA: <imię> tel <tel> | <firma>, [NIP x,] <ulica nr>,
#  <kod miasto>, [<email>] | Odbiorca: ... | oryg. adres: <ADRES DORĘCZENIA> | ...`
# Punkt ODBIORU = adres NADAWCY (segment NADAWCA); pickup_rules mostu ustawiają
# tylko CZAS (czasówka pushowana przy tworzeniu). "oryg. adres" = doręczenie,
# NIE odbiór. Proweniencja = poprawny terminalny HMAC koperty v2.
_BRIDGE_NADAWCA_SEGMENT_PATTERN = re.compile(r"^NADAWCA:\s*")
_BRIDGE_ODBIORCA_SEGMENT_PATTERN = re.compile(r"^Odbiorca:\s*")
_BRIDGE_POSTAL_TOKEN_PATTERN = re.compile(r"(?<!\d)\d{2}-\d{3}(?!\d)")
_BRIDGE_ADDR_ANCHOR_PATTERN = re.compile(
    r"(?P<addr>[^,|]+),\s*(?P<zip>\d{2}-\d{3})\s+(?P<city>[^,|]+?)\s*(?:,|\|)"
)
_BRIDGE_STREET_NUMBER_PATTERN = re.compile(
    r"^(?P<street>.+?)\s+(?P<number>\d+[a-zA-Z]?(?:/\d+\w*)?)(?:\s+(?P<extra>.+))?$"
)
_BRIDGE_ADDRESS_EXTRA_PATTERN = re.compile(
    r"(?:"
    r"(?:lok\.?|lokal)\s+\S+"
    r"|bud(?:ynek)?\.?\s+\S+"
    r"|m\.?\s*\d+"
    r")",
    re.IGNORECASE,
)


def _load_company_stoplist() -> frozenset:
    """Załaduj kanoniczną stoplistę bez I/O i zachowaj testowy fallback."""
    try:
        from dispatch_v2.common import UWAGI_PARSER_COMPANY_STOPLIST
        return UWAGI_PARSER_COMPANY_STOPLIST
    except ImportError:
        return _DEFAULT_STOPLIST


def _try_bridge_nadawca(
    text: str,
    stoplist: frozenset,
    hmac_material: Optional[bytes],
    expected_order_id: object = None,
    now: Optional[float] = None,
    max_age_seconds: Optional[float] = None,
) -> BridgeNadawcaAttempt:
    """P0 bridge-NADAWCA: waliduj kopertę, potem adres nadawcy.

    Każda niejednoznaczność odrzuca całą próbę P0. ``reason`` jest stabilnym,
    czystym diagnostykiem; wywołujący może odróżnić przyszłą wersję formatu od
    braku proweniencji bez logowania ani I/O w parserze. Anti-replay
    (``order_id_mismatch``/``envelope_expired``) egzekwuje koperta.
    """
    verification = verify_bridge_envelope(
        text,
        hmac_material,
        expected_order_id=expected_order_id,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    if not verification.authenticated or verification.payload is None:
        return BridgeNadawcaAttempt(
            None,
            verification.reason,
            verification.envelope_seen,
            verification.version,
        )

    # Liczenie na surowym payloadzie, zanim split/normalizacja zmieni strukturę.
    # Chroni także `NADAWCA: NADAWCA:` w jednym segmencie.
    raw_nadawca_count = verification.payload.count("NADAWCA:")
    if raw_nadawca_count != 1:
        return BridgeNadawcaAttempt(
            None,
            f"raw_nadawca_prefix_count:{raw_nadawca_count}",
            True,
            verification.version,
        )

    segments = verification.payload.split("|")
    marker_index = len(segments)

    nadawca_indices = [
        index
        for index, segment in enumerate(segments)
        if _BRIDGE_NADAWCA_SEGMENT_PATTERN.match(segment.strip())
    ]
    if len(nadawca_indices) != 1:
        return BridgeNadawcaAttempt(
            None, f"nadawca_segment_count:{len(nadawca_indices)}", True,
            verification.version,
        )
    odbiorca_indices = [
        index
        for index, segment in enumerate(segments)
        if _BRIDGE_ODBIORCA_SEGMENT_PATTERN.match(segment.strip())
    ]
    if len(odbiorca_indices) != 1:
        return BridgeNadawcaAttempt(
            None, f"odbiorca_boundary_count:{len(odbiorca_indices)}", True,
            verification.version,
        )

    nadawca_index = nadawca_indices[0]
    odbiorca_index = odbiorca_indices[0]
    if nadawca_index == 0:
        return BridgeNadawcaAttempt(
            None, "nadawca_segment_not_delimited", True, verification.version
        )
    if not nadawca_index < odbiorca_index < marker_index:
        return BridgeNadawcaAttempt(
            None, "odbiorca_boundary_order", True, verification.version
        )

    sender_parts = segments[nadawca_index:odbiorca_index]
    sender_parts[0] = _BRIDGE_NADAWCA_SEGMENT_PATTERN.sub(
        "", sender_parts[0].strip(), count=1
    )
    sender_segment = "|".join(sender_parts)
    postal_tokens = list(_BRIDGE_POSTAL_TOKEN_PATTERN.finditer(sender_segment))
    anchors = list(
        _BRIDGE_ADDR_ANCHOR_PATTERN.finditer(sender_segment + "|")
    )
    if len(postal_tokens) != 1 or len(anchors) != 1:
        return BridgeNadawcaAttempt(
            None,
            f"postal_anchor_count:{max(len(postal_tokens), len(anchors))}",
            True,
            verification.version,
        )

    am = anchors[0]
    addr = _normalize(am.group("addr"))
    city = _normalize(am.group("city"))
    sn = _BRIDGE_STREET_NUMBER_PATTERN.fullmatch(addr)
    if not sn:
        return BridgeNadawcaAttempt(None, "address_shape", True, verification.version)
    street = _canonicalize_street(sn.group("street"))
    if not _is_plausible_street(street, stoplist):
        return BridgeNadawcaAttempt(
            None, "street_not_plausible", True, verification.version
        )
    number = sn.group("number")
    extra = sn.group("extra")
    if extra:
        extra = " ".join(_normalize(extra).split())
        if not _BRIDGE_ADDRESS_EXTRA_PATTERN.fullmatch(extra):
            return BridgeNadawcaAttempt(
                None, "unsupported_address_extra", True, verification.version
            )
        number = f"{number} {extra}"
    # firma nadawcy: pierwszy token przecinkowy segmentu meta (po `| `),
    # tylko display — bez walidacji plauzybilności firmy
    company = None
    meta = sender_segment.split("|")[-1]
    first_tok = _normalize(meta.split(",")[0])
    if first_tok and not first_tok.lower().startswith("nip "):
        company = first_tok
    return BridgeNadawcaAttempt(
        ParsedPickup(
            street=street,
            number=number,
            company=company,
            raw_pickup_line=f"{addr}, {am.group('zip')} {city}",
            confidence=0.95,
            city=city or None,
        ),
        "parsed_v2",
        True,
        verification.version,
    )


def inspect_bridge_nadawca(
    text: Optional[str],
    hmac_material: Optional[bytes] = None,
    *,
    expected_order_id: object = None,
    now: Optional[float] = None,
    max_age_seconds: Optional[float] = None,
) -> BridgeNadawcaAttempt:
    """Publiczna, czysta diagnostyka koperty bridge-NADAWCA."""
    if not text or not text.strip():
        return BridgeNadawcaAttempt(None, "empty_text")
    return _try_bridge_nadawca(
        text,
        _load_company_stoplist(),
        hmac_material,
        expected_order_id=expected_order_id,
        now=now,
        max_age_seconds=max_age_seconds,
    )


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


def _is_plausible_company(token: str) -> bool:
    """Czy nie-uliczny token wygląda jak nazwa firmy (a nie narracja/śmieć).

    Firma: ≤3 słowa i zaczyna się wielką literą. Narracja typu „za szlabanem
    20 m schody po lewej." (zaczyna małą literą / wiele słów) = śmieć, nie firma.
    """
    if not token:
        return False
    words = token.split()
    if len(words) > 3:
        return False
    if len(token) >= 50:
        return False
    if token.replace(' ', '').isdigit():
        return False
    first_alpha = next((c for c in token if c.isalpha()), '')
    if first_alpha and first_alpha.islower():
        return False
    return True


def _try_p1(pickup_line: str, stoplist: frozenset) -> Optional[ParsedPickup]:
    """Try strict P1 extraction.

    2026-05-21 (Adrian): firmy ze stoplisty są NULLOWANE (company=None) — nie
    raportujemy ich jako company (i tak nie są realną nazwą lokalu, mieszają
    `_restaurant_override`). Stoplisted token nadal LICZY się jako „rozpoznana
    firma" do confidence (czysty strukturalny pickup); leftover narracyjny śmieć
    (nie-uliczny, nie-stoplisted, nie-company-like) obniża confidence do 0.8.
    """
    tokens = [t.strip() for t in pickup_line.split(',')]
    street = None
    number = None
    company = None
    had_known_company = False   # token ze stoplisty (rozpoznana firma — nullowana)
    had_extra = False           # leftover nie-uliczny token nierozpoznany (śmieć)
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
        # Non-street token.
        norm = _normalize(token).lower()
        if norm in stoplist:
            # Firma ze stoplisty → NULL company, ale rozpoznana (clean).
            had_known_company = True
            continue
        # Nie-uliczny, nie-stoplisted: realna firma vs narracyjny śmieć.
        if company is None and _is_plausible_company(token):
            company = token
        else:
            had_extra = True
    if street is not None and number is not None:
        if had_extra:
            confidence = 0.8
        elif company is not None or had_known_company:
            confidence = 1.0
        else:
            confidence = 0.8
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


def parse_pickup_from_uwagi(text: Optional[str],
                            bridge_format: bool = False,
                            bridge_hmac_material: Optional[bytes] = None,
                            *,
                            expected_order_id: object = None,
                            now: Optional[float] = None,
                            max_age_seconds: Optional[float] = None,
                            ) -> Optional[ParsedPickup]:
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
    - 0.95: P0 bridge-NADAWCA (verbose_uwagi mostu epaki; bridge_format=True)
    - 0.8: P1 structured without company OR ambiguous order
    - 0.5: P2 narrative regex extraction
    - 0.0: fail (returns None)

    bridge_format: gałąź P0 dla verbose_uwagi mostu epaki. Call-site podaje też
    zweryfikowany odczytem pliku 0600 ``bridge_hmac_material``; parser nie robi
    I/O i weryfikuje podpis przed składnią. False = zachowanie legacy.
    """
    if not text or not text.strip():
        return None

    # Lazy import stoplist to allow test override.
    stoplist = _load_company_stoplist()

    if bridge_format:
        attempt = _try_bridge_nadawca(
            text,
            stoplist,
            bridge_hmac_material,
            expected_order_id=expected_order_id,
            now=now,
            max_age_seconds=max_age_seconds,
        )
        if attempt.pickup is not None:
            return attempt.pickup
        if attempt.envelope_seen:
            return None

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
