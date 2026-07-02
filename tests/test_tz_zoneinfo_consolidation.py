"""Lane TZ-consolidate (audyt 2.0 findingi C+D) — strażnik konsolidacji stref.

Cel: fixed-offset Warsaw (`timezone(timedelta(hours=2))` / `WARSAW_OFFSET_HOURS` /
stała `"+02:00"`) → `ZoneInfo("Europe/Warsaw")` (DST-safe CET/CEST). Bomby uzbrajają
się po końcu DST (25-26.10.2026): zimą CET=+1, więc stały +2 kłamie o 1h.

Testy są BEHAWIORALNE (C13), nie string-match:
  (a) ZIMOWY kill-test staged gastro_assign — logika HH:MM daje poprawny wynik
      (bez fałszywego „+1 dzień"); MUTATION-CHECK: podmiana ZoneInfo→stały +2
      MUSI zepsuć wynik (test to udowadnia = strażnik ma zęby).
  (b) LETNI parytet — stara (fixed +2) ↔ nowa (ZoneInfo) implementacja dają
      IDENTYCZNE wyniki na próbce realnych stempli (dziś CEST=+2 ⇒ neutralne).
  (c) GREP-RATCHET — skan repo *.py na fixed-offset TZ z jawną allowlistą;
      zero NOWYCH wystąpień, allowlista może się tylko kurczyć.

WAŻNE (izolacja): conftest hardkoduje `_SCRIPTS_ROOT=.../scripts` na sys.path, więc
`import dispatch_v2.tools.X` celuje w KANONICZNE repo, nie w ten worktree. Dlatego
edytowane pliki ładujemy PO ŚCIEŻCE z worktree (importlib), by walidować MOJE zmiany.
`sequential_replay.py` robi `os.execv` przy imporcie (+ globalne monkeypatche
OR-Tools/ThreadPool) → NIGDY nie importujemy go w procesie; pokrywamy go ratchetem
i równoważnością na poziomie wyrażenia (parytet instancji tz).
"""
import importlib.util
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest

# ── ścieżki worktree (NIE kanon) ─────────────────────────────────────────
_WT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_WT_ROOT, "tools")
_STAGED_GASTRO = os.path.join(_WT_ROOT, "deploy_staging", "scripts", "gastro_assign.py")

WARSAW = ZoneInfo("Europe/Warsaw")
FIXED2 = timezone(timedelta(hours=2))          # stary, BŁĘDNY zimą offset (baseline mutacji)
SUMMER = datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)   # CEST 10:30 (DST) — parytet
WINTER = datetime(2026, 12, 15, 9, 30, tzinfo=timezone.utc)  # CET 10:30 (po DST) — kill


def _load_by_path(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════════
# (a) STAGED gastro_assign — ZIMOWY kill-test + MUTATION-CHECK + LETNI parytet
# ══════════════════════════════════════════════════════════════════════════
def _run_gastro_time_arg(mod, fixed_utc, time_arg, monkeypatch):
    """Napędza REALNE main() offline (zero sieci): zwraca `time` (minuty) wysłane
    do panelu. Zamraża `now` na `fixed_utc`; podmienia login/assign/get_kurier_id."""
    captured = {}

    def _fake_assign(order_id, kurier_id, time_minutes, csrf):
        captured["tmin"] = time_minutes
        return {"success": True}

    class _FrozenNow:
        def now(self, tz=None):
            return fixed_utc.astimezone(tz) if tz is not None else fixed_utc.replace(tzinfo=None)

    monkeypatch.setattr(mod, "assign", _fake_assign)
    monkeypatch.setattr(mod, "login", lambda: "csrf-test")
    monkeypatch.setattr(mod, "get_kurier_id", lambda name: 999)
    monkeypatch.setattr(mod, "datetime", _FrozenNow())
    monkeypatch.setattr(sys, "argv", ["gastro_assign", "--id", "999", "--kurier", "X", "--time", time_arg])
    mod.main()
    return captured["tmin"]


@pytest.fixture()
def gastro():
    assert os.path.exists(_STAGED_GASTRO), f"brak staged gastro_assign: {_STAGED_GASTRO}"
    return _load_by_path(_STAGED_GASTRO, "gastro_assign_staged_wt")


def test_gastro_staged_uses_zoneinfo(gastro):
    # kotwica: staged kopia trzyma ZoneInfo, nie stały offset.
    assert gastro._WARSAW is not None
    assert getattr(gastro._WARSAW, "key", None) == "Europe/Warsaw"


def test_gastro_winter_hhmm_no_false_next_day(gastro, monkeypatch):
    # ZIMA (CET=+1): realny Warsaw wall-clock 10:30. Odbiór 10:45 (za 15 min).
    # Z ZoneInfo now=10:30 → 10:45>10:30 → 15 min (POPRAWNIE).
    tmin = _run_gastro_time_arg(gastro, WINTER, "10:45", monkeypatch)
    assert tmin == 15, f"zimowy HH:MM ma dać 15 min, dał {tmin}"


def test_gastro_winter_mutation_reintroduces_bug(gastro, monkeypatch):
    # MUTATION-CHECK (C13): ZoneInfo→stały +2 → zimą now liczone jako 11:30,
    # odbiór 10:45<11:30 → guard „+1 dzień" → ~1410 min zamiast 15.
    # Poprawny (ZoneInfo) daje 15; zmutowany daje wartość ogromną → strażnik gryzie.
    good = _run_gastro_time_arg(gastro, WINTER, "10:45", monkeypatch)
    assert good == 15
    monkeypatch.setattr(gastro, "_WARSAW", FIXED2)
    bug = _run_gastro_time_arg(gastro, WINTER, "10:45", monkeypatch)
    assert bug == 1395, f"mutacja (+2) zimą ma dać fałszywy +1 dzień (~1395 min), dała {bug}"
    assert bug > 1000 and good != bug  # dowód: rewers fixu przywraca bombę


def test_gastro_summer_parity_zoneinfo_equals_fixed_offset(gastro, monkeypatch):
    # LATO (CEST=+2): ZoneInfo i stały +2 IDENTYCZNE ⇒ zmiana neutralna dziś.
    with_zoneinfo = _run_gastro_time_arg(gastro, SUMMER, "10:45", monkeypatch)
    monkeypatch.setattr(gastro, "_WARSAW", FIXED2)
    with_fixed = _run_gastro_time_arg(gastro, SUMMER, "10:45", monkeypatch)
    assert with_zoneinfo == with_fixed == 15


# ══════════════════════════════════════════════════════════════════════════
# (b) LETNI parytet + ZIMOWA poprawność — 5 pozostałych plików partycji
# ══════════════════════════════════════════════════════════════════════════
def test_freshness_monitor_tz(monkeypatch):
    m = _load_by_path(os.path.join(_TOOLS, "freshness_shadow_monitor.py"), "freshness_wt")
    assert getattr(m.WARSAW, "key", None) == "Europe/Warsaw"
    # naive stempel → interpretowany jako Warsaw wall-clock (offset zależny od DST)
    assert m._pt("2026-07-15T10:30:00").utcoffset() == timedelta(hours=2)   # lato CEST
    assert m._pt("2026-12-15T10:30:00").utcoffset() == timedelta(hours=1)   # zima CET (był +2 = bug)
    # LETNI parytet dziennego bucketa (stara +2 ↔ nowa ZoneInfo): identyczne
    iso = "2026-07-15T21:30:00+00:00"
    new = m._pt(iso).astimezone(m.WARSAW).strftime("%Y-%m-%d")
    old = m._pt(iso).astimezone(FIXED2).strftime("%Y-%m-%d")
    assert new == old
    # ZIMOWA poprawność: 22:30 UTC 15.12 = 23:30 Warsaw (CET) = ten dzień;
    # stary +2 dałby 00:30 następnego dnia (zły bucket).
    w = "2026-12-15T22:30:00+00:00"
    assert m._pt(w).astimezone(m.WARSAW).strftime("%Y-%m-%d") == "2026-12-15"
    assert m._pt(w).astimezone(FIXED2).strftime("%Y-%m-%d") == "2026-12-16"


def test_reassignment_shadow_tw(monkeypatch):
    m = _load_by_path(os.path.join(_TOOLS, "reassignment_shadow.py"), "reassignment_wt")
    assert getattr(m.WAR, "key", None) == "Europe/Warsaw"
    # LETNI parytet: tw() == stara implementacja (wall-clock naive)
    iso = "2026-07-15T08:30:00Z"
    old = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(FIXED2).replace(tzinfo=None)
    assert m.tw(iso) == old
    # ZIMA: tw() używane WYŁĄCZNIE w różnicach (okno świeżości 3h) → wynik
    # niezależny od offsetu. RÓŻNICA dwóch stempli identyczna stara↔nowa.
    a, b = "2026-12-15T13:00:00Z", "2026-12-15T11:15:00Z"
    d_new = m.tw(a) - m.tw(b)
    d_old = (datetime.fromisoformat(a.replace("Z", "+00:00")).astimezone(FIXED2)
             - datetime.fromisoformat(b.replace("Z", "+00:00")).astimezone(FIXED2))
    assert d_new == d_old == timedelta(hours=1, minutes=45)


def test_common_to_warsaw(monkeypatch):
    m = _load_by_path(os.path.join(_WT_ROOT, "sprint2_analysis", "_common.py"), "sprint2_common_wt")
    assert getattr(m.WARSAW, "key", None) == "Europe/Warsaw"
    # LETNI parytet: to_warsaw == (utc + 2h) naive
    old_summer = (SUMMER.astimezone(timezone.utc) + timedelta(hours=2)).replace(tzinfo=None)
    assert m.to_warsaw(SUMMER) == old_summer
    # ZIMA poprawnie CET (+1): godzina bucketowania peak = 10, nie 11 (był bug +2)
    assert m.to_warsaw(WINTER).hour == 10
    assert (WINTER.astimezone(timezone.utc) + timedelta(hours=2)).replace(tzinfo=None).hour == 11


def test_monitor_refloor_hhmm(monkeypatch):
    m = _load_by_path(os.path.join(_TOOLS, "monitor_refloor_peak_2026_05_31.py"), "monitor_refloor_wt")
    assert getattr(m.WARSAW, "key", None) == "Europe/Warsaw"
    # LETNI parytet HH:MM (stara +2 ↔ nowa)
    assert m._hhmm_warsaw(SUMMER) == SUMMER.astimezone(FIXED2).strftime("%H:%M") == "10:30"
    # ZIMOWA poprawność: 10:30 (CET), nie 11:30 (był bug +2)
    assert m._hhmm_warsaw(WINTER) == "10:30"
    assert SUMMER.astimezone(FIXED2).strftime("%H:%M") == "10:30"


def test_sequential_replay_expression_equivalence():
    # sequential_replay NIE importowalny (os.execv + globalne patche OR-Tools).
    # Pokrywam SEMANTYKĘ zmiany na poziomie wyrażenia (ta sama instancja tz):
    # (1) granice zmiany 10:00/22:00 — lato identyczny INSTANT, zima poprawny.
    d = "2026-07-15"
    new_s = datetime.fromisoformat(f"{d}T10:00:00").replace(tzinfo=WARSAW)
    old_s = datetime.fromisoformat(f"{d}T10:00:00+02:00")
    assert new_s.astimezone(timezone.utc) == old_s.astimezone(timezone.utc)   # lato: ten sam instant
    dw = "2026-12-15"
    new_w = datetime.fromisoformat(f"{dw}T10:00:00").replace(tzinfo=WARSAW)
    old_w = datetime.fromisoformat(f"{dw}T10:00:00+02:00")
    assert new_w.astimezone(timezone.utc).hour == 9    # CET: 10:00 Warsaw = 09:00 UTC (poprawnie)
    assert old_w.astimezone(timezone.utc).hour == 8    # stary +2 = 08:00 UTC (błąd)
    # (2) _per_hour bucket: lato = int(src[11:13])+2, zima poprawnie +1
    src_s = "2026-07-15T13:00:00+00:00"
    assert datetime.fromisoformat(src_s).astimezone(WARSAW).hour == (int(src_s[11:13]) + 2) % 24 == 15
    src_w = "2026-12-15T13:00:00+00:00"
    assert datetime.fromisoformat(src_w).astimezone(WARSAW).hour == 14        # CET (był 15 = bug)


# ══════════════════════════════════════════════════════════════════════════
# (b') shadow_outcome_enricher — martwa stała usunięta + math offset-niezależny
# ══════════════════════════════════════════════════════════════════════════
def test_enricher_no_fixed_offset_constant():
    m = _load_by_path(os.path.join(_TOOLS, "shadow_outcome_enricher.py"), "enricher_wt")
    assert not hasattr(m, "WARSAW_OFFSET_HOURS"), "stała fixed-offset musi zniknąć"
    assert getattr(m.WARSAW, "key", None) == "Europe/Warsaw"


@pytest.mark.parametrize("day", ["2026-07-15", "2026-12-15"])  # lato + zima → identyczne
def test_enricher_deltas_offset_independent(day):
    m = _load_by_path(os.path.join(_TOOLS, "shadow_outcome_enricher.py"), "enricher_wt")
    decision = {
        "order_id": "999", "ts": f"{day}T08:00:00+00:00",
        "verdict": "PROPOSE", "reason": "x",
        "best": {"courier_id": "5", "name": "Kurier", "travel_min": 10.0, "drive_min": 8.0},
    }
    outcomes = {
        "assigned": {"courier_id": "5", "created_at_utc": f"{day}T08:05:00+00:00"},
        "picked_up": {"courier_id": "5", "created_at_utc": f"{day}T08:20:00+00:00"},
        "delivered": {"created_at_utc": f"{day}T08:45:00+00:00"},
    }
    r = m.enrich_record(decision, outcomes)
    assert r is not None
    # deltas liczone między stemplami z offsetem-ISO ⇒ niezależne od strefy/DST
    assert r["actual"]["actual_kurier_to_pickup_min"] == 20.0
    assert r["actual"]["actual_assign_to_pickup_min"] == 15.0
    assert r["actual"]["actual_pickup_to_delivery_min"] == 25.0


# ══════════════════════════════════════════════════════════════════════════
# (c) GREP-RATCHET — zero NOWYCH fixed-offset TZ; allowlista tylko się kurczy
# ══════════════════════════════════════════════════════════════════════════
# Konstrukcje fixed-offset TZ (celowo wąskie — nie łapią gołego timedelta(hours=2)
# jako delty ani stringów ISO „...+02:00" wewnątrz większego literału).
_FIXED_TZ = re.compile(
    r"timezone\(\s*timedelta\(\s*hours\s*=\s*[12]\s*\)\s*\)"   # timezone(timedelta(hours=1|2))
    r"|WARSAW_OFFSET_HOURS"
    r"|WARSAW_OFFSET\s*="
    r"|=\s*[\"']\+0[12]:00[\"']"                               # NAME = "+02:00" (stała-offset)
)
# Pliki, którym WOLNO mieć fixed-offset (snapshot z pełnego grepa 2026-07-02).
# Allowlista może się TYLKO kurczyć — dodanie NOWEGO pliku = test PADA.
_ALLOWLIST = {
    "tools/ontime_lib.py",                       # POPRAWNY wzór: para CET/CEST (warsaw_tz_for, DST-aware)
    # drive_speed_overshoot_verdict.py skonwertowany na ZoneInfo (lane TZ-drobnica FALA-2) → zdjęty z allowlisty.
}
_SELF = "tests/test_tz_zoneinfo_consolidation.py"
# Testy-strażnicy TZ MUSZĄ trzymać stały offset jako BAZĘ MUTACJI (podmieniają
# ZoneInfo→+2 by udowodnić że fix ma zęby). To NIE produkcyjny fixed-offset →
# osobna kategoria od allowlisty (która = pliki produkcyjne i tylko się kurczy).
_GUARDIAN_TESTS = {
    _SELF,
    "tests/test_drive_speed_overshoot_tz.py",   # lane TZ-drobnica FALA-2
    "tests/test_grafik_fetch_schedule.py",      # lane S2 grafik: trzyma literał
                                                # fixed-offset jako NEGATYWNĄ asercję
                                                # (H1b: musi zniknąć ze staged źródła).
}
_MY_PARTITION = {
    "tools/shadow_outcome_enricher.py", "tools/freshness_shadow_monitor.py",
    "tools/reassignment_shadow.py", "tools/sequential_replay.py",
    "tools/monitor_refloor_peak_2026_05_31.py", "sprint2_analysis/_common.py",
}


def _scan_fixed_offset():
    offenders = set()
    for dirpath, dirnames, filenames in os.walk(_WT_ROOT):
        if any(seg in dirpath for seg in ("/.git", "/__pycache__", "/eod_drafts", "/fixtures")):
            continue
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "eod_drafts", "fixtures")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), _WT_ROOT)
            if rel in _GUARDIAN_TESTS:
                continue
            try:
                with open(os.path.join(dirpath, fn), encoding="utf-8", errors="ignore") as fh:
                    src = fh.read()
            except OSError:
                continue
            if _FIXED_TZ.search(src):
                offenders.add(rel)
    return offenders


def test_ratchet_no_new_fixed_offset_tz():
    offenders = _scan_fixed_offset()
    extra = offenders - _ALLOWLIST
    assert not extra, (
        "NOWE fixed-offset TZ (skonwertuj na ZoneInfo('Europe/Warsaw')): "
        + ", ".join(sorted(extra))
    )


def test_ratchet_partition_files_are_clean():
    # moje 6 plików partycji NIE mogą już zawierać fixed-offset (po fixie znikają)
    offenders = _scan_fixed_offset()
    dirty = _MY_PARTITION & offenders
    assert not dirty, "plik partycji nadal ma fixed-offset: " + ", ".join(sorted(dirty))


# ══════════════════════════════════════════════════════════════════════════
# (c') GREP-RATCHET dla ŻYWYCH plików grafiku POZA repo (lane S2, finding H)
# ══════════════════════════════════════════════════════════════════════════
# Blind-spot ratcheta (zgłoszony w tz-drobnica_raport §Część 1): _scan_fixed_offset
# chodzi po _WT_ROOT = repo dispatch_v2. Pliki grafiku żyją O POZIOM WYŻEJ w
# `scripts/` (poza repo) → ratchet ICH NIE WIDZI. Skan JAWNEJ LISTY (nie rekursja
# po scripts/, żeby nie łapać cudzych/nieznanych plików) domyka tę dziurę.
_EXTERNAL_SCRIPTS = {
    "scripts/gastro_assign.py":  "/root/.openclaw/workspace/scripts/gastro_assign.py",
    "scripts/fetch_schedule.py": "/root/.openclaw/workspace/scripts/fetch_schedule.py",
    "scripts/schedule_utils.py": "/root/.openclaw/workspace/scripts/schedule_utils.py",
}
# 2026-07-02 ~13:35: żywy schedule_utils.py PODMIENIONY na staged (fix grafik-h,
# GO Adriana, .bak-pre-grafik-h-2026-07-02) — fixed-offset fallback usunięty,
# allowlista skurczona do ZERA (może tylko się kurczyć; nowy wpis = nowa bomba TZ).
_EXTERNAL_ALLOWLIST = set()


def test_ratchet_external_scripts_no_new_fixed_offset():
    offenders = set()
    for rel, path in _EXTERNAL_SCRIPTS.items():
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="ignore") as fh:
            if _FIXED_TZ.search(fh.read()):
                offenders.add(rel)
    extra = offenders - _EXTERNAL_ALLOWLIST
    assert not extra, (
        "NOWY fixed-offset TZ w żywych scripts/ (skonwertuj na ZoneInfo('Europe/Warsaw')): "
        + ", ".join(sorted(extra))
    )
