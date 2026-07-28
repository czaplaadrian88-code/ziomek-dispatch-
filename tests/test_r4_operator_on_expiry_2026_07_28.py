"""R4 — wygasanie rekordu operatorskiego na granicy doby operacyjnej.

Karta `engine.operator-on-expiry-r4`. Mapa: `docs/R4_OPERATOR_ON_MAP.md`.

Defekt: kontrakt CID-keyed (`availability_by_cid`) przejął od bliźniaczej trójki
`excluded`/`excluded_cids`/`working` semantykę, ale NIE jej cykl życia — bliźniak
kasowany jest codziennie o 06:00 Europe/Warsaw przez
`manual_overrides_daily_reset.py`, a rekord CID-keyed żyje bezterminowo. Skutek na
żywym stanie 27.07: 10 rekordów `OPERATOR_ON` z 24-26.07 nadal wpuszczało kurierów
do puli, w tym CID 284 (`assignment_event`, `updated_at=2026-07-26T16:13:16Z`).
"""
from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import schedule_utils

from dispatch_v2 import common as C
from dispatch_v2 import courier_availability as CA
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import manual_overrides as MO
from dispatch_v2.courier_resolver import CourierState


ROOT = Path(__file__).parents[1]
POS = (53.1325, 23.1688)

# Znaczniki żywego incydentu (UTC) — bez nich test byłby o abstrakcyjnym TTL.
CID284_UPDATED_AT = datetime(2026, 7, 26, 16, 13, 16, tzinfo=timezone.utc)
CID284_PROPOSED_AT = datetime(2026, 7, 27, 19, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _pin_expiry_flag(monkeypatch):
    """Hermetyzacja względem ŻYWEGO `flags.json`.

    `ENABLE_OPERATOR_AVAILABILITY_EXPIRY` należy do `ETAP4_DECISION_FLAGS`, więc
    `tests/conftest.py::_isolate_flags_json` wycina klucz z tmp-kopii i
    `decision_flag()` spada na stałą modułu — tę przypinamy tu na default OFF.
    Każdy test ustawia swoją wartość jawnie (albo przez `expiry_enabled=`).
    """
    monkeypatch.setattr(C, "ENABLE_OPERATOR_AVAILABILITY_EXPIRY", False)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _store(tmp_path, schedule, records=None):
    """Buduje parę plików store'u i zwraca (overrides_path, names_path)."""
    overrides = tmp_path / "manual_overrides.json"
    names = tmp_path / "grafik_full_names.json"
    _write_json(overrides, {"availability_by_cid": records or {}})
    _write_json(names, {"Courier Exact": 400})
    return overrides, names


def _record(state, provenance, updated_at):
    record = {"state": state.value, "provenance": provenance.value}
    if updated_at is not None:
        record["updated_at"] = updated_at.isoformat()
    return record


def _resolve(
    overrides,
    names,
    schedule,
    *,
    now,
    expiry_enabled,
    on_shift=(False, "empty"),
    mins=None,
):
    ctx = CA.load_context(
        schedule,
        overrides_path=str(overrides),
        grafik_names_path=str(names),
        now=now,
        expiry_enabled=expiry_enabled,
    )
    return CA.resolve(
        ctx,
        400,
        is_on_shift=lambda *_: on_shift,
        mins_to_shift_start=lambda _: mins,
        pre_shift_window_min=60,
    )


# ─────────────────────── negatywny oracle: żywy incydent CID 284 ───────────────────────


def test_negative_oracle_cid284_stale_assignment_on_expires_after_day_boundary(
    tmp_path,
):
    """Rekord z 26.07 16:13Z NIE jest prawdą o dobie 27.07 wieczorem.

    Flaga OFF = dzisiejsze (błędne) zachowanie: kurier wpuszczony do puli mimo
    pustego grafiku. Flaga ON = rekord wygasł, decyzja spada na grafik → poza pulą.
    """
    overrides, names = _store(
        tmp_path,
        None,
        records={
            "400": _record(
                CA.AvailabilityState.OPERATOR_ON,
                CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
                CID284_UPDATED_AT,
            )
        },
    )
    schedule = {"Courier Exact": None}  # brak w grafiku, jak CID 284 dnia 27.07

    before = _resolve(
        overrides, names, schedule, now=CID284_PROPOSED_AT, expiry_enabled=False
    )
    after = _resolve(
        overrides, names, schedule, now=CID284_PROPOSED_AT, expiry_enabled=True
    )

    assert before.state is CA.AvailabilityState.OPERATOR_ON
    assert before.dispatchable is True, "OFF musi zachować zachowanie sprzed R4"

    assert after.dispatchable is False
    assert after.state is CA.AvailabilityState.OFF_PLANNED
    assert after.provenance is CA.AvailabilityProvenance.SCHEDULE_EMPTY_DAY
    assert after.operator_expired is True


def test_negative_oracle_end_to_end_stale_record_leaves_dispatchable_pool(
    monkeypatch, tmp_path
):
    """Ten sam incydent przez PEŁNĄ ścieżkę `dispatchable_fleet()`."""
    overrides = tmp_path / "manual_overrides.json"
    names = tmp_path / "grafik_full_names.json"
    _write_json(overrides, {})
    _write_json(names, {"Courier Exact": 400})
    monkeypatch.setattr(C, "ENABLE_CID_AVAILABILITY_CONTRACT", True)
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(MO, "OVERRIDES_PATH", str(overrides))
    monkeypatch.setattr(CR, "GRAFIK_FULL_NAMES_PATH", str(names))
    monkeypatch.setattr(schedule_utils, "load_schedule", lambda: {"Courier Exact": None})
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)

    # Przypisanie sprzed pięciu dób — stale niezależnie od momentu uruchomienia testu.
    CA.set_operator_availability(
        400,
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
        path=str(overrides),
        at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    fleet = {
        "400": CourierState(
            courier_id="400", name="Courier Exact", pos=POS, pos_source="gps"
        )
    }

    monkeypatch.setattr(C, "ENABLE_OPERATOR_AVAILABILITY_EXPIRY", False)
    assert [c.courier_id for c in CR.dispatchable_fleet(dict(fleet))] == ["400"]

    monkeypatch.setattr(C, "ENABLE_OPERATOR_AVAILABILITY_EXPIRY", True)
    assert CR.dispatchable_fleet(dict(fleet)) == []


def test_fresh_record_from_current_operational_day_survives(monkeypatch, tmp_path):
    """Kurier przypisany DZIŚ (jak CID 492 o 20:30Z) zostaje w puli przy fladze ON."""
    overrides = tmp_path / "manual_overrides.json"
    names = tmp_path / "grafik_full_names.json"
    _write_json(overrides, {})
    _write_json(names, {"Courier Exact": 400})
    monkeypatch.setattr(C, "ENABLE_CID_AVAILABILITY_CONTRACT", True)
    monkeypatch.setattr(C, "ENABLE_OPERATOR_AVAILABILITY_EXPIRY", True)
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(MO, "OVERRIDES_PATH", str(overrides))
    monkeypatch.setattr(CR, "GRAFIK_FULL_NAMES_PATH", str(names))
    monkeypatch.setattr(schedule_utils, "load_schedule", lambda: {"Courier Exact": None})
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)

    CA.set_operator_availability(
        400,
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
        path=str(overrides),
    )
    fleet = {
        "400": CourierState(
            courier_id="400", name="Courier Exact", pos=POS, pos_source="gps"
        )
    }
    assert [c.courier_id for c in CR.dispatchable_fleet(fleet)] == ["400"]


# ─────────────────────────────── przypadki brzegowe ───────────────────────────────


def test_courier_on_active_shift_stays_in_pool_despite_expired_record(tmp_path):
    """Wygaśnięcie NIE wyrzuca kuriera, który dziś realnie jest na zmianie.

    Rekord wygasa, ale decyzja spada na grafik — a grafik mówi „na zmianie".
    To jest dokładnie sedno projektu: wygasanie zdejmuje FIKCYJNĄ przesłankę,
    nie realną dostępność.
    """
    overrides, names = _store(
        tmp_path,
        None,
        records={
            "400": _record(
                CA.AvailabilityState.OPERATOR_ON,
                CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
                CID284_UPDATED_AT,
            )
        },
    )
    decision = _resolve(
        overrides,
        names,
        {"Courier Exact": {"start": "00:00", "end": "23:59"}},
        now=CID284_PROPOSED_AT,
        expiry_enabled=True,
        on_shift=(True, "on_shift"),
    )
    assert decision.dispatchable is True
    assert decision.state is CA.AvailabilityState.SCHEDULED_ON
    assert decision.operator_expired is True


@pytest.mark.parametrize(
    "updated_at, expected_expired",
    [
        # 27.07 05:59 warszawskiego (=03:59Z) — jeszcze POPRZEDNIA doba operacyjna.
        (datetime(2026, 7, 27, 3, 59, tzinfo=timezone.utc), True),
        # 27.07 06:01 warszawskiego (=04:01Z) — już BIEŻĄCA doba, żyje do 28.07 06:00.
        # Dwa znaczniki odległe o 2 minuty lądują po przeciwnych stronach granicy.
        (datetime(2026, 7, 27, 4, 1, tzinfo=timezone.utc), False),
        # 26.07 06:01 warszawskiego — doba wcześniej, więc wygasły.
        (datetime(2026, 7, 26, 4, 1, tzinfo=timezone.utc), True),
    ],
)
def test_day_boundary_is_0600_warsaw_not_midnight(
    tmp_path, updated_at, expected_expired
):
    """Granicą jest 06:00 Europe/Warsaw (bliźniaczy timer), NIE północ UTC.

    Doba operacyjna biegnie 06:00→06:00, więc rekord z 05:59 należy do doby
    POPRZEDNIEJ — o 14:00 tego samego dnia jest już nieaktualny.
    """
    overrides, names = _store(
        tmp_path,
        None,
        records={
            "400": _record(
                CA.AvailabilityState.OPERATOR_ON,
                CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
                updated_at,
            )
        },
    )
    decision = _resolve(
        overrides,
        names,
        {"Courier Exact": None},
        now=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        expiry_enabled=True,
    )
    assert decision.operator_expired is expected_expired
    assert decision.dispatchable is not expected_expired


def test_boundary_is_exact_and_inclusive():
    """Rekord żyje DO granicy włącznie i wygasa dokładnie w jej momencie."""
    stamped = datetime(2026, 7, 26, 16, 13, 16, tzinfo=timezone.utc)
    boundary = CA._operational_day_start_after(stamped)
    assert boundary == datetime(2026, 7, 27, 4, 0, tzinfo=timezone.utc)

    record = _record(
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
        stamped,
    )
    assert CA._operator_record_expired(record, boundary - timedelta(seconds=1)) is False
    assert CA._operator_record_expired(record, boundary) is True


def test_winter_boundary_follows_local_wall_clock_through_dst():
    """Zimą 06:00 Warszawy to 05:00Z — granica jedzie za czasem ściennym, nie offsetem."""
    assert CA._operational_day_start_after(
        datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 1, 16, 5, 0, tzinfo=timezone.utc)
    assert CA._operational_day_start_after(
        datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 7, 16, 4, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("updated_at", [None, "", "nie-iso-8601"])
def test_unprovable_freshness_expires_on_but_keeps_off(tmp_path, updated_at):
    """Brak dowodu świeżości nigdy NIE nadaje dostępności — i nigdy jej nie zdejmuje.

    `OPERATOR_ON` bez stempla wygasa (wpuszczenie do puli wymaga przesłanki),
    `OPERATOR_OFF` zostaje (zdjęcie STOP-u wymaga DOWODU, że granica minęła).
    """
    now = datetime(2026, 7, 27, 19, 30, tzinfo=timezone.utc)
    on_record = {
        "state": CA.AvailabilityState.OPERATOR_ON.value,
        "provenance": CA.AvailabilityProvenance.ASSIGNMENT_EVENT.value,
    }
    off_record = {
        "state": CA.AvailabilityState.OPERATOR_OFF.value,
        "provenance": CA.AvailabilityProvenance.COORDINATOR_CONSOLE.value,
    }
    if updated_at is not None:
        on_record["updated_at"] = updated_at
        off_record["updated_at"] = updated_at

    assert CA._operator_record_expired(on_record, now) is True
    assert CA._operator_record_expired(off_record, now) is False

    overrides, names = _store(tmp_path, None, records={"400": off_record})
    decision = _resolve(
        overrides,
        names,
        {"Courier Exact": {"start": "00:00", "end": "23:59"}},
        now=now,
        expiry_enabled=True,
        on_shift=(True, "on_shift"),
    )
    assert decision.state is CA.AvailabilityState.OPERATOR_OFF
    assert decision.dispatchable is False


def test_fresh_coordinator_stop_still_blocks_a_courier_on_shift(tmp_path):
    """Dzisiejszy STOP koordynatora trzyma — wygasanie go nie osłabia."""
    overrides, names = _store(
        tmp_path,
        None,
        records={
            "400": _record(
                CA.AvailabilityState.OPERATOR_OFF,
                CA.AvailabilityProvenance.COORDINATOR_CONSOLE,
                datetime(2026, 7, 27, 19, 55, 46, tzinfo=timezone.utc),
            )
        },
    )
    decision = _resolve(
        overrides,
        names,
        {"Courier Exact": {"start": "00:00", "end": "23:59"}},
        now=CID284_PROPOSED_AT,
        expiry_enabled=True,
        on_shift=(True, "on_shift"),
    )
    assert decision.state is CA.AvailabilityState.OPERATOR_OFF
    assert decision.dispatchable is False
    assert decision.operator_expired is False


def test_stale_coordinator_stop_expires_like_the_twin_excluded_list(tmp_path):
    """Wczorajszy STOP wygasa — bo bliźniacze `excluded` też jest kasowane o 06:00.

    „Wykluczony do końca dnia" i „pracuje do końca dnia" mają ten sam horyzont;
    asymetria oznaczałaby dwa cykle życia dla jednej klasy stanu.
    """
    overrides, names = _store(
        tmp_path,
        None,
        records={
            "400": _record(
                CA.AvailabilityState.OPERATOR_OFF,
                CA.AvailabilityProvenance.COORDINATOR_CONSOLE,
                datetime(2026, 7, 25, 19, 55, tzinfo=timezone.utc),
            )
        },
    )
    decision = _resolve(
        overrides,
        names,
        {"Courier Exact": {"start": "00:00", "end": "23:59"}},
        now=CID284_PROPOSED_AT,
        expiry_enabled=True,
        on_shift=(True, "on_shift"),
    )
    assert decision.state is CA.AvailabilityState.SCHEDULED_ON
    assert decision.dispatchable is True
    assert decision.operator_expired is True


def test_flag_off_leaves_every_decision_byte_for_byte(tmp_path):
    """OFF nie może różnić się od stanu sprzed R4 — poza polem obserwowalności."""
    now = datetime(2026, 7, 27, 19, 30, tzinfo=timezone.utc)
    for state, provenance in (
        (CA.AvailabilityState.OPERATOR_ON, CA.AvailabilityProvenance.ASSIGNMENT_EVENT),
        (
            CA.AvailabilityState.OPERATOR_OFF,
            CA.AvailabilityProvenance.COORDINATOR_CONSOLE,
        ),
    ):
        overrides, names = _store(
            tmp_path,
            None,
            records={"400": _record(state, provenance, CID284_UPDATED_AT)},
        )
        decision = _resolve(
            overrides, names, {"Courier Exact": None}, now=now, expiry_enabled=False
        )
        assert decision.state is state
        assert decision.provenance is provenance
        assert decision.dispatchable is (state is CA.AvailabilityState.OPERATOR_ON)
        assert decision.operator_expired is False


# ──────────────────────────────── mutation probes ────────────────────────────────


def test_mutation_neutralizing_the_expiry_predicate_reproduces_the_defect(
    monkeypatch, tmp_path
):
    """Usunięcie predykatu wygasania → incydent CID 284 wraca (test znów czerwony)."""
    overrides, names = _store(
        tmp_path,
        None,
        records={
            "400": _record(
                CA.AvailabilityState.OPERATOR_ON,
                CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
                CID284_UPDATED_AT,
            )
        },
    )
    args = dict(now=CID284_PROPOSED_AT, expiry_enabled=True)
    assert (
        _resolve(overrides, names, {"Courier Exact": None}, **args).dispatchable is False
    )

    monkeypatch.setattr(CA, "_operator_record_expired", lambda record, now: False)
    mutant = _resolve(overrides, names, {"Courier Exact": None}, **args)
    assert mutant.dispatchable is True, "mutant MUSI odtworzyć defekt"
    assert mutant.state is CA.AvailabilityState.OPERATOR_ON


def test_mutation_pushing_the_boundary_out_reproduces_the_defect(
    monkeypatch, tmp_path
):
    """Rozciągnięcie granicy doby (np. na tydzień) też przywraca defekt."""
    overrides, names = _store(
        tmp_path,
        None,
        records={
            "400": _record(
                CA.AvailabilityState.OPERATOR_ON,
                CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
                CID284_UPDATED_AT,
            )
        },
    )
    monkeypatch.setattr(
        CA,
        "_operational_day_start_after",
        lambda moment: moment + timedelta(days=7),
    )
    mutant = _resolve(
        overrides,
        names,
        {"Courier Exact": None},
        now=CID284_PROPOSED_AT,
        expiry_enabled=True,
    )
    assert mutant.dispatchable is True, "mutant MUSI odtworzyć defekt"


def test_mutation_freezing_now_per_courier_would_break_loop_consistency(tmp_path):
    """`now` jest zamrożone w kontekście — dwa `resolve()` z tego samego kontekstu
    rozstrzygają na TYM SAMYM znaczniku, niezależnie od zegara ściennego."""
    overrides, names = _store(
        tmp_path,
        None,
        records={
            "400": _record(
                CA.AvailabilityState.OPERATOR_ON,
                CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
                CID284_UPDATED_AT,
            )
        },
    )
    ctx = CA.load_context(
        {"Courier Exact": None},
        overrides_path=str(overrides),
        grafik_names_path=str(names),
        now=CID284_PROPOSED_AT,
        expiry_enabled=True,
    )
    assert ctx.now == CID284_PROPOSED_AT
    kwargs = dict(
        is_on_shift=lambda *_: (False, "empty"),
        mins_to_shift_start=lambda _: None,
        pre_shift_window_min=60,
    )
    first = CA.resolve(ctx, 400, **kwargs)
    second = CA.resolve(replace(ctx), 400, **kwargs)
    assert first.dispatchable == second.dispatchable is False


# ─────────────────────────────────── ratchety ───────────────────────────────────


def test_ratchet_expiry_policy_has_exactly_one_owner():
    """Polityka wygasania mieszka WYŁĄCZNIE w kanonicznym module dostępności."""
    production = [p for p in ROOT.rglob("*.py") if "tests" not in p.parts]
    owners = sorted(
        p.relative_to(ROOT).as_posix()
        for p in production
        if "OPERATIONAL_DAY_RESET_HOUR" in p.read_text(encoding="utf-8")
    )
    assert owners == ["courier_availability.py"]

    predicate_users = sorted(
        p.relative_to(ROOT).as_posix()
        for p in production
        if "_operator_record_expired" in p.read_text(encoding="utf-8")
    )
    assert predicate_users == ["courier_availability.py"]


def test_ratchet_expiry_is_evaluated_before_the_operator_short_circuit():
    """Wygasanie MUSI stać przed gałęzią honorującą rekord — inaczej jest martwe."""
    source = (ROOT / "courier_availability.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "resolve"
    )
    expiry_lines = [
        node.lineno
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_operator_record_expired"
    ]
    # Miejsce, w którym rekord jest HONOROWANY: odczyt `operator["state"]`.
    honour_lines = [
        node.lineno
        for node in ast.walk(func)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "operator"
    ]
    assert expiry_lines, "predykat wygasania wypadł z resolve()"
    assert honour_lines, "nie znaleziono gałęzi honorującej rekord"
    assert max(expiry_lines) < min(honour_lines), (
        "wygasanie musi być rozstrzygnięte ZANIM rekord zostanie użyty — "
        "inaczej predykat jest martwym kodem"
    )


def test_ratchet_boundary_matches_the_twin_daily_reset_timer():
    """Stała granicy i `OnCalendar` bliźniaczego timera nie mogą się rozjechać.

    Jednostka systemd żyje poza repo, więc guard jest warunkowy — gdy plik jest
    dostępny (host produkcyjny), rozjazd konfiguracji zapala się natychmiast.
    """
    assert CA.OPERATIONAL_DAY_RESET_HOUR == 6
    assert str(CA.OPERATIONAL_DAY_TZ) == "Europe/Warsaw"
    unit = Path("/etc/systemd/system/dispatch-overrides-reset.timer")
    if unit.exists():
        assert "06:00:00 Europe/Warsaw" in unit.read_text(encoding="utf-8")


def test_flag_is_etap4_and_defaults_to_off():
    assert "ENABLE_OPERATOR_AVAILABILITY_EXPIRY" in C.ETAP4_DECISION_FLAGS
    registry = json.loads(
        (ROOT / "tools" / "flag_lifecycle_registry.json").read_text(encoding="utf-8")
    )
    entry = registry["flags"]["ENABLE_OPERATOR_AVAILABILITY_EXPIRY"]
    assert entry["default"] is False
    assert entry["current_snapshot"]["flags.json"] in (False, None)
