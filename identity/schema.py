"""Canonical courier record schema (Z-P1-05 Faza A).

CID is the immutable canonical key, stored as ``str`` (JSON holds int,
courier_api.db holds TEXT — we normalize to str at the boundary). Aliases and
full names are versioned per source so the same person never fragments into
several entities.

PIN is treated as a secret: a record NEVER stores the plaintext PIN, only
``pin_present`` and ``pin_last2`` (last two digits) for reports/diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = [
    "SOURCE_LABELS",
    "COORDINATOR_CIDS",
    "PIN_LENGTH",
    "ROOT_VALIDATORS",
    "TIERS_META_KEY",
    "CourierRecord",
    "canon_cid",
    "canonical_courier_id",
    "canonical_numeric_cid",
    "courier_names_record_issue",
    "full_names_record_issue",
    "grafik_record_issue",
    "ids_record_issue",
    "pin_last2",
    "piny_record_issue",
    "tiers_record_issue",
    "validate_courier_ids_store",
    "validate_courier_names_root",
    "validate_courier_tiers_root",
    "validate_kurier_full_names_root",
    "validate_kurier_ids_root",
    "validate_kurier_piny_root",
    "validate_record",
    "validate_root",
]

# Alias provenance buckets (brief: panel/gps/grafik/app/accounting) + "ids" =
# the raw kurier_ids alias registry (the union that actually drives resolution).
SOURCE_LABELS = ("ids", "panel", "gps", "grafik", "app", "accounting")

# Virtual / non-courier identities. cid 26 "Koordynator" has no courier_tiers
# entry; observability/data_alerts.py excludes "26" by default.
COORDINATOR_CIDS = frozenset({"26"})


def canon_cid(cid) -> str:
    """Canonical cid as str. ``26`` / ``"26"`` / ``26.0`` -> ``"26"``."""
    if cid is None:
        return ""
    if isinstance(cid, bool):  # guard: bool is an int subclass
        return str(cid)
    if isinstance(cid, float) and cid.is_integer():
        return str(int(cid))
    return str(cid).strip()


def canonical_courier_id(value) -> Optional[str]:
    """Normalize the shared JSON CID scalar or reject a malformed value.

    ``bool`` is rejected explicitly: it is an ``int`` subclass, so ``int(True)``
    would otherwise mint CID 1 out of a flag.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = str(value).strip()
    return normalized or None


def canonical_numeric_cid(value) -> Optional[str]:
    """Canonical CID restricted to a positive decimal integer, or ``None``.

    Stricter sibling of :func:`canonical_courier_id`, for the *writer* side
    (onboarding / grafik) that mints new roster rows. ``canonical_courier_id``
    only rejects ``bool`` and non ``str``/``int`` scalars, so ``-5``, ``"017"``,
    ``"+17"``, ``"1_7"`` or ``" 17 "`` would still pass — yet an int()-based
    reader (``manual_overrides._all_name_to_cid``, ``courier_availability``)
    coerces every one of those into a *different* identity. A writer must never
    persist a form that a reader would read as a different CID, so here the
    accepted set is exactly the canonical decimal integer:

    ``str(value)`` must be all ASCII digits, round-trip through ``int``
    (``str(int(s)) == s`` rejects leading zeros) and be strictly positive
    (``int(s) > 0`` rejects ``0`` / ``"0"`` — no real courier owns CID 0, and the
    canonical reader ``courier_availability._canon_cid(0)`` raises because ``0``
    is falsy, so a persisted 0 would be a writer-only identity no reader accepts).
    ``bool`` stays rejected (it is an ``int`` subclass), ``float`` / ``None`` /
    other types are rejected. Returns the canonical decimal string, or ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        s = str(value)
    except (ValueError, OverflowError):  # int beyond int_max_str_digits
        return None
    if not s.isascii() or not s.isdigit():  # rejects sign/space/underscore, unicode digits
        return None
    try:
        canonical = str(int(s))
    except (ValueError, OverflowError):  # digit string beyond int_max_str_digits
        return None
    return s if (canonical == s and int(s) > 0) else None  # rejects leading zeros AND 0/non-positive


def validate_courier_ids_store(store) -> Dict:
    """Validate the complete name->CID generation used for authorization.

    Authorization reads one *generation* of the roster, never a subset: a
    consumer that skips an unrelated malformed row keeps answering from a
    roster nobody is allowed to trust. Raises ``ValueError`` so the caller
    fails the whole generation closed.
    """
    if not isinstance(store, dict):
        raise ValueError("courier ID store is not a mapping")
    for name, cid in store.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or canonical_courier_id(cid) is None
        ):
            raise ValueError("courier ID store contains a malformed record")
    return store


# --------------------------------------------------------------------------- #
# Schemat PIĘCIU rootów generacji rostera (A-6/G5, K5)
# --------------------------------------------------------------------------- #
# ``courier_admin.add_new_courier`` zmienia pięć plików JAKO JEDNĄ generację, a
# ``identity.journal`` musi umieć powiedzieć — i przy zapisie, i przy odtwarzaniu
# po crashu — czy dany root jest zdrowy. Lekcja K5 (bryła V12): walidowano tam
# tylko PIN+KDF, więc każdy inny root wracał z backupu bez sprawdzenia. Reguły
# kształtu rootów mieszkają WYŁĄCZNIE tutaj (jeden owner kontraktu); journal je
# tylko woła przez ``validate_root``.
#
# Każdy walidator: zwraca store gdy zdrowy, rzuca ``ValueError`` gdy nie.
# Komunikaty NIGDY nie zawierają nazwisk ani PIN-ów (parytet z raportami A-6).

PIN_LENGTH = 4  # forma mintowana przez _generate_unique_pin (1000..9999)


def _as_mapping(store, root: str) -> Dict:
    if not isinstance(store, dict):
        raise ValueError(f"{root} nie jest mapą")
    return store


def validate_kurier_ids_root(store) -> Dict:
    """``kurier_ids.json`` = ``{name: cid}`` — rejestr autoryzacyjny.

    Deleguje do :func:`validate_courier_ids_store` (istniejący owner tej samej
    prawdy — celowo bez drugiej kopii reguł), a NA WIERZCHU dokłada regułę
    PISARZA: każdy rekord musi mieć CID kanoniczny liczbowo
    (:func:`canonical_numeric_cid`).

    Dlaczego warstwa, a nie zmiana delegata: ``validate_courier_ids_store`` jest
    kontraktem AUTORYZACJI (``courier_info`` — czytelnik całej generacji) i używa
    luźnego ``canonical_courier_id``; jego zaostrzenie to osobna bramka. Tutaj
    natomiast jesteśmy w ścieżce PISARZA (onboarding + recovery, które utrwala
    bajty z backupu), a docstring ``canonical_numeric_cid`` mówi wprost, że
    ``-5``, ``"017"``, ``" 17 "``, ``"1_7"`` czyta reader oparty o ``int()`` jako
    INNĄ tożsamość — więc pisarz nie może ich utrwalić. Bez tej warstwy root
    rządzący autoryzacją miał najsłabszą regułę z całej piątki (``courier_names``
    i ``courier_tiers`` odrzucały tę samą klasę defektu). Wykonalność zmierzona
    na żywych danych: 125/125 wpisów ``kurier_ids`` przechodzi już dziś.
    """
    validate_courier_ids_store(_as_mapping(store, "kurier_ids"))
    for _name, cid in store.items():
        if canonical_numeric_cid(cid) is None:
            raise ValueError("kurier_ids ma rekord z niekanonicznym liczbowo CID")
    return store


def validate_kurier_piny_root(store) -> Dict:
    """``kurier_piny.json`` = ``{pin: alias}``.

    PIN musi mieć dokładnie formę, którą mintuje onboarding i której szuka
    ``pin_auth.resolve_pin`` (dokładne trafienie klucza): ``PIN_LENGTH`` cyfr
    ASCII. Alias — niepusty string (to on wiąże PIN z tożsamością).
    """
    for pin, alias in _as_mapping(store, "kurier_piny").items():
        if (
            not isinstance(pin, str)
            or len(pin) != PIN_LENGTH
            or not pin.isascii()
            or not pin.isdigit()
        ):
            raise ValueError("kurier_piny ma klucz PIN spoza formy 4 cyfr ASCII")
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("kurier_piny ma PIN bez aliasu tożsamości")
    return store


def validate_courier_tiers_root(store) -> Dict:
    """``courier_tiers.json`` = ``{cid: {...}}`` + opcjonalny blok ``_meta``."""
    for cid, row in _as_mapping(store, "courier_tiers").items():
        if cid == "_meta":
            if not isinstance(row, dict):
                raise ValueError("courier_tiers: blok _meta nie jest obiektem")
            continue
        if canonical_numeric_cid(cid) is None:
            raise ValueError("courier_tiers ma niekanoniczny klucz CID")
        if not isinstance(row, dict):
            raise ValueError("courier_tiers ma rekord kuriera, który nie jest obiektem")
    return store


def validate_courier_names_root(store) -> Dict:
    """``courier_names.json`` = ``{cid: krótka nazwa panelowa}``."""
    for cid, name in _as_mapping(store, "courier_names").items():
        if canonical_numeric_cid(cid) is None:
            raise ValueError("courier_names ma niekanoniczny klucz CID")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("courier_names ma CID bez nazwy")
    return store


def validate_kurier_full_names_root(store) -> Dict:
    """``daily_accounting/kurier_full_names.json`` = ``{alias: pełne imię}``."""
    for alias, full_name in _as_mapping(store, "kurier_full_names").items():
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("kurier_full_names ma pusty alias")
        if not isinstance(full_name, str) or not full_name.strip():
            raise ValueError("kurier_full_names ma alias bez pełnego imienia")
    return store


#: root-id -> walidator. Owner mapy jest JEDEN; ``identity.journal`` odkrywa z
#: niej walidator per root, a ratchet pilnuje, żeby każdy root transakcji miał
#: tu wpis (dołożenie szóstego roota bez walidatora = czerwony test).
ROOT_VALIDATORS = {
    "kurier_ids": validate_kurier_ids_root,
    "kurier_piny": validate_kurier_piny_root,
    "courier_tiers": validate_courier_tiers_root,
    "courier_names": validate_courier_names_root,
    "kurier_full_names": validate_kurier_full_names_root,
}


def validate_root(root: str, store):
    """Waliduj ``store`` walidatorem roota ``root``. Nieznany root = ``ValueError``
    (fail-closed: nie ma „domyślnego" schematu)."""
    validator = ROOT_VALIDATORS.get(root)
    if validator is None:
        raise ValueError(f"nieznany root generacji: {root!r}")
    return validator(store)
# Schemat POJEDYNCZEGO rekordu roota tożsamości (A-6/G6, K1)
# --------------------------------------------------------------------------- #
# ``validate_courier_ids_store`` odpowiada na pytanie AUTORYZACJI: „czy tej
# CAŁEJ generacji wolno ufać" (jeden zepsuty wiersz wywraca całość — konsument
# nie ma prawa odpowiadać z rostera, którego nie da się zweryfikować). Verifier
# wpięcia kuriera (``new_courier_pairing.verify_courier_wired``) zadaje pytanie
# o jeden stopień drobniejsze: „czy TEN rekord zobaczy kanoniczny czytelnik" —
# musi umieć oddzielić rekord audytowanej tożsamości (fail-closed) od rekordu
# obcego (raport ⚠), więc potrzebuje warstwy per REKORD, nie per generacja.
#
# Reguły kształtu rootów mieszkają WYŁĄCZNIE tutaj. Decyzja o formie CID jest w
# każdym z nich delegowana do ``canonical_numeric_cid`` — tego samego jednego
# ownera, którego używa writer onboardingu (G4), writer grafiku (G7) i roster
# (G3). Dzięki temu „widoczny dla czytelnika" znaczy w audycie DOKŁADNIE to, co
# znaczy przy zapisie; ratchet w ``tests/test_a6_g6_verifier_root_schema.py``
# pilnuje, żeby nie wróciła druga kopia tej polityki.
#
# Każdy walidator: ``None`` = rekord zdrowy, string = powód odrzucenia. Powód
# NIGDY nie zawiera nazwiska ani PIN-u (parytet z raportami A-6) i nigdy nie
# podnosi wyjątku — audyt ma raportować cały root, nie kończyć się na pierwszym
# zepsutym wierszu.

TIERS_META_KEY = "_meta"  # blok metadanych courier_tiers — NIE jest rekordem kuriera


def ids_record_issue(name, cid) -> Optional[str]:
    """``kurier_ids.json`` = ``{nazwa: cid}`` — rejestr autoryzacyjny."""
    if not isinstance(name, str) or not name.strip():
        return "klucz-nazwa nie jest niepustym stringiem"
    if canonical_numeric_cid(cid) is None:
        return "wartość-CID poza kanoniczną formą liczbową"
    return None


def piny_record_issue(pin, alias) -> Optional[str]:
    """``kurier_piny.json`` = ``{pin: alias}``.

    ``pin_auth.resolve_pin`` trafia klucz DOKŁADNIE, a apka kuriera wysyła to,
    co da się wystukać na klawiaturze PIN-u — klucz spoza formy mintowanej przez
    onboarding jest loginem, którego nikt nie wpisze.
    """
    if (
        not isinstance(pin, str)
        or len(pin) != PIN_LENGTH
        or not pin.isascii()
        or not pin.isdigit()
    ):
        return f"klucz-PIN poza formą {PIN_LENGTH} cyfr ASCII"
    if not isinstance(alias, str) or not alias.strip():
        return "PIN bez aliasu tożsamości"
    return None


def tiers_record_issue(cid, row) -> Optional[str]:
    """``courier_tiers.json`` = ``{cid: {...}}`` + opcjonalny blok ``_meta``."""
    if cid == TIERS_META_KEY:
        return None if isinstance(row, dict) else "blok _meta nie jest obiektem"
    if canonical_numeric_cid(cid) is None:
        return "klucz-CID poza kanoniczną formą liczbową"
    if not isinstance(row, dict):
        return "rekord kuriera nie jest obiektem"
    return None


def courier_names_record_issue(cid, name) -> Optional[str]:
    """``courier_names.json`` = ``{cid: krótka nazwa panelowa}``."""
    if canonical_numeric_cid(cid) is None:
        return "klucz-CID poza kanoniczną formą liczbową"
    if not isinstance(name, str) or not name.strip():
        return "CID bez nazwy"
    return None


def full_names_record_issue(alias, full_name) -> Optional[str]:
    """``daily_accounting/kurier_full_names.json`` = ``{alias: pełne imię}``."""
    if not isinstance(alias, str) or not alias.strip():
        return "klucz-alias nie jest niepustym stringiem"
    if not isinstance(full_name, str) or not full_name.strip():
        return "alias bez pełnego imienia"
    return None


def grafik_record_issue(full_name, cid) -> Optional[str]:
    """``grafik_full_names.json`` = ``{pełne imię: cid}`` (PANEL-CANON)."""
    if not isinstance(full_name, str) or not full_name.strip():
        return "klucz-imię nie jest niepustym stringiem"
    if canonical_numeric_cid(cid) is None:
        return "wartość-CID poza kanoniczną formą liczbową"
    return None


def pin_last2(pin) -> Optional[str]:
    """Last two chars of a PIN — never expose the full PIN in artifacts."""
    if pin is None:
        return None
    s = str(pin).strip()
    if not s:
        return None
    return s[-2:]


@dataclass
class CourierRecord:
    """One courier keyed by canonical ``cid`` (str)."""

    cid: str
    aliases: Dict[str, List[str]] = field(default_factory=dict)   # source -> [alias]
    full_name: Dict[str, str] = field(default_factory=dict)        # source -> full name
    tier: Optional[str] = None
    pin_present: bool = False
    pin_last2: Optional[str] = None
    active: bool = True
    excluded: bool = False
    is_coordinator: bool = False
    added_at: Optional[str] = None

    def all_aliases(self) -> List[str]:
        """Deduplicated union of aliases across all sources (insertion order)."""
        seen: Dict[str, None] = {}
        for vals in self.aliases.values():
            for a in vals:
                if a not in seen:
                    seen[a] = None
        return list(seen.keys())

    def add_alias(self, source: str, alias: str) -> None:
        if not alias:
            return
        bucket = self.aliases.setdefault(source, [])
        if alias not in bucket:
            bucket.append(alias)

    def best_full_name(self) -> Optional[str]:
        """Preferred display name: grafik > accounting > panel > app > gps."""
        for src in ("grafik", "accounting", "panel", "app", "gps"):
            v = self.full_name.get(src)
            if v:
                return v
        return None

    def to_public_dict(self) -> dict:
        """PIN-redacted, JSON-safe view for reports (never the plaintext PIN)."""
        return {
            "cid": self.cid,
            "best_full_name": self.best_full_name(),
            "aliases": {k: list(v) for k, v in self.aliases.items()},
            "full_name": dict(self.full_name),
            "tier": self.tier,
            "pin_present": self.pin_present,
            "pin_last2": self.pin_last2,
            "active": self.active,
            "excluded": self.excluded,
            "is_coordinator": self.is_coordinator,
            "added_at": self.added_at,
        }


def validate_record(rec: CourierRecord) -> List[str]:
    """Return a list of schema issues (empty == valid). Never raises."""
    issues: List[str] = []
    if not rec.cid:
        issues.append("empty cid")
    if rec.cid and rec.cid != canon_cid(rec.cid):
        issues.append(f"non-canonical cid {rec.cid!r}")
    for src in rec.aliases:
        if src not in SOURCE_LABELS:
            issues.append(f"unknown alias source {src!r}")
    for src in rec.full_name:
        if src not in SOURCE_LABELS:
            issues.append(f"unknown full_name source {src!r}")
    if rec.pin_present and not rec.pin_last2:
        issues.append("pin_present but no pin_last2")
    if rec.pin_last2 and len(rec.pin_last2) > 2:
        issues.append("pin_last2 leaks more than 2 chars")
    return issues
