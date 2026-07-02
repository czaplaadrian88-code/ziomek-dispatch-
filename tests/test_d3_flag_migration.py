"""D.3 fala A+B — migracja flag env-frozen → flags.json (KANON).

Kontekst: 15 flag route/kanon (plan_recheck.py) i 2 flagi V326 (common.py) były
env-frozen module-consty (`os.environ.get(...,"0/1")`). Były LIVE ON przez drop-iny
systemd, ale poza KANONEM (flags.json/ETAP4/fingerprint/conftest). Migracja: czytane
przez `common.decision_flag` (flags.json → stała-fallback True) + rejestracja w
ETAP4_DECISION_FLAGS. Ten plik dowodzi mechanizmu KANONU i neutralności migracji.

Dowody (per flaga, parametryzowane):
  (i)   brak klucza w flags.json + brak env → decision_flag=True (stała-fallback
        steady-state; utrata klucza NIE flipuje po cichu = anty-COMMIT_DIVERGENCE),
  (ii)  flags.json[flag]=false → decision_flag=False (KANON działa),
  (iii) env=0 przy braku klucza → decision_flag=True (env MARTWY po migracji — zamierzone).
+ stała-fallback istnieje i jest True + zarejestrowana w ETAP4,
+ ON≠OFF na reprezentatywnej gałęzi (plan_recheck fala A: redecide_courier),
+ refresh flags.json→moduł-global (hot-reload w pw) z zachowaniem monkeypatch testów,
+ sprzężenie pary B (#13: GROUPING=ON przy OR_TOOLS=OFF → ostrzeżenie),
+ mutation-check (usunięcie stałej-fallback → (i) pada = fallback jest load-bearing),
+ strażnik strukturalny: żadna z 15 flag fali A nie czyta już env w plan_recheck.
"""
import inspect
import re

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import plan_manager


FALA_A = (
    "ENABLE_GPS_FREE_ANCHOR",
    "ENABLE_GPS_FREE_ANCHOR_LAST_POS",
    "ENABLE_PLAN_REAL_PICKED_UP_AT",
    "ENABLE_PLAN_SEQUENCE_LOCK",
    "ENABLE_PLAN_CANON_ORDER_INVARIANTS",
    "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP",
    "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE",
    "ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP",
    "ENABLE_RECANON_ON_WRITE",
    "ENABLE_CARRIED_FIRST_RELAX",
    "ENABLE_CARRIED_AGE_TZ_FIX",
    "ENABLE_LEX_COMMITTED_WINDOW_SHADOW",
    "ENABLE_LEX_COMMITTED_WINDOW",
    "ENABLE_RELAX_COLOC_PICKUP",
    "ENABLE_NONCARRIED_DROPOFF_REORDER",
)
FALA_B = ("ENABLE_V326_OR_TOOLS_TSP", "ENABLE_V326_SAME_RESTAURANT_GROUPING")
ALL17 = FALA_A + FALA_B


@pytest.fixture
def flags_control(monkeypatch):
    """Kontroluj zawartość flags.json widzianą przez decision_flag (bez pliku)."""
    state = {"flags": {}}
    monkeypatch.setattr(C, "load_flags", lambda: state["flags"])
    return state


# --- (i) brak klucza + brak env → True (stała-fallback steady-state) ----------
@pytest.mark.parametrize("flag", ALL17)
def test_i_default_no_key_no_env_is_true(flag, flags_control, monkeypatch):
    monkeypatch.delenv(flag, raising=False)
    flags_control["flags"] = {}
    assert C.decision_flag(flag) is True


# --- (ii) flags.json=false → False (KANON działa) -----------------------------
@pytest.mark.parametrize("flag", ALL17)
def test_ii_flagsjson_false_is_false(flag, flags_control):
    flags_control["flags"] = {flag: False}
    assert C.decision_flag(flag) is False


# --- (iii) env=0 ignorowany → True (env MARTWY po migracji — zamierzone) -------
@pytest.mark.parametrize("flag", ALL17)
def test_iii_env_zero_ignored_true(flag, flags_control, monkeypatch):
    monkeypatch.setenv(flag, "0")
    flags_control["flags"] = {}
    assert C.decision_flag(flag) is True


# --- stała-fallback istnieje, =True, zarejestrowana w ETAP4 -------------------
@pytest.mark.parametrize("flag", ALL17)
def test_module_const_true_and_registered(flag):
    assert getattr(C, flag) is True, f"{flag}: brak stałej-fallback True (COMMIT_DIVERGENCE)"
    assert flag in C.ETAP4_DECISION_FLAGS, f"{flag}: nie w ETAP4 (poza fingerprint/conftest strip)"


# --- fingerprint obejmuje wszystkie 17 (parytet cross-proces) ----------------
def test_fingerprint_includes_all_17():
    fp = C.flag_fingerprint()
    for flag in ALL17:
        assert f"{flag}=" in fp, f"{flag} nie w flag_fingerprint"


# --- (iv) ON≠OFF na REALNEJ gałęzi fali A: redecide_courier -------------------
# Migrowany odczyt (ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE) rządzi decyzją: OFF =
# no-op (False, _gen nie wołany), ON = generuje kanon (True). Refresh w teście =
# no-op (klucz nieobecny w flags.json → monkeypatch stałej zachowany).
_ORDERS = {
    "o1": {"courier_id": "9", "status": "picked_up"},
    "o2": {"courier_id": "9", "status": "assigned"},
}


def test_iv_fala_a_redecide_gate_on_off(monkeypatch):
    calls = []
    monkeypatch.setattr(PR, "_gen_one_bag_plan", lambda cid, oids, *a, **k: calls.append(cid) or True)
    monkeypatch.setattr(plan_manager, "load_plan", lambda c: None)
    monkeypatch.setattr(PR, "_load_gps_positions", lambda: {})

    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", False)
    assert PR.redecide_courier("9", orders_state=_ORDERS) is False   # gate OFF → no-op
    assert calls == []

    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    assert PR.redecide_courier("9", orders_state=_ORDERS) is True    # gate ON → generuje
    assert calls == ["9"]


# --- refresh: flags.json steruje moduł-globalem (hot-reload w pw) -------------
# ORAZ: brak klucza → refresh NIE rusza globala (monkeypatch testów zachowany).
def test_refresh_flagsjson_drives_module_global(flags_control, monkeypatch):
    # klucz obecny → refresh nadpisuje (hot-reload) w OBIE strony
    for val in (False, True):
        flags_control["flags"] = {"ENABLE_PLAN_SEQUENCE_LOCK": val}
        monkeypatch.setattr(PR, "ENABLE_PLAN_SEQUENCE_LOCK", not val, raising=False)
        PR._refresh_d3_fala_a_flags()
        assert PR.ENABLE_PLAN_SEQUENCE_LOCK is val


def test_refresh_no_key_preserves_monkeypatch(flags_control, monkeypatch):
    # brak klucza w flags.json (jak conftest-strip w testach) → refresh = no-op
    flags_control["flags"] = {}
    monkeypatch.setattr(PR, "ENABLE_CARRIED_FIRST_RELAX", False, raising=False)
    PR._refresh_d3_fala_a_flags()
    assert PR.ENABLE_CARRIED_FIRST_RELAX is False   # monkeypatch NIE skasowany


# --- (iv) Fala B: konsument czyta atrybut modułu common (źródło = migracja) ---
def test_iv_fala_b_consumer_reads_module_attr():
    src = inspect.getsource(__import__("dispatch_v2.route_simulator_v2", fromlist=["x"]))
    # GROUPING: getattr(_C7, "ENABLE_V326_SAME_RESTAURANT_GROUPING", ...) (route_simulator:299)
    assert 'ENABLE_V326_SAME_RESTAURANT_GROUPING' in src
    # OR_TOOLS: from dispatch_v2.common import ENABLE_V326_OR_TOOLS_TSP as _ot_flag (route_simulator:438)
    assert 'ENABLE_V326_OR_TOOLS_TSP' in src
    # atrybut modułu = migracja (literał True); toggling atrybutu = toggling gałęzi
    assert C.ENABLE_V326_SAME_RESTAURANT_GROUPING is True
    assert C.ENABLE_V326_OR_TOOLS_TSP is True


# --- sprzężenie pary B (#13): GROUPING=ON przy OR_TOOLS=OFF → ostrzeżenie ------
def test_pair_b_coherence_return():
    assert C.check_v326_pair_coherence(or_tools=False, grouping=True) is True   # NIESPÓJNE (#13)
    assert C.check_v326_pair_coherence(or_tools=True, grouping=True) is False   # OK (oba ON)
    assert C.check_v326_pair_coherence(or_tools=False, grouping=False) is False  # OK (oba OFF)
    assert C.check_v326_pair_coherence(or_tools=True, grouping=False) is False   # OK


def test_pair_b_warning_emitted(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="dispatch.v326_pair"):
        C.check_v326_pair_coherence(or_tools=False, grouping=True)
    assert any("V326_PAIR_INCOHERENT" in r.message for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="dispatch.v326_pair"):
        C.check_v326_pair_coherence(or_tools=True, grouping=True)
    assert not any("V326_PAIR_INCOHERENT" in r.message for r in caplog.records)


# --- MUTATION-CHECK: stała-fallback jest load-bearing -------------------------
# Usuń stałą-fallback jednej flagi → decision_flag (brak klucza) spada do False
# (globals().get(name, False)) → dowód że True-fallback trzyma zachowanie (i).
def test_mutation_fallback_load_bearing(flags_control, monkeypatch):
    flag = "ENABLE_CARRIED_FIRST_RELAX"
    flags_control["flags"] = {}
    assert C.decision_flag(flag) is True           # pre: fallback trzyma True
    monkeypatch.delattr(C, flag, raising=True)      # mutacja: usuń stałą-fallback
    assert C.decision_flag(flag) is False           # (i) PADA → fallback był load-bearing


# --- strażnik strukturalny: 15 flag fali A NIE czyta już env w plan_recheck ---
def test_fala_a_no_env_read_in_plan_recheck():
    src = inspect.getsource(PR)
    for flag in FALA_A:
        assert f'environ.get("{flag}"' not in src, f"{flag}: nadal czyta env (migracja niepełna)"
        # decision_flag("FLAG") — dopuść zawijanie wiersza (whitespace/newline)
        assert re.search(r'decision_flag\(\s*"' + re.escape(flag) + r'"', src), \
            f"{flag}: brak odczytu decision_flag"
