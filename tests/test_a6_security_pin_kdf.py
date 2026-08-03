"""A-6 SECURITY P0 (2026-08-02) — bramka testowa Przykazania #0 dla:
  (1) PIN KDF (PBKDF2-HMAC-SHA256) + sól per-user + dual-read + lazy re-hash
      (`identity/pin_auth`),
  (2) rate-limit floodu pozycji GPS per-kurier/per-IP (`gps_rate_limit`),
  wraz z wiringiem w `gps_server` (_resolve_pin dual-read + do_POST 429).

Zawiera: NEGATYWNY ORACLE (reprodukuje defekt: plaintext / brak dławienia),
MUTATION PROBE (KDF→plaintext oraz usunięcie limitu → oracle RED), PARYTET ON≠OFF.

Izolacja: wszystkie ścieżki w tmp_path (HERMETIC-GUARD spełniony). ZERO PIN-ów z
żywego kurier_piny.json — wyłącznie syntetyczne PIN-y w tmp. Żaden test nie loguje
ani nie asertuje sekretu poza syntetycznym PIN-em użytym lokalnie do weryfikacji.
"""
import io
import json

import pytest

from dispatch_v2 import gps_rate_limit as rl
from dispatch_v2 import gps_server
from dispatch_v2.identity import pin_auth


# ─────────────────────────── PIN KDF — helpers ──────────────────────────────

def _mk_stores(tmp_path, mapping):
    piny = tmp_path / "kurier_piny.json"
    kdf = tmp_path / "kurier_piny_kdf.json"
    piny.write_text(json.dumps(mapping), encoding="utf-8")
    return str(piny), str(kdf)


def _no_plaintext_oracle(kdf_path, pins):
    """SECURITY ORACLE: magazyn KDF nie zawiera plaintextu PIN-u, każdy rekord ma
    niezerowy koszt KDF + sól, a sole są RÓŻNE per-user. Raisuje AssertionError gdy
    naruszone (→ RED pod mutacją KDF→plaintext)."""
    store = pin_auth._load_json(kdf_path)
    blob = json.dumps(store)
    for pin in pins:
        assert pin not in blob, "PLAINTEXT PIN w magazynie KDF (mutacja/regresja)"
    salts = []
    for name, rec in store.items():
        assert rec.get("kdf") == pin_auth.KDF_NAME, f"{name}: obcy/kdf-less rekord"
        assert int(rec.get("iter", 0)) >= pin_auth._MIN_ITER_FLOOR, \
            f"{name}: koszt KDF poniżej floor (osłabienie)"
        assert len(str(rec.get("salt", ""))) >= pin_auth.SALT_BYTES * 2, \
            f"{name}: sól za krótka/brak"
        salts.append(rec["salt"])
    assert len(salts) == len(set(salts)), "sole NIE są per-user unikatowe"


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
    piny, kdf = _mk_stores(tmp_path, {"1234": "Marcin By", "5678": "Jan Ko"})
    pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=True)
    pin_auth.resolve_pin("5678", piny_path=piny, kdf_path=kdf, use_kdf=True)
    _no_plaintext_oracle(kdf, ["1234", "5678"])  # sprawdza sole różne + iter>=floor


def test_security_oracle_no_plaintext_in_store(tmp_path):
    piny, kdf = _mk_stores(tmp_path, {"1234": "Marcin By", "5678": "Jan Ko"})
    pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=True)
    pin_auth.resolve_pin("5678", piny_path=piny, kdf_path=kdf, use_kdf=True)
    _no_plaintext_oracle(kdf, ["1234", "5678"])  # PASS na prawdziwym KDF


def test_mutation_plaintext_store_fails_oracle(tmp_path, monkeypatch):
    """MUTATION: gdyby make_record zapisywał PLAINTEXT (osłabienie KDF), security
    oracle MUSI sczerwienieć."""
    def _plaintext_record(pin):
        return {"kdf": pin_auth.KDF_NAME, "salt": "00", "iter": 0, "hash": str(pin)}
    monkeypatch.setattr(pin_auth, "make_record", _plaintext_record)
    piny, kdf = _mk_stores(tmp_path, {"1234": "Marcin By"})
    pin_auth.resolve_pin("1234", piny_path=piny, kdf_path=kdf, use_kdf=True)
    with pytest.raises(AssertionError):
        _no_plaintext_oracle(kdf, ["1234"])


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
