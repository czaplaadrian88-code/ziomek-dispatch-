"""G5 — bramka testowa KANONICZNEGO PRODUCENTA snapshotu loadgov.

Spec: `docs/G5_LOADGOV_SNAPSHOT.md` + RUN3-b sekcja 3 (G5) + WB2
(`core/loadgov_snapshot.py` = czytnik, którego kontrakt tu domykamy).

Układ pliku odpowiada wymaganiom bramki:

  1. FORMAT      — snapshot spełnia kontrakt czytnika i niesie pola RUN3-b;
  2. ATOMOWOŚĆ   — zapis przerwany w połowie NIE zostawia pliku częściowego
                   (mutation: naiwny zapis w miejscu czerwieni ten test);
  3. STARTOWANIE/PADANIE producenta → czytnik STRICT 5 (nigdy loose przez
                   pomyłkę);
  4. EQUAL-TREATMENT mianownika — negatywny oracle pokazuje, że mianownik
                   ślepy na kurierów bez GPS PRZERZUCA obciążenie przez próg
                   poluzowania okna;
  5. BRAMKI      — flaga OFF, brak roli producenta, rozgrzewka, dławienie;
  6. RATCHET     — publikacja NIE MA WPŁYWU NA DECYZJĘ (brak Alarm certificate)
                   i jądro EWMA zostaje JEDNO.
"""
from __future__ import annotations

import inspect
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

# `schedule_utils` mieszka w katalogu skryptów, nie w pakiecie — dokładnie ta
# sama wstawka, którą robi u siebie `courier_resolver.dispatchable_fleet`,
# więc monkeypatch trafia w TEN SAM obiekt modułu, który czyta produkcja.
if "/root/.openclaw/workspace/scripts" not in sys.path:
    sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import schedule_utils  # noqa: E402

from dispatch_v2 import common as C
from dispatch_v2 import courier_availability as CA
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import manual_overrides as MO
from dispatch_v2.core import loadgov_ewma as LGE
from dispatch_v2.core import loadgov_publisher as LGP
from dispatch_v2.core import loadgov_snapshot as LGS
from dispatch_v2.courier_resolver import CourierState

T0 = datetime(2026, 7, 27, 18, 0, 0, tzinfo=timezone.utc)
POS = (53.1325, 23.1688)
ROLE = "dispatch-shadow"


# ──────────────────────────── harness ────────────────────────────

@pytest.fixture(autouse=True)
def _clean_producer():
    LGP.reset_state()
    yield
    LGP.reset_state()


@pytest.fixture
def snap_path(tmp_path):
    return str(tmp_path / "loadgov_snapshot.json")


def _arm(monkeypatch, *, dispatchable=15, no_position=5, computed_at=T0,
         pid=None, flag=True):
    """Uzbrój producenta: flaga ON, rola zgłoszona, statystyki puli podstawione."""
    monkeypatch.setattr(C, "ENABLE_LOADGOV_SNAPSHOT_PUBLISH", flag)
    monkeypatch.setattr(C, "load_flags", lambda: {})
    monkeypatch.setattr(
        CR, "last_fleet_filter_stats",
        lambda: {"computed_at": computed_at,
                 "pid": os.getpid() if pid is None else pid,
                 "dispatchable": dispatchable, "no_position": no_position,
                 "rejected_total": no_position})
    LGP.claim_producer_role(ROLE, now=T0)


def _observe(now, *, orders=70, couriers=15, legacy=4.667, path=None):
    return LGP.observe(now=now, active_orders=orders,
                       couriers_dispatchable=couriers, legacy_ewma=legacy,
                       path=path)


def _warm(monkeypatch, snap_path, *, orders=70, couriers=15):
    """Doprowadź serię do pierwszej publikacji (rozgrzewka = 2 próbki)."""
    assert _observe(T0, orders=orders, couriers=couriers, path=snap_path) == "warmup"
    assert _observe(T0 + timedelta(seconds=60), orders=orders,
                    couriers=couriers, path=snap_path) == "published"


# ─────────────────────── 1. FORMAT kontraktu ───────────────────────

def test_snapshot_spelnia_kontrakt_czytnika_i_pola_run3b(monkeypatch, snap_path):
    _arm(monkeypatch)
    _warm(monkeypatch, snap_path)

    body = json.loads(open(snap_path, encoding="utf-8").read())
    # a) komplet pól WYMAGANYCH przez czytnik WB2 — kontrakt jest all-or-none.
    for field in LGS.REQUIRED_FIELDS:
        assert body.get(field) is not None, f"brak wymaganego pola {field}"
    # b) pola wymienione wprost w RUN3-b sekcja 3.
    for field in ("active_orders", "eligible_couriers", "producer_role",
                  "flag_fingerprint_sha", "code_fingerprint"):
        assert body.get(field) is not None, f"brak pola kontraktu G5 {field}"
    assert body["schema"] == LGP.SCHEMA
    assert body["denominator_basis"] == "equal_treatment"
    assert body["producer_role"] == ROLE

    # c) czytnik WB2 przyjmuje go BEZ zastrzeżeń.
    snap, meta = LGS.read_snapshot(T0 + timedelta(seconds=61), path=snap_path)
    assert snap is not None
    assert meta["source"] == "snapshot"
    assert meta["generation"] == 1
    assert meta["fingerprint"] == body["fingerprint"]


def test_valid_until_przezywa_pelen_okres_publikacji(monkeypatch, snap_path):
    """`valid_until` MUSI pokryć przerwę między dwoma zapisami zdrowego
    producenta — inaczej konsument widziałby `expired` w środku normalnej pracy."""
    _arm(monkeypatch)
    _warm(monkeypatch, snap_path)
    body = json.loads(open(snap_path, encoding="utf-8").read())
    observed = datetime.fromisoformat(body["observed_at"])
    valid_until = datetime.fromisoformat(body["valid_until"])
    period = float(C.LOADGOV_SNAPSHOT_MIN_INTERVAL_S)
    assert (valid_until - observed).total_seconds() >= 2.0 * period


def test_ttl_krotszy_niz_okres_publikacji_jest_przyciety(monkeypatch, snap_path):
    """Ktoś, kto ustawi TTL poniżej dławienia, dostaje przycięcie — nie dziurę."""
    _arm(monkeypatch)
    monkeypatch.setattr(C, "LOADGOV_SNAPSHOT_TTL_S", 5.0)   # < 2 × 30 s
    _warm(monkeypatch, snap_path)
    body = json.loads(open(snap_path, encoding="utf-8").read())
    assert body["ttl_s"] == 2.0 * float(C.LOADGOV_SNAPSHOT_MIN_INTERVAL_S)


# ───────────────────────── 2. ATOMOWOŚĆ ─────────────────────────

class _PartialJson:
    """Podróbka `json`, która zapisuje POŁOWĘ treści i pada.

    Odtwarza dokładnie scenariusz, przed którym broni zapis atomowy: proces
    ginie w środku serializacji. Przy zapisie w miejscu konsument zobaczyłby
    obcięty JSON; przy temp+rename widzi poprzedni, kompletny snapshot.
    """

    @staticmethod
    def dump(obj, fh, **kwargs):
        fh.write(json.dumps(obj)[: len(json.dumps(obj)) // 2])
        raise RuntimeError("symulacja padu w trakcie serializacji")


def test_zapis_przerwany_nie_zostawia_snapshotu_czesciowego(monkeypatch, snap_path):
    _arm(monkeypatch)
    _warm(monkeypatch, snap_path)
    before = open(snap_path, encoding="utf-8").read()

    monkeypatch.setattr(LGP, "json", _PartialJson)
    assert _observe(T0 + timedelta(seconds=120), path=snap_path) == "write_error"

    # a) plik na dysku jest DOKŁADNIE poprzednim, kompletnym snapshotem;
    assert open(snap_path, encoding="utf-8").read() == before
    snap, meta = LGS.read_snapshot(T0 + timedelta(seconds=121), path=snap_path)
    assert snap is not None and meta["source"] == "snapshot"
    # b) nieudany zapis NIE przesuwa generacji (kolejny tick ponowi);
    assert meta["generation"] == 1
    # c) po nieudanym zapisie nie zostaje śmieć tymczasowy.
    leftovers = [n for n in os.listdir(os.path.dirname(snap_path))
                 if n.startswith(".loadgov_snapshot_")]
    assert leftovers == []


def test_ratchet_zapis_jest_temp_fsync_rename(monkeypatch):
    """Statyczny ratchet: nie da się „uprościć" publikacji do zapisu w miejscu."""
    src = inspect.getsource(LGP._publish)
    assert "tempfile.mkstemp" in src, "zapis musi iść przez plik tymczasowy"
    assert "os.fsync" in src, "brak fsync — rename mógłby przetrwać pustą treść"
    assert "os.replace" in src, "podmiana musi być atomowym rename"
    assert "open(path" not in src, "zapis w miejscu = snapshot częściowy"


# ────────────── 3. producent wstaje / pada → STRICT 5 ──────────────

def test_brak_producenta_daje_strict_5(snap_path):
    """Zanim producent cokolwiek zapisze, konsument bierze STRICT — nigdy loose."""
    snap, meta = LGS.read_snapshot(T0, path=snap_path)
    assert snap is None and meta["source"] == "absent"
    tol, reason = LGS.window_tol_min(T0, snapshot=snap)
    assert (tol, reason) == (C.OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN,
                             "strict_no_snapshot")


def test_producent_padl_snapshot_wygasa_i_wraca_strict_5(monkeypatch, snap_path):
    _arm(monkeypatch)
    _warm(monkeypatch, snap_path)
    ttl = float(C.LOADGOV_SNAPSHOT_TTL_S)

    # tuż przed wygaśnięciem — jeszcze ważny…
    snap, meta = LGS.read_snapshot(T0 + timedelta(seconds=60 + ttl - 1),
                                   path=snap_path)
    assert meta["source"] == "snapshot" and snap is not None

    # …a po TTL (producent nie żyje, nikt nie odświeżył) — twardo STRICT.
    snap, meta = LGS.read_snapshot(T0 + timedelta(seconds=60 + ttl + 1),
                                   path=snap_path)
    assert snap is None and meta["source"] == "expired"
    tol, reason = LGS.window_tol_min(T0, snapshot=snap)
    assert (tol, reason) == (C.OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN,
                             "strict_no_snapshot")


def test_producent_wstaje_od_nowa_z_wlasna_generacja(monkeypatch, snap_path):
    """Restart producenta = nowa ciągłość serii: generacja startuje od 1,
    a `producer_started_at` się zmienia — konsument ma po czym poznać przerwę."""
    _arm(monkeypatch)
    _warm(monkeypatch, snap_path)
    first = json.loads(open(snap_path, encoding="utf-8").read())

    LGP.reset_state()                       # ← „proces padł"
    _arm(monkeypatch, computed_at=T0 + timedelta(seconds=600))
    LGP.claim_producer_role(ROLE, now=T0 + timedelta(seconds=600))
    t = T0 + timedelta(seconds=600)
    assert _observe(t, path=snap_path) == "warmup"
    assert _observe(t + timedelta(seconds=60), path=snap_path) == "published"

    second = json.loads(open(snap_path, encoding="utf-8").read())
    assert second["generation"] == 1
    assert second["producer_started_at"] != first["producer_started_at"]
    assert second["ewma_samples"] == 2


# ───────────── 4. EQUAL-TREATMENT mianownika (oracle) ─────────────

def test_negatywny_oracle_mianownik_slepy_na_brak_gps_przerzuca_prog(
        monkeypatch, snap_path):
    """DEFEKT, który G5 zamyka.

    70 aktywnych zleceń, 15 kurierów dispatchowalnych + 5 na zmianie bez GPS.
    Mianownik ślepy na brak GPS daje 70/15 = 4,667 ≥ 4,5 (próg poluzowania
    tolerancji okna), mianownik EQUAL-TREATMENT daje 70/20 = 3,5 < 4,5.
    Ta sama flota, ten sam moment, DWA przeciwne werdykty o przeciążeniu —
    i błędny idzie w stronę LUŹNIEJSZĄ, czyli niebezpieczną.
    """
    threshold = float(C.OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD)
    _arm(monkeypatch, dispatchable=15, no_position=5)
    _warm(monkeypatch, snap_path, orders=70, couriers=15)

    body = json.loads(open(snap_path, encoding="utf-8").read())
    assert body["eligible_couriers"] == 20        # 15 z GPS + 5 bez
    assert body["couriers_dispatchable"] == 15
    assert body["couriers_no_position"] == 5
    assert body["active_orders"] == 70
    assert body["load_now"] == 3.5
    assert body["ewma"] < threshold               # equal-treatment: BEZ przeciążenia
    assert round(70 / 15, 3) >= threshold         # mianownik ślepy: przeciążenie
    # Mutacja `eligible = couriers_dispatchable` czerwieni oba asserty naraz.


def test_dispatchable_fleet_liczy_odrzuconych_za_brak_pozycji(monkeypatch):
    """Źródłem liczby jest `courier_resolver` — właściciel decyzji o dostępności."""
    monkeypatch.setattr(schedule_utils, "load_schedule",
                        lambda: {"Bartek O": {"start": "00:00", "end": "23:59"},
                                 "Adrian R": {"start": "00:00", "end": "23:59"}})
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)
    monkeypatch.setattr(MO, "get_excluded", lambda: [])
    monkeypatch.setattr(MO, "get_excluded_cids", lambda: set())
    monkeypatch.setattr(MO, "get_working", lambda: {})

    fleet = {
        "123": CourierState(courier_id="123", pos=POS, pos_source="gps",
                            name="Bartek O"),
        "999": CourierState(courier_id="999", pos=None, pos_source="none",
                            name="Adrian R"),
    }
    result = CR.dispatchable_fleet(fleet=fleet)
    stats = CR.last_fleet_filter_stats()

    assert [c.courier_id for c in result] == ["123"]
    assert stats["dispatchable"] == 1
    assert stats["no_position"] == 1          # kurier bez GPS NIE znika z rachunku
    assert stats["pid"] == os.getpid()
    assert isinstance(stats["computed_at"], datetime)


def test_kontrakt_availability_liczy_tylko_dostepnych_bez_pozycji(
        monkeypatch, tmp_path):
    """Ścieżka ŻYWA (ENABLE_CID_AVAILABILITY_CONTRACT ON): `no_position` liczy
    WYŁĄCZNIE kurierów, którzy przeszli bramkę dostępności — czyli dokładnie
    lukę equal-treatment, bez doliczania osób spoza zmiany."""
    overrides = tmp_path / "manual_overrides.json"
    names = tmp_path / "grafik_full_names.json"
    overrides.write_text("{}", encoding="utf-8")
    names.write_text(json.dumps({"Na Zmianie": 400, "Poza Zmiana": 401}),
                     encoding="utf-8")
    monkeypatch.setattr(C, "ENABLE_CID_AVAILABILITY_CONTRACT", True)
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(MO, "OVERRIDES_PATH", str(overrides))
    monkeypatch.setattr(CR, "GRAFIK_FULL_NAMES_PATH", str(names))
    monkeypatch.setattr(schedule_utils, "load_schedule",
                        lambda: {"Na Zmianie": {"start": "00:00", "end": "23:59"},
                                 "Poza Zmiana": None})
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)

    fleet = {
        "400": CourierState(courier_id="400", pos=None, pos_source="none",
                            name="Na Zmianie"),
        "401": CourierState(courier_id="401", pos=None, pos_source="none",
                            name="Poza Zmiana"),
    }
    assert CR.dispatchable_fleet(fleet=fleet) == []
    stats = CR.last_fleet_filter_stats()
    assert stats["dispatchable"] == 0
    assert stats["no_position"] == 1     # tylko 400; 401 odpada na dostępności
    assert stats["rejected_total"] == 2


def test_statystyki_puli_z_innego_procesu_lub_stare_blokuja_publikacje(
        monkeypatch, snap_path):
    """Mianownik z innego stanu świata niż licznik = brak snapshotu (STRICT)."""
    _arm(monkeypatch, pid=os.getpid() + 1)
    assert _observe(T0, path=snap_path) == "no_fleet_stats"

    LGP.reset_state()
    _arm(monkeypatch, computed_at=T0 - timedelta(
        seconds=float(C.LOADGOV_FLEET_STATS_MAX_AGE_S) + 1))
    assert _observe(T0, path=snap_path) == "stale_fleet_stats"
    assert not os.path.exists(snap_path)


def test_blad_zrodla_statystyk_nie_dotyka_decyzji(monkeypatch, snap_path):
    _arm(monkeypatch)

    def _boom():
        raise RuntimeError("courier_resolver niedostępny")

    monkeypatch.setattr(CR, "last_fleet_filter_stats", _boom)
    assert _observe(T0, path=snap_path) == "no_fleet_stats"
    assert not os.path.exists(snap_path)


# ─────────────────────── 5. bramki publikacji ───────────────────────

def test_flaga_off_nie_publikuje_i_nie_rusza_serii(monkeypatch, snap_path):
    _arm(monkeypatch, flag=False)
    assert _observe(T0, path=snap_path) == "flag_off"
    assert not os.path.exists(snap_path)
    assert LGP.state_snapshot()["series"]["samples"] == 0


def test_proces_bez_zgloszonej_roli_nie_publikuje(monkeypatch, snap_path):
    """Czasówka / plan-recheck / panel-quote też wołają `assess_order` — ich
    EWMA po pierwszym ticku RÓWNA SIĘ próbce chwilowej, więc nie wolno im
    publikować (to jest ten „drugi niestabilny governor")."""
    _arm(monkeypatch)
    LGP.reset_state()                      # ← proces bez claim
    monkeypatch.setattr(C, "ENABLE_LOADGOV_SNAPSHOT_PUBLISH", True)
    assert LGP.is_producer() is False
    assert _observe(T0, path=snap_path) == "not_producer"
    assert not os.path.exists(snap_path)


def test_niewlasciwa_rola_nie_publikuje(monkeypatch, snap_path):
    _arm(monkeypatch)
    LGP.claim_producer_role("czasowka", now=T0)
    assert LGP.is_producer() is False
    assert _observe(T0, path=snap_path) == "not_producer"


def test_prawo_do_publikacji_nie_dziedziczy_sie_po_forku(monkeypatch, snap_path):
    _arm(monkeypatch)
    monkeypatch.setitem(LGP._PRODUCER, "pid", os.getpid() + 1)   # „potomek"
    assert LGP.is_producer() is False
    assert _observe(T0, path=snap_path) == "not_producer"


def test_jedna_probka_to_nie_ewma_wiec_brak_publikacji(monkeypatch, snap_path):
    _arm(monkeypatch)
    assert _observe(T0, path=snap_path) == "warmup"
    assert not os.path.exists(snap_path)
    assert LGP.state_snapshot()["series"]["samples"] == 1


def test_dlawienie_zapisu_nie_gubi_probek_serii(monkeypatch, snap_path):
    """Dławiony jest ZAPIS, nie seria: EWMA konsumuje każdą próbkę."""
    _arm(monkeypatch)
    _warm(monkeypatch, snap_path)
    period = float(C.LOADGOV_SNAPSHOT_MIN_INTERVAL_S)

    assert _observe(T0 + timedelta(seconds=61), path=snap_path) == "throttled"
    assert LGP.state_snapshot()["series"]["samples"] == 3
    body = json.loads(open(snap_path, encoding="utf-8").read())
    assert body["generation"] == 1

    assert _observe(T0 + timedelta(seconds=61 + period),
                    path=snap_path) == "published"
    body = json.loads(open(snap_path, encoding="utf-8").read())
    assert body["generation"] == 2
    assert body["ewma_samples"] == 4


def test_brak_licznika_lub_mianownika_nie_publikuje(monkeypatch, snap_path):
    _arm(monkeypatch)
    assert _observe(T0, orders=None, path=snap_path) == "no_orders"
    LGP.reset_state()
    _arm(monkeypatch, no_position=0)
    assert _observe(T0, couriers=0, path=snap_path) == "no_eligible"
    assert not os.path.exists(snap_path)


# ──────────────── 6. ratchety: zero wpływu na decyzję ────────────────

def test_snapshot_ponad_progiem_NADAL_daje_strict(monkeypatch, snap_path):
    """OD-04: samo wysokie EWMA NIE uprawnia do tolerancji 10. Dopóki nie ma
    producenta Alarm certificate, publikacja snapshotu nie zmienia ŻADNEJ
    decyzji — dlatego kill-switch producenta jest dziś niedecyzyjny."""
    _arm(monkeypatch, dispatchable=5, no_position=0)
    _warm(monkeypatch, snap_path, orders=100, couriers=5)    # load 20 ≫ 4,5

    snap, meta = LGS.read_snapshot(T0 + timedelta(seconds=61), path=snap_path)
    assert snap is not None and snap["ewma"] >= C.OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD
    tol, reason = LGS.window_tol_min(T0, snapshot=snap)
    assert tol == C.OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN
    assert reason == "strict_no_alarm_certificate"


def test_integracja_licznik_snapshotu_zgadza_sie_z_seria_legacy(
        monkeypatch, snap_path):
    """Producent i seria legacy patrzą na TEN SAM licznik i ten sam moment.

    Odtwarza sekwencję z `_assess_order_impl`: `_loadgov_compute` (seria
    legacy) → `observe` (snapshot). Gdyby producent czytał `orders_state.json`
    po swojemu, licznik snapshotu mógłby pochodzić z innego stanu świata niż
    EWMA legacy — ten test by to złapał.
    """
    from dispatch_v2 import dispatch_pipeline as DP

    monkeypatch.setattr(DP, "_loadgov_active_orders", lambda now: 70)
    monkeypatch.setitem(DP._LOADGOV_STATE, "ts", None)
    monkeypatch.setitem(DP._LOADGOV_STATE, "ewma", None)
    _arm(monkeypatch, dispatchable=15, no_position=5)

    fleet = {str(i): object() for i in range(15)}
    for step, t in enumerate((T0, T0 + timedelta(seconds=60))):
        now_, ewma_legacy, orders, couriers = DP._loadgov_compute(fleet, t)
        reason = LGP.observe(now=t, active_orders=orders,
                             couriers_dispatchable=couriers,
                             legacy_ewma=ewma_legacy, path=snap_path)
        assert reason == ("warmup" if step == 0 else "published")

    body = json.loads(open(snap_path, encoding="utf-8").read())
    assert body["active_orders"] == 70
    assert body["couriers_dispatchable"] == 15
    assert body["legacy_ewma"] == DP._LOADGOV_STATE["ewma"] == round(70 / 15, 3)
    assert body["ewma"] == 3.5                    # 70 / (15 + 5)
    # Rozjazd, dla którego snapshot niesie obie liczby — materiał do decyzji
    # ownera o unifikacji mianownika serii legacy.
    assert body["legacy_ewma"] > body["ewma"]


def test_ewma_step_parytet_z_zamrozona_matematyka_legacy():
    """Jądro EWMA jest JEDNO. Gdyby ktoś rozjechał wygładzanie serii legacy
    i serii equal-treatment, ten test to złapie na siatce parametrów."""
    def _frozen_legacy(prev, prev_ts, load_now, now, tau_min):
        # 1:1 kopia inline'u SP-B2-LOADGOV sprzed G5 (dispatch_pipeline).
        if prev is None or prev_ts is None:
            return load_now
        dt_min = max(0.0, (now - prev_ts).total_seconds() / 60.0)
        tau = max(0.1, float(tau_min))
        alpha = 1.0 - math.exp(-dt_min / tau)
        return round(alpha * load_now + (1.0 - alpha) * prev, 3)

    cases = 0
    for prev in (None, 0.0, 1.234, 9.87):
        for dt_s in (-30, 0, 1, 45, 900, 7200):
            for sample in (0.0, 0.5, 3.7, 12.0):
                for tau in (0.0, 0.1, 15.0, 60.0):
                    prev_ts = None if prev is None else T0
                    now = T0 + timedelta(seconds=dt_s)
                    assert LGE.ewma_step(prev, prev_ts, sample, now, tau) == \
                        _frozen_legacy(prev, prev_ts, sample, now, tau)
                    cases += 1
    assert cases == 4 * 6 * 4 * 4


def test_ratchet_silnik_uzywa_wspolnego_jadra_ewma():
    """Nie da się cicho wrócić do własnej kopii alfy w `_loadgov_compute`."""
    from dispatch_v2 import dispatch_pipeline as DP

    src = inspect.getsource(DP._loadgov_compute)
    assert "_loadgov_ewma_step(" in src, "seria legacy musi wołać wspólne jądro"
    assert "math.exp" not in src, "własna kopia alfy = dwie polityki wygładzania"
    assert DP._loadgov_ewma_step is LGE.ewma_step


def test_ratchet_producent_nie_publikuje_z_procesu_bez_roli():
    """Statyczny strażnik bramki roli — usunięcie `is_producer()` z `observe`
    otworzyłoby publikację czasówce i plan-recheckowi. Bramka w `enabled()`
    jest optymalizacją call-site'u, więc NIE MOŻE być jedynym sprawdzeniem."""
    src = inspect.getsource(LGP.observe)
    assert "is_producer()" in src
    assert "decision_flag(FLAG_PUBLISH)" in src


def test_sciezka_decyzji_omija_producenta_gdy_bezczynny(monkeypatch):
    """Przy kill-switchu OFF ścieżka decyzji nie buduje nawet argumentów ani
    wpisu w buforze efektów (`enabled()` short-circuituje przed `divert`)."""
    from dispatch_v2 import dispatch_pipeline as DP

    monkeypatch.setattr(C, "ENABLE_LOADGOV_SNAPSHOT_PUBLISH", False)
    monkeypatch.setattr(C, "load_flags", lambda: {})
    LGP.claim_producer_role(ROLE, now=T0)
    assert LGP.enabled() is False

    monkeypatch.setattr(C, "ENABLE_LOADGOV_SNAPSHOT_PUBLISH", True)
    assert LGP.enabled() is True
    LGP.reset_state()
    assert LGP.enabled() is False          # bez zgłoszonej roli — bezczynny

    src = inspect.getsource(DP._assess_order_impl)
    assert "_loadgov_pub.enabled()" in src
    assert "_EB.divert(_loadgov_pub.observe" in src


def test_flaga_producenta_jest_zarejestrowana_i_domyslnie_off():
    assert C.ENABLE_LOADGOV_SNAPSHOT_PUBLISH is False
    registry = json.loads(
        open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "tools",
            "flag_lifecycle_registry.json"), encoding="utf-8").read())
    entry = registry["flags"]["ENABLE_LOADGOV_SNAPSHOT_PUBLISH"]
    assert entry["default"] is False
    # Dziś NIEdecyzyjna (czytnik zwraca strict niezależnie od EWMA). Gdy
    # powstanie producent Alarm certificate — MUSI wejść do ETAP4.
    assert "ENABLE_LOADGOV_SNAPSHOT_PUBLISH" not in C.ETAP4_DECISION_FLAGS
