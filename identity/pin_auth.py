"""A-6 SECURITY P0 (2026-08-02) — kanoniczna weryfikacja PIN kuriera z KDF
(PBKDF2-HMAC-SHA256) + losowa SÓL PER-USER, ZA flagą `ENABLE_PIN_KDF` (default OFF).

DEFEKT ŹRÓDŁOWY: magazyn PIN `kurier_piny.json` trzyma PIN-y jako PLAINTEXTOWE
KLUCZE słownika (`{pin: name}`). Wyciek pliku = natychmiastowy wyciek wszystkich
PIN-ów kurierów; brak KDF, brak soli. Bliźniak `courier_api/auth.py::resolve_pin`
(apka Android) czyta ten sam plaintext (dokumentacja mapy kompletności w raporcie).

FIX — wzorzec ADDYTYWNY + DUAL-READ + shadow-first (BEZ masowej migracji, zero-live):
  - Legacy `kurier_piny.json` = {pin: name} POZOSTAJE kanonicznym magazynem dla
    ścieżki OFF ORAZ dla każdego wewnętrznego konsumenta zależnego od kształtu
    {pin:name} — `courier_resolver` (phantom-defense przez `piny.keys()`),
    `courier_info` (/pin), onboarding `courier_admin`. Ten moduł NIGDY go nie
    nadpisuje ani nie usuwa z niego kluczy.
  - NOWY ADDYTYWNY magazyn KDF `kurier_piny_kdf.json` =
    `{name: {"kdf","salt","iter","hash"}}` — zapełnia się LENIWIE przy logowaniu.
  - `resolve_pin(pin)` DUAL-READ (flaga ON):
      1. NOWY FORMAT NAJPIERW: dla tożsamości wskazanej przez legacy weryfikuj
         rekord KDF w stałym czasie; gdy legacy przycięte (przyszła migracja ACK)
         — pełny skan KDF (PIN-y globalnie unikatowe → jednoznaczne).
      2. STARY FORMAT fallback: dokładne trafienie klucza legacy = weryfikacja
         starego formatu; gdy brak rekordu KDF → LENIWY re-hash (addytywnie, pod
         flock). Zwróć name.
      3. brak → None.
  - flaga OFF → dokładnie legacy `piny.get(pin)` (bajt-parytet, zero KDF, zero I/O
    do magazynu KDF).

DUAL-READ NIE otwiera bypassu: trafienie legacy rozwiązuje TYLKO ISTNIEJĄCY rekord
`{pin:name}`; nie ma uniwersalnego fallbacku akceptującego nieznane PIN-y. Nieznany
PIN → None w OBU trybach.

BEZPIECZEŃSTWO LOGÓW: ten moduł NIGDY nie loguje ani nie zwraca PIN-u, soli, ani
wejścia hasha. `resolve_pin` zwraca wyłącznie `name` (jak legacy resolver).
"""
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Optional

# ── parametry KDF ────────────────────────────────────────────────────────────
KDF_NAME = "pbkdf2_sha256"
# Koszt NIEZEROWY. PIN 4-cyfrowy ma tylko 10 000 kombinacji, więc realną obronę
# brute-force daje rate-limit (gps_rate_limit / courier_api pin_attempts) — KDF+sól
# zapewnia HIGIENĘ MAGAZYNU: wyciek pliku nie ujawnia PIN-u wprost, sól per-user
# blokuje rainbow/prekomputację i korelację (ten sam PIN → różny hash u dwóch osób).
PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
_MIN_ITER_FLOOR = 50_000  # security-oracle: rekord poniżej = podejrzenie osłabienia

LOCK_SUFFIX = ".lock"


def pin_kdf_enabled() -> bool:
    """Odczyt `ENABLE_PIN_KDF` z `flags.json` (hot-reload, wspólne źródło).
    Fail-soft → OFF (legacy) gdy flags nieczytelne / import padł."""
    try:
        from dispatch_v2.common import flag
        # Nazwa LITERALNIE (nie przez stałą) — wymóg walidatora rejestru flag
        # (flag_lifecycle_seed: literal_flag_calls). Wartość identyczna.
        return bool(flag("ENABLE_PIN_KDF", False))
    except Exception:
        return False


# ── prymitywy KDF ────────────────────────────────────────────────────────────

def _hash_pin(pin: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", str(pin).encode("utf-8"), salt, iterations)


def make_record(pin: str) -> dict:
    """Nowy rekord KDF dla PIN-u: losowa sól per-user + hash PBKDF2. NIE zawiera
    PIN-u plaintext ani niczego, z czego PIN da się odtworzyć bez brute-force."""
    salt = secrets.token_bytes(SALT_BYTES)
    it = PBKDF2_ITERATIONS
    digest = _hash_pin(pin, salt, it)
    return {"kdf": KDF_NAME, "salt": salt.hex(), "iter": it, "hash": digest.hex()}


def verify_record(pin: str, rec: dict) -> bool:
    """Stały-czasowo porównaj PIN z rekordem KDF. False przy dowolnej niespójności
    (obcy `kdf`, zła sól, uszkodzony hash) — fail-closed."""
    try:
        if not isinstance(rec, dict) or rec.get("kdf") != KDF_NAME:
            return False
        salt = bytes.fromhex(str(rec["salt"]))
        iterations = int(rec["iter"])
        expected = bytes.fromhex(str(rec["hash"]))
        if iterations <= 0 or not salt or not expected:
            return False
        got = _hash_pin(pin, salt, iterations)
        return hmac.compare_digest(got, expected)
    except Exception:
        return False


# ── I/O magazynu KDF (addytywne, atomowe, pod flock) ─────────────────────────

def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _atomic_write_json(path: str, data: dict) -> None:
    """temp (mkstemp) + flock LOCK_EX na temp + fsync + rename (wzorzec P0.5b,
    1:1 z gps_pwa_store)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def register_pin(name: str, pin: str, *, kdf_path: str) -> bool:
    """Addytywnie zapisz rekord KDF dla `name` (idempotentnie — istniejącego NIE
    nadpisuje, nie re-hashuje). Cały cykl load→set→write pod DEDYKOWANYM lockfile
    (serializacja cross-proces: równoległe lazy-rehash / onboarding). Zwraca True
    gdy dopisano nowy rekord, False gdy już istniał.

    Publiczne API dla PRZYSZŁEJ adopcji onboardingu (courier_admin) po flipie ON;
    dziś magazyn zapełnia się głównie leniwie przez `resolve_pin`."""
    _dir = os.path.dirname(kdf_path) or "."
    os.makedirs(_dir, exist_ok=True)
    with open(kdf_path + LOCK_SUFFIX, "w") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            store = _load_json(kdf_path)
            if name in store:
                return False
            store[name] = make_record(pin)
            _atomic_write_json(kdf_path, store)
            return True
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)


# ── skan / dual-read resolve ─────────────────────────────────────────────────

def _scan_store(pin: str, store: dict) -> Optional[str]:
    """Pełny skan magazynu KDF (używany tylko gdy legacy nie zna PIN-u — np. po
    przyszłej ACK-owanej migracji z przycięciem legacy). PIN-y globalnie unikatowe
    → co najwyżej jedno trafienie."""
    for name, rec in store.items():
        if verify_record(pin, rec):
            return name
    return None


def resolve_pin(pin, *, piny_path: str, kdf_path: str,
                use_kdf: Optional[bool] = None) -> Optional[str]:
    """PIN → `name` (kanoniczna nazwa z magazynu) albo None dla nieznanego PIN-u.

    Parametry:
      piny_path: legacy `kurier_piny.json` ({pin: name}) — NIGDY nadpisywany.
      kdf_path:  addytywny `kurier_piny_kdf.json` ({name: rec}).
      use_kdf:   None → czytaj flagę `ENABLE_PIN_KDF`; True/False → wymuś
                 (deterministyczne testy ON/OFF; produkcja woła bez tego argumentu).

    OFF → dokładnie legacy `piny.get(pin)` (bajt-parytet; zero dostępu do kdf_path).
    """
    pin = str(pin or "")
    piny = _load_json(piny_path)
    enabled = pin_kdf_enabled() if use_kdf is None else bool(use_kdf)
    if not enabled:
        name = piny.get(pin)
        return name if name else None

    kdf_store = _load_json(kdf_path)
    cand = piny.get(pin) or None  # kandydat tożsamości z legacy (O(1) reverse index)
    if cand is not None:
        # (1) NOWY FORMAT NAJPIERW dla tej tożsamości.
        rec = kdf_store.get(cand)
        if rec is not None:
            # Rekord KDF istnieje → weryfikacja KDF jest autorytatywna dla „nowego
            # formatu". Trafienie klucza legacy i tak potwierdza poprawność PIN-u
            # (PIN-y globalnie unikatowe), więc w fazie shadow zwracamy cand także
            # gdy rekord KDF jest niespójny (nie blokujemy kuriera; brak re-hashu).
            verify_record(pin, rec)
            return cand
        # (2) STARY FORMAT: brak rekordu KDF → LENIWY re-hash (addytywny).
        register_pin(cand, pin, kdf_path=kdf_path)
        return cand

    # Legacy nie zna PIN-u → nowy format wciąż autorytatywny (post-migracja).
    return _scan_store(pin, kdf_store)
