"""A-6 SECURITY P0 (2026-08-02) — bramka testowa Przykazania #0 dla:
  (1) PIN KDF (PBKDF2-HMAC-SHA256) + sól per-user + dual-read + lazy re-hash
      (`identity/pin_auth`),
  (2) rate-limit floodu pozycji GPS per-kurier/per-IP (`gps_rate_limit`),
  wraz z wiringiem w `gps_server` (_resolve_pin dual-read + do_POST 429).

Zawiera: NEGATYWNY ORACLE (reprodukuje defekt: plaintext / brak dławienia),
MUTATION PROBE (KDF→plaintext oraz usunięcie limitu → oracle RED), PARYTET ON≠OFF.

Sesja 297 (bramka `tests.pin-kdf-oracle-substring-flake`): security-oracle magazynu
KDF mieszka w `tests/pin_kdf_store_oracle.py` (jedno źródło). Iteracja 1 wycięła
skan dosłowny (flake ~0,6% biegów — 4-cyfrowy PIN trafiał w losowy hex soli/hasha)
i zastąpiła go samym kształtem pól; blind review 2026-08-05 wykazał, że sól/`iter`
stały się przez to polami SWOBODNYMI (PIN wpisany w sól przechodził na zielono).
Iteracja 2: skan dosłowny WRACA (asercja (G) + `iter` przypięty do kontraktowej
wartości produkcji), a flake znika przez KONTRAKT DŁUGOŚCI PIN-u — bramka woła
oracle SYNTETYCZNYMI PIN-ami >= `MIN_ORACLE_PIN_LEN` (12 cyfr, p(kolizji) ~7e-13),
co oracle egzekwuje fail-closed (`test_oracle_rejects_short_pin_fail_closed`).
Siłę oracle trzymają: `test_mutation_leak_forms_fail_oracle` (katalog `LEAK_FORMS`,
17 realnych form wycieku — każda MUSI dać RED), mutacja produkcji „sól z PIN-u"
(`test_mutation_salt_derived_from_pin_*`) i sonda niezależności soli od sekretu.
Brak flake'a: `test_oracle_stable_when_kdf_hex_contains_pin_digits` + sweep 1000
biegów w `eod_drafts/2026-08-05/pin_oracle_flake_297/`.

PIN-y syntetyczne vs produkcyjne: testy ORACLE używają 12-cyfrowych PIN-ów
syntetycznych, testy ŚCIEŻKI PRODUKCYJNEJ (`resolve_pin`, `gps_server`) zostają na
4-cyfrowym PIN-ie produkcyjnym (`identity/schema.PIN_LENGTH`). Most między nimi to
`test_record_shape_is_pin_length_independent`: `pin_auth` nie ma żadnej gałęzi
zależnej od długości PIN-u, więc forma wycieku wykryta na 12 cyfrach jest tą samą
formą dla 4 cyfr.

Izolacja: wszystkie ścieżki w tmp_path (HERMETIC-GUARD spełniony). ZERO PIN-ów z
żywego kurier_piny.json — wyłącznie syntetyczne PIN-y w tmp. Żaden test nie loguje
ani nie asertuje sekretu poza syntetycznym PIN-em użytym lokalnie do weryfikacji.
"""
import io
import json
import os

import pytest

from dispatch_v2 import gps_rate_limit as rl
from dispatch_v2 import gps_server
from dispatch_v2.identity import pin_auth
# SECURITY ORACLE magazynu KDF — JEDNO ŹRÓDŁO (`tests/pin_kdf_store_oracle.py`),
# wspólne z dowodem anty-flake `eod_drafts/2026-08-05/pin_oracle_flake_297/`.
# Sesja 297 iter2: asercje strukturalne (A-F) + PRZYWRÓCONY SKAN DOSŁOWNY (G) na
# syntetycznych PIN-ach o długości czyniącej kolizję z hexem pomijalną.
from dispatch_v2.tests import pin_kdf_store_oracle as _oracle
from dispatch_v2.tests.pin_kdf_store_oracle import (
    assert_no_plaintext_pin_in_store as _no_plaintext_oracle,
)

# SYNTETYCZNE PIN-y bramki oracle (kontrakt `MIN_ORACLE_PIN_LEN` = 12 cyfr).
PIN_A = "471028365914"
PIN_B = "830257491063"
# PIN o kształcie flake'a: jego 4-cyfrowy PREFIKS ląduje w hexie soli (dokładnie ta
# przypadkowa kolizja czerwieniła stary skan na PIN-ie produkcyjnym).
PIN_FLAKE_SHAPE = "123487560192"
# Entropia przypięta na czas sondy niezależności soli od sekretu.
_FIXED_ENTROPY = bytes.fromhex("a1b2c3d4e5f60718293a4b5c6d7e8f90")


# ─────────────────────────── PIN KDF — helpers ──────────────────────────────

def _pin_entropy(monkeypatch):
    """Przypnij CAŁĄ entropię widzianą przez `pin_auth` — dzięki temu jedyną
    zmienną wejściową `make_record` zostaje PIN (sonda niezależności soli)."""
    class _FixedSecrets:  # tylko `token_bytes` — jedyne API używane przez pin_auth
        @staticmethod
        def token_bytes(n):
            return _FIXED_ENTROPY[:n]

    monkeypatch.setattr(pin_auth, "secrets", _FixedSecrets)
    monkeypatch.setattr(os, "urandom", lambda n: _FIXED_ENTROPY[:n])


def _salt_from_pin_record(pin):
    """MUTANT PRODUKCJI (repro blind review F1): sól WYPROWADZONA Z SEKRETU —
    kształt pól zachowany (32 zn. hex), hash to prawdziwy PBKDF2 nad tą solą,
    a PIN jest czytelny wprost w `kurier_piny_kdf.json`."""
    salt_hex = (pin + pin_auth.secrets.token_bytes(pin_auth.SALT_BYTES).hex())[:_oracle.SALT_HEX_LEN]
    salt = bytes.fromhex(salt_hex)
    it = pin_auth.PBKDF2_ITERATIONS
    return {"kdf": pin_auth.KDF_NAME, "salt": salt_hex, "iter": it,
            "hash": pin_auth._hash_pin(pin, salt, it).hex()}

def _mk_stores(tmp_path, mapping):
    piny = tmp_path / "kurier_piny.json"
    kdf = tmp_path / "kurier_piny_kdf.json"
    piny.write_text(json.dumps(mapping), encoding="utf-8")
    return str(piny), str(kdf)


# ─────────────────────────── PIN KDF — oracle ───────────────────────────────

def test_off_resolve_is_legacy_byte_parity(tmp_path):
    piny, kdf = _mk_stores(tmp_path, {"1234": "Marcin By", "5678": "Jan Ko"})
    assert pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=False) == "Marcin By"
    assert pin_auth.resolve_pin("5678", piny_path=piny, kdf_path=kdf, use_kdf=False) == "Jan Ko"
    assert pin_auth.resolve_pin("9999", piny_path=piny, kdf_path=kdf, use_kdf=False) is None
    assert pin_auth.resolve_pin("", piny_path=piny, kdf_path=kdf, use_kdf=False) is None
    # OFF NIE tworzy magazynu KDF (zero I/O, bajt-parytet).
    import os
    assert not os.path.exists(kdf), "OFF nie może tworzyć magazynu KDF"


def test_on_old_pin_dual_read_then_lazy_rehash(tmp_path):
    piny, kdf = _mk_stores(tmp_path, {"1234": "Marcin By"})
    # 1. stary PIN → dual-read OK (name) + lazy re-hash tworzy rekord KDF.
    assert pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=True) == "Marcin By"
    store = pin_auth._load_json(kdf)
    assert "Marcin By" in store, "lazy re-hash nie zapisał rekordu KDF"
    rec = store["Marcin By"]
    assert rec["kdf"] == pin_auth.KDF_NAME and int(rec["iter"]) >= pin_auth._MIN_ITER_FLOOR
    assert pin_auth.verify_record("1234", rec) and not pin_auth.verify_record("0000", rec)
    # 2. druga weryfikacja przechodzi przez NOWY format (KDF), nie zmienia magazynu.
    before = json.dumps(pin_auth._load_json(kdf))
    assert pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=True) == "Marcin By"
    assert json.dumps(pin_auth._load_json(kdf)) == before, "druga weryfikacja re-hashowała ponownie"


def test_on_wrong_pin_rejected_no_bypass(tmp_path):
    piny, kdf = _mk_stores(tmp_path, {"1234": "Marcin By"})
    # Zapełnij magazyn KDF (migracja Marcina).
    pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=True)
    # Nieznany PIN → None (ON), MIMO zapełnionego magazynu (brak uniwersalnego fallbacku).
    assert pin_auth.resolve_pin("0000", piny_path=piny, kdf_path=kdf, use_kdf=True) is None
    assert pin_auth.resolve_pin("9999", piny_path=piny, kdf_path=kdf, use_kdf=True) is None
    assert pin_auth.resolve_pin("", piny_path=piny, kdf_path=kdf, use_kdf=True) is None


def test_salt_per_user_differs_and_cost_nonzero(tmp_path):
    piny, kdf = _mk_stores(tmp_path, {PIN_A: "Marcin By", PIN_B: "Jan Ko"})
    pin_auth.resolve_pin(PIN_A, piny_path=piny, kdf_path=kdf, use_kdf=True)
    pin_auth.resolve_pin(PIN_B, piny_path=piny, kdf_path=kdf, use_kdf=True)
    _no_plaintext_oracle(kdf, [PIN_A, PIN_B])  # sprawdza sole różne + iter kontraktowy


def test_security_oracle_no_plaintext_in_store(tmp_path):
    piny, kdf = _mk_stores(tmp_path, {PIN_A: "Marcin By", PIN_B: "Jan Ko"})
    pin_auth.resolve_pin(PIN_A, piny_path=piny, kdf_path=kdf, use_kdf=True)
    pin_auth.resolve_pin(PIN_B, piny_path=piny, kdf_path=kdf, use_kdf=True)
    _no_plaintext_oracle(kdf, [PIN_A, PIN_B])  # PASS na prawdziwym KDF


def test_mutation_plaintext_store_fails_oracle(tmp_path, monkeypatch):
    """MUTATION: gdyby make_record zapisywał PLAINTEXT (osłabienie KDF), security
    oracle MUSI sczerwienieć."""
    def _plaintext_record(pin):
        return {"kdf": pin_auth.KDF_NAME, "salt": "00", "iter": 0, "hash": str(pin)}
    monkeypatch.setattr(pin_auth, "make_record", _plaintext_record)
    piny, kdf = _mk_stores(tmp_path, {PIN_A: "Marcin By"})
    pin_auth.resolve_pin(PIN_A, piny_path=piny, kdf_path=kdf, use_kdf=True)
    with pytest.raises(AssertionError):
        _no_plaintext_oracle(kdf, [PIN_A])


# ───────── sesja 297: anty-flake + dowód, że oracle POZOSTAJE oraclem ─────────

def test_oracle_stable_when_kdf_hex_contains_pin_digits(tmp_path, monkeypatch):
    """REGRESJA FLAKE (bramka `tests.pin-kdf-oracle-substring-flake`): sól/hash to
    losowy hex, więc KRÓTKI ciąg cyfr wpada do niego przypadkiem (PIN produkcyjnej
    długości: ~0,6% biegów → fałszywa czerwień strażnika CO NOC). Deterministyczna
    reprodukcja kształtu: wymuszamy sól, której hex zawiera 4-cyfrowy FRAGMENT
    syntetycznego PIN-u bramki. Oracle iter2 = PASS (skan dosłowny szuka PIN-u i
    fragmentów >= BLOB_FRAGMENT_MIN_LEN, a nie 4 przypadkowych cyfr)."""
    pin, name = PIN_FLAKE_SHAPE, "Marcin By"
    salt = bytes.fromhex("0123456789abcdef0123456789abcdef")
    assert pin[:4] in salt.hex(), "setup: fragment PIN-u ma być w hexie soli (kształt flake'a)"

    class _FixedSecrets:  # tylko `token_bytes` — jedyne API używane przez pin_auth
        @staticmethod
        def token_bytes(n):
            return salt[:n]

    monkeypatch.setattr(pin_auth, "secrets", _FixedSecrets)
    piny, kdf = _mk_stores(tmp_path, {pin: name})
    assert pin_auth.resolve_pin(pin, piny_path=piny, kdf_path=kdf, use_kdf=True) == name
    store = pin_auth._load_json(kdf)
    assert pin[:4] in json.dumps(store), "repro: kolizja krótkiego ciągu cyfr z hexem"
    assert pin_auth.verify_record(pin, store[name]), "rekord musi być prawdziwym KDF"
    _no_plaintext_oracle(kdf, [pin])  # oracle iter2: PASS — zero fałszywej czerwieni


def test_oracle_rejects_short_pin_fail_closed(tmp_path):
    """KONTRAKT ANTY-FLAKE (iter2): oracle NIE WOLNO wołać PIN-em produkcyjnej
    długości — skan dosłowny na 4 cyfrach czerwieni losowo (~0,6% biegów) i to był
    flake. Wywołanie takie jest błędem BRAMKI i musi być odrzucone FAIL-CLOSED,
    a nie po cichu przełączać oracle w słabszy tryb (regresja z iteracji 1)."""
    piny, kdf = _mk_stores(tmp_path, {"1234": "Marcin By"})
    pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=True)
    with pytest.raises(AssertionError, match="MIN_ORACLE_PIN_LEN"):
        _no_plaintext_oracle(kdf, ["1234"])


def test_salt_is_independent_of_pin(monkeypatch):
    """SONDA PRODUKCYJNA (blind review F1): sól i `iter` są polami SWOBODNYMI, więc
    z samego pliku nie da się wykluczyć zakodowania w nich PIN-u. Sprawdzamy KOD:
    przy TEJ SAMEJ entropii różne PIN-y (także produkcyjnej długości) MUSZĄ dostać
    TĘ SAMĄ sól — sól nie może być funkcją sekretu."""
    _pin_entropy(monkeypatch)
    recs = [pin_auth.make_record(p) for p in (PIN_A, PIN_B, "1234")]
    _oracle.assert_salt_not_derived_from_secret(recs)
    assert len({r["hash"] for r in recs}) == len(recs), "hash MUSI zależeć od PIN-u"


def test_mutation_salt_derived_from_pin_fails_independence_probe(monkeypatch):
    """MUTACJA: produkcja wyprowadzająca sól z PIN-u MUSI sczerwienić sondę
    niezależności (inaczej sonda nie jest sondą)."""
    _pin_entropy(monkeypatch)
    monkeypatch.setattr(pin_auth, "make_record", _salt_from_pin_record)
    recs = [pin_auth.make_record(p) for p in (PIN_A, PIN_B)]
    with pytest.raises(AssertionError):
        _oracle.assert_salt_not_derived_from_secret(recs)


def test_mutation_salt_derived_from_pin_fails_store_oracle(tmp_path, monkeypatch):
    """MUTACJA PRODUKCJI END-TO-END (dokładne repro z blind review F1): gdyby
    `make_record` wyprowadzał sól z PIN-u, PIN byłby CZYTELNY w
    `kurier_piny_kdf.json` przy zachowanym kształcie pól i prawdziwym PBKDF2
    (warunek (F) zielony). Oracle iteracji 1 przechodził tu na zielono — iter2
    MUSI być RED."""
    monkeypatch.setattr(pin_auth, "make_record", _salt_from_pin_record)
    piny, kdf = _mk_stores(tmp_path, {PIN_A: "Marcin By"})
    assert pin_auth.resolve_pin(PIN_A, piny_path=piny, kdf_path=kdf, use_kdf=True) == "Marcin By"
    store = pin_auth._load_json(kdf)
    assert PIN_A in json.dumps(store), "repro: PIN MUSI być czytelny wprost w pliku"
    assert pin_auth.verify_record(PIN_A, store["Marcin By"]), "rekord jest prawdziwym PBKDF2"
    with pytest.raises(AssertionError):
        _no_plaintext_oracle(kdf, [PIN_A])


def test_record_shape_is_pin_length_independent():
    """MOST 4↔12 CYFR: bramka oracle używa 12-cyfrowych PIN-ów syntetycznych, a
    produkcja ma 4 cyfry (`identity/schema.PIN_LENGTH`). `pin_auth` nie ma ŻADNEJ
    gałęzi zależnej od długości PIN-u, więc rekord ma identyczny kontrakt w obu
    przypadkach — forma wycieku wykryta na 12 cyfrach jest tą samą formą dla 4."""
    prod, synth = "1234", PIN_A
    r_prod, r_synth = pin_auth.make_record(prod), pin_auth.make_record(synth)
    assert set(r_prod) == set(r_synth) == _oracle.KDF_REC_FIELDS
    for rec in (r_prod, r_synth):
        assert len(rec["salt"]) == _oracle.SALT_HEX_LEN
        assert len(rec["hash"]) == _oracle.HASH_HEX_LEN
        assert rec["kdf"] == pin_auth.KDF_NAME
        assert rec["iter"] == pin_auth.PBKDF2_ITERATIONS
    assert pin_auth.verify_record(prod, r_prod) and not pin_auth.verify_record(synth, r_prod)
    assert pin_auth.verify_record(synth, r_synth) and not pin_auth.verify_record(prod, r_synth)


@pytest.mark.parametrize("form", sorted(_oracle.LEAK_FORMS))
def test_mutation_leak_forms_fail_oracle(tmp_path, form):
    """ORACLE POZOSTAJE ORACLEM: każda REALNA forma wycieku PIN-u do magazynu KDF
    (plaintext w polu, PIN w nazwie pola/klucza, PIN doklejony do wartości, PIN
    zakodowany hexem, osłabiony koszt KDF, a od iter2 również formy ZACHOWUJĄCE
    kształt pól: PIN w soli — dosłownie / odwrócony / hex-ASCII, PIN rozdzielony
    klucz+sól, cyfry PIN-u w `iter`) MUSI sczerwienić oracle. Katalog form =
    `tests/pin_kdf_store_oracle.LEAK_FORMS` (jedno źródło, wspólne ze sweepem).
    Kontrola w tym samym teście: czysty magazyn KDF = PASS."""
    pin, name = PIN_A, "Marcin By"
    kdf = str(tmp_path / "kurier_piny_kdf.json")
    store = {name: pin_auth.make_record(pin)}
    pin_auth._atomic_write_json(kdf, store)
    _no_plaintext_oracle(kdf, [pin])  # kontrola: prawdziwy KDF przechodzi
    _oracle.LEAK_FORMS[form](store, name, pin)
    pin_auth._atomic_write_json(kdf, store)
    with pytest.raises(AssertionError):
        _no_plaintext_oracle(kdf, [pin])


def test_parity_on_vs_off_behavioral(tmp_path):
    """ON≠OFF: OFF nie tworzy rekordu KDF; ON tworzy (mierzalna różnica zachowania)."""
    import os
    # OFF branch (own tmp).
    off_dir = tmp_path / "off"; off_dir.mkdir()
    p_off, k_off = _mk_stores(off_dir, {"1234": "Marcin By"})
    assert pin_auth.resolve_pin("1234", piny_path=p_off, kdf_path=k_off, use_kdf=False) == "Marcin By"
    assert not os.path.exists(k_off)
    # ON branch (own tmp).
    on_dir = tmp_path / "on"; on_dir.mkdir()
    p_on, k_on = _mk_stores(on_dir, {"1234": "Marcin By"})
    assert pin_auth.resolve_pin("1234", piny_path=p_on, kdf_path=k_on, use_kdf=True) == "Marcin By"
    assert os.path.exists(k_on) and "Marcin By" in pin_auth._load_json(k_on)


def test_resolve_via_scan_post_migration(tmp_path):
    """Po (przyszłej ACK) migracji legacy przycięte: PIN nieobecny w {pin:name}, ale
    rekord KDF istnieje → resolve przez skan zwraca name; nieznany → None (bez bypassu)."""
    piny, kdf = _mk_stores(tmp_path, {"1234": "Marcin By"})
    pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=True)  # zapełnij KDF
    # Symuluj przycięcie legacy: pusty {pin:name}.
    (tmp_path / "kurier_piny.json").write_text("{}", encoding="utf-8")
    assert pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=True) == "Marcin By"
    assert pin_auth.resolve_pin("0000", piny_path=piny, kdf_path=kdf, use_kdf=True) is None


# ─────────────────────────── GPS rate-limit — oracle ────────────────────────

def test_flood_throttled_when_on():
    lim = rl.SlidingWindowLimiter()
    n = rl.PER_COURIER_MAX
    # Normalna kadencja: n zdarzeń w oknie → wszystkie przechodzą.
    for i in range(n):
        assert rl.check("C1", None, enabled=True, now=1000.0 + i, limiter=lim) is None
    # Flood: (n+1)-sze w tym samym oknie → ZDŁAWIONE (scope 'courier').
    assert rl.check("C1", None, enabled=True, now=1000.0 + n, limiter=lim) == rl.SCOPE_COURIER


def test_off_no_throttle():
    lim = rl.SlidingWindowLimiter()
    for i in range(rl.PER_COURIER_MAX * 5):
        assert rl.check("C1", None, enabled=False, now=1000.0, limiter=lim) is None


def test_per_ip_scope_throttles():
    lim = rl.SlidingWindowLimiter()
    for i in range(rl.PER_IP_MAX):
        assert rl.check(None, "1.2.3.4", enabled=True, now=2000.0, limiter=lim) is None
    assert rl.check(None, "1.2.3.4", enabled=True, now=2000.0, limiter=lim) == rl.SCOPE_IP
    # Inny IP nie jest dławiony (izolacja kluczy).
    assert rl.check(None, "5.6.7.8", enabled=True, now=2000.0, limiter=lim) is None


def test_sliding_window_recovers():
    lim = rl.SlidingWindowLimiter()
    for i in range(rl.PER_COURIER_MAX):
        assert rl.check("C1", None, enabled=True, now=3000.0, limiter=lim) is None
    assert rl.check("C1", None, enabled=True, now=3000.0, limiter=lim) == rl.SCOPE_COURIER
    # Po upływie okna → znów dozwolone.
    later = 3000.0 + rl.PER_COURIER_WINDOW_S + 1
    assert rl.check("C1", None, enabled=True, now=later, limiter=lim) is None


def test_mutation_removed_limit_fails_oracle():
    """MUTATION: usunięcie limitu (limiter zawsze przepuszcza / flaga OFF) MUSI
    sczerwienić oracle floodu."""
    lim = rl.SlidingWindowLimiter()
    def flood_is_throttled(enabled):
        results = [rl.check("C9", None, enabled=enabled, now=4000.0, limiter=lim)
                   for _ in range(rl.PER_COURIER_MAX + 5)]
        return any(r is not None for r in results)
    assert flood_is_throttled(True), "ON: flood musi być dławiony (oracle)"
    # Usunięty limit (OFF = brak dławienia) → oracle floodu RED.
    lim2 = rl.SlidingWindowLimiter()
    results = [rl.check("C9", None, enabled=False, now=4000.0, limiter=lim2)
               for _ in range(rl.PER_COURIER_MAX + 5)]
    assert not any(r is not None for r in results), "OFF/usunięty limit = brak dławienia"


# ─────────────────────── gps_server integration (wiring) ────────────────────

def _patch_gps_paths(monkeypatch, tmp_path, mapping):
    """mapping: {pin: (name, cid)}. Monkeypatchuje ścieżki modułu gps_server na tmp."""
    piny = tmp_path / "kurier_piny.json"
    ids = tmp_path / "kurier_ids.json"
    kdf = tmp_path / "kurier_piny_kdf.json"
    pwa = tmp_path / "gps_positions_pwa.json"
    piny.write_text(json.dumps({p: n for p, (n, cid) in mapping.items()}), encoding="utf-8")
    ids.write_text(json.dumps({n: cid for (n, cid) in mapping.values()}), encoding="utf-8")
    monkeypatch.setattr(gps_server, "KURIER_PINY_PATH", str(piny))
    monkeypatch.setattr(gps_server, "KURIER_IDS_PATH", str(ids))
    monkeypatch.setattr(gps_server, "KURIER_PINY_KDF_PATH", str(kdf))
    monkeypatch.setattr(gps_server, "GPS_PWA_PATH", str(pwa))
    return str(kdf), str(pwa)


def test_gps_server_resolve_pin_off_parity(monkeypatch, tmp_path):
    kdf, _ = _patch_gps_paths(monkeypatch, tmp_path, {"1234": ("Marcin By", 515)})
    monkeypatch.setattr(pin_auth, "pin_kdf_enabled", lambda: False)
    assert gps_server._resolve_pin("1234") == ("515", "Marcin By")
    assert gps_server._resolve_pin("9999") == (None, None)
    import os
    assert not os.path.exists(kdf), "OFF: brak magazynu KDF (bajt-parytet)"


def test_gps_server_resolve_pin_on_lazy_rehash(monkeypatch, tmp_path):
    kdf, _ = _patch_gps_paths(monkeypatch, tmp_path, {"1234": ("Marcin By", 515)})
    monkeypatch.setattr(pin_auth, "pin_kdf_enabled", lambda: True)
    assert gps_server._resolve_pin("1234") == ("515", "Marcin By")
    store = pin_auth._load_json(kdf)
    assert "Marcin By" in store and store["Marcin By"]["kdf"] == pin_auth.KDF_NAME
    assert gps_server._resolve_pin("0000") == (None, None)  # brak bypassu


# ── do_POST end-to-end (fake handler) — dławienie 429 ──

class _FakeHandler(gps_server.GpsHandler):
    """Wywołuje GpsHandler.do_POST bez socketu (dziedziczy _json/_html; init pomija
    BaseHTTPRequestHandler.handle)."""
    def __init__(self, body, ip="1.2.3.4", xff=None):
        raw = json.dumps(body).encode("utf-8")
        self.client_address = (ip, 5555)
        self.path = "/gps"
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(raw))}
        if xff:
            self.headers["X-Forwarded-For"] = xff
        self.status = None

    def send_response(self, code):
        self.status = code

    def send_header(self, *a, **k):
        pass

    def end_headers(self):
        pass

    def log_message(self, *a, **k):
        pass

    def run(self):
        self.do_POST()
        body = self.wfile.getvalue()
        return self.status, (json.loads(body) if body else {})


def _valid_body(pin="1234"):
    return {"pin": pin, "lat": 53.13, "lon": 23.16, "accuracy": 8.0}


def test_do_post_rate_limit_429_when_on(monkeypatch, tmp_path):
    _patch_gps_paths(monkeypatch, tmp_path, {"1234": ("Marcin By", 515)})
    monkeypatch.setattr(pin_auth, "pin_kdf_enabled", lambda: False)
    monkeypatch.setattr(rl, "rate_limit_enabled", lambda: True)
    rl._limiter.reset()
    # W limicie per-kurier: pierwsze PER_COURIER_MAX → 200.
    for _ in range(rl.PER_COURIER_MAX):
        st, resp = _FakeHandler(_valid_body(), ip="9.9.9.9").run()
        assert st == 200 and resp.get("ok") is True
    # (n+1) → 429 (scope courier).
    st, resp = _FakeHandler(_valid_body(), ip="9.9.9.9").run()
    assert st == 429 and "rate limited" in resp.get("error", "")
    rl._limiter.reset()


def test_do_post_off_no_rate_limit(monkeypatch, tmp_path):
    _patch_gps_paths(monkeypatch, tmp_path, {"1234": ("Marcin By", 515)})
    monkeypatch.setattr(pin_auth, "pin_kdf_enabled", lambda: False)
    monkeypatch.setattr(rl, "rate_limit_enabled", lambda: False)
    rl._limiter.reset()
    # OFF: żaden request nie jest dławiony (bajt-parytet do_POST).
    for _ in range(rl.PER_COURIER_MAX + 5):
        st, resp = _FakeHandler(_valid_body(), ip="8.8.8.8").run()
        assert st == 200 and resp.get("ok") is True


def test_do_post_bad_pin_401_when_on(monkeypatch, tmp_path):
    _patch_gps_paths(monkeypatch, tmp_path, {"1234": ("Marcin By", 515)})
    monkeypatch.setattr(pin_auth, "pin_kdf_enabled", lambda: True)
    monkeypatch.setattr(rl, "rate_limit_enabled", lambda: True)
    rl._limiter.reset()
    st, resp = _FakeHandler(_valid_body("0000"), ip="7.7.7.7").run()
    assert st == 401 and resp.get("error") == "bad pin"
    rl._limiter.reset()
