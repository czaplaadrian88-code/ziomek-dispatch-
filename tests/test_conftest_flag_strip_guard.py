"""A1-SERIALIZER cz.2 (Sprint 1, 2026-07-05) — INV-FLAG-CONFTEST-STRIP (kontrakt 4).

Inwariant: „test z OFF nie biegnie cicho ON". Mechanizm: conftest
`_stripped_flags_copy()` daje subprocess script-runnerom kopię żywego
flags.json BEZ flag decyzyjnych (wracają do env-defaultów). Oracle Fazy 1
(30.06, C19-conftest-leak) pokazał, że claim naprawy `257d315` był VOID:
strip pokrywa TYLKO `ETAP4_DECISION_FLAGS` + `FLAGS_JSON_NUMERIC_OVERRIDES`
+ `TEST_ISOLATED_INFRA_FLAGS` — flagi w flags.json POZA tymi listami
przeciekają do testów z żywą wartością (silent-ON survivors).

Ten strażnik domyka klasę dwustronnie:
1. STRIP DZIAŁA: kopia faktycznie nie zawiera żadnego klucza z 3 list,
   a klucze niedecyzyjne przechodzą bajt-w-bajt (strip = usuwanie, nie
   mutacja — subprocess czytający inne configi z flags.json nie dostaje
   skorumpowanego pliku).
2. KLASA NIE ROŚNIE (ratchet): każdy klucz żywego flags.json spoza 3 list
   MUSI być w zamrożonym baseline poniżej. NOWA flaga dodana do flags.json
   bez członkostwa w ETAP4_DECISION_FLAGS = ten test PADA. Właściwa naprawa
   = dopisać flagę do ETAP4_DECISION_FLAGS w common.py (strip + fingerprint
   + rejestr), NIGDY dopisanie do baseline bez świadomej decyzji (baseline
   wolno tylko ZMNIEJSZAĆ przy migracji flag do rejestru).

Pełne zamknięcie klasy (baseline -> 0) = praca INV-FLAG-REGISTRY (dziś
osobne 🔴: ~112 flag poza rejestrem). Ratchet zamienia cichy przeciek
w głośny fail — to jest de-VOID, nie deklaracja „naprawione".
"""
import json
import os
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.tests import conftest as CT  # noqa: E402

_LIVE_FLAGS = os.path.join(CT._SCRIPTS_ROOT, "flags.json")


def _covered() -> set:
    return (set(getattr(C, "ETAP4_DECISION_FLAGS", ()))
            | set(getattr(C, "FLAGS_JSON_NUMERIC_OVERRIDES", ()))
            | set(getattr(C, "TEST_ISOLATED_INFRA_FLAGS", ())))


# Zamrożony stan długu 2026-07-05 (134 klucze; _comment_* pominięte — to
# dokumentacja w pliku, nie flagi). Kierunek dozwolony: TYLKO w dół.
_KNOWN_SURVIVORS_2026_07_05 = {
    "A4_TEST_FLAG",
    "ALWAYS_PROPOSE_WOULD_REDIRECT_SHADOW",
    "AUTO_KOORD_ON_NEW_ORDER_ENABLED",
    "AUTO_KOORD_TELEGRAM_INFO_ENABLED",
    "AUTO_PROXIMITY_ENABLED",
    "AUTO_PROXIMITY_SHADOW_ONLY",
    "AUTO_PROXIMITY_THRESHOLD",
    "AUTO_ROUTE_WEAK_PICK_SCORE_FLOOR",
    "BARTEK_USER_ID",
    "BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN",
    "BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN",
    "COORDINATOR_DM_ROUTING_ENABLED",
    "CZASOWKA_MIN_PROPOSAL_SCORE",
    "CZASOWKA_PROACTIVE_ENABLED",
    "CZASOWKA_PROACTIVE_MAX_WAIT_MIN",
    "CZASOWKA_PROACTIVE_MIN_MARGIN",
    "CZASOWKA_PROACTIVE_MIN_SCORE",
    "CZASOWKA_PROACTIVE_SCORE_SHADOW",
    "CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES",
    "CZASOWKA_T0_ALERT_ENABLED",
    "CZASOWKA_T40_ENABLED",
    "CZASOWKA_T50_ENABLED",
    "CZASOWKA_T60_ENABLED",
    "CZASOWKA_TRIGGERS_MIN",
    "CZASOWKA_TRIGGER_TOLERANCE_MIN",
    "ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW",
    "ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW",
    "ENABLE_AUTO_ROUTE_WEAK_PICK_ALERT",
    "ENABLE_BAG_TIME_ALERTS",
    "ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW",
    "ENABLE_BEST_EFFORT_OBJM_SHADOW",
    "ENABLE_BUG4_RESEQ_SHADOW",
    "ENABLE_CARRY_CHAIN_PENALTY",
    "ENABLE_COORDINATOR_FORCE_TIME_RECHECK",
    "ENABLE_CZASOWKA_CK_PASSIVE_GUARD",
    "ENABLE_DATA_ALERTS",
    "ENABLE_DRIVE_MIN_CALIBRATION_V2",
    "ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW",
    "ENABLE_DRIVE_SPEED_TIER_CORRECTION",
    "ENABLE_EARLYBIRD_T30_SHADOW",
    "ENABLE_ELASTYK_CK_NO_BACKWARD",
    "ENABLE_ETA_QUANTILE_SHADOW",
    "ENABLE_ETA_R3_DROP_SHADOW",
    "ENABLE_ETA_R3_SHADOW",
    "ENABLE_F7_HIGH_RISK_BUCKET",
    "ENABLE_FAIL03_K2_SHADOW",
    "ENABLE_FEAS_CARRY_BLIND_SHADOW",
    "ENABLE_FIRMOWE_KONTO_KOORD_ALERTS",
    "ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS",
    "ENABLE_FLAG_FINGERPRINT_GUARD_ALERT",
    "ENABLE_GEOCODE_NOMINATIM_FALLBACK",
    "ENABLE_GEOCODE_VERIFICATION_ENFORCE",
    "ENABLE_GLOBAL_ALLOC_WRITE",
    "ENABLE_GPS_DELIVERY_VALIDATION",
    "ENABLE_GRAFIK_ENTRY_SALVAGE",
    "ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE",
    "ENABLE_KEBAB_KROL_DINNER_EXCLUSION",
    "ENABLE_LGBM_TWOMODEL_SHADOW",
    "ENABLE_MIN_DELIVERED_AT_SHADOW",
    "ENABLE_NOTIFY_PRIORITY_ROUTING",
    "ENABLE_OBJM_LEXR6_SELECT_SHADOW",
    "ENABLE_ORDERS_STATE_PRUNE",
    "ENABLE_PANEL_PACKS_EMPTY_WRITE_GUARD",
    "ENABLE_PARCEL_LANE_LIVE",
    "ENABLE_PENDING_PROPOSALS_WRITE",
    "ENABLE_PENDING_RESWEEP",
    "ENABLE_PERF_SLO_ALERT",
    "ENABLE_PICKUP_DEBIAS_SHADOW",
    "ENABLE_PICKUP_FROM_GROUND_TRUTH",
    "ENABLE_PICKUP_TIME_MIRRORS_CK",
    "ENABLE_PLN_OBJECTIVE_SHADOW",
    "ENABLE_PREP_BIAS_SHADOW",
    "ENABLE_PREP_VARIANCE_ANOMALY_SHADOW",
    "ENABLE_PROPOSAL_ETA_FLOOR_TO_COMMITTED",
    "ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN",
    "ENABLE_R1_CORRIDOR_GRADIENT",
    "ENABLE_READY_AT_INSTRUMENTATION",
    "ENABLE_REASSIGNMENT_FORWARD_SHADOW",
    "ENABLE_REASSIGN_GLOBAL_SELECT",
    "ENABLE_REGEOCODE_SYNC_TEXT",
    "ENABLE_REPO_COST_SHADOW",
    "ENABLE_R_DECLARED_TRIPWIRE",
    "ENABLE_SAME_RESTAURANT_RACE_PROBE",
    "ENABLE_SPLIT_LAYER_GUARD",
    "ENABLE_STATE_PANEL_DIVERGENCE_ALERT",
    "ENABLE_STATE_WRITE_GUARD",
    "ENABLE_UWAGI_ADDRESS_PARSER",
    "ENABLE_WAITING_AT_PERSIST",
    "FAZA7_AGREEMENT_BUTTONS_ENABLED",
    "GPS_FEED_ALERT_ENABLED",
    "GPS_FEED_ALERT_SHADOW_ONLY",
    "GPS_FEED_MIN_FRESH_RATIO",
    "GPS_FEED_SUSTAIN_CYCLES",
    "KONIEC_AUTHORIZED_USER_IDS",
    "MANUAL_KONIEC_COMMAND_ENABLED",
    "MANUAL_POPRAWA_COMMAND_ENABLED",
    "NEW_COURIER_AUTOPAIR_AUTOWRITE",
    "NEW_COURIER_AUTOPAIR_ENABLED",
    "OBSERVABILITY_FLEET_FILTER_LOGGING",
    "OBSERVABILITY_PER_CANDIDATE_ENABLED",
    "ORDERS_STATE_PRUNE_DRY_RUN",
    "ORDERS_STATE_PRUNE_RETENTION_HOURS",
    "PANEL_PACKS_EMPTY_GUARD_MAX_PREV_AGE_S",
    "PARSER_DEGRADED",
    "PARSE_BLACKOUT_MIN_PREV",
    "PARSE_CONTINUITY_GUARD_ENABLED",
    "PARSE_DROP_PCT",
    "PARSE_GUARD_CONFIRM_CYCLES",
    "PENDING_RESWEEP_LIVE",
    "PENDING_RESWEEP_MARGIN",
    "PROPOSAL_FORMAT_V2",
    "REASSIGN_FWD_MARGIN",
    "REASSIGN_FWD_MAX_ORDERS",
    "REASSIGN_FWD_NOTIFY_COOLDOWN_MIN",
    "REASSIGN_FWD_NOTIFY_TRUSTED_ONLY",
    "REASSIGN_FWD_TELEGRAM_LIVE",
    "RECONCILIATION_AUTO_AGE_THRESHOLD_HOURS",
    "RECONCILIATION_AUTO_RESYNC_ENABLED",
    "RECONCILIATION_ENABLED",
    "RECONCILIATION_HARD_CAP_PER_RUN",
    "RECONCILIATION_HEALTH_SELF_HEAL",
    "RECONCILIATION_INTERVAL_MIN",
    "RECONCILIATION_LOOKBACK_DAYS",
    "RECONCILIATION_REVALIDATE_TRANSIENT",
    "RECONCILIATION_TELEGRAM_ALERT_ENABLED",
    "SHIFT_BATCH_MIN_COURIERS",
    "SHIFT_BATCH_WINDOW_MIN",
    "SHIFT_NOTIFY_ENABLED",
    "SHIFT_NOTIFY_T30_REMINDER_ENABLED",
    "SHIFT_NOTIFY_T60_END_ENABLED",
    "SHIFT_NOTIFY_T60_START_ENABLED",
    "SHIFT_NOTIFY_TARGET_CHAT_ID",
    "commitment_level",
    "kill_switch_to_v1",
}


def test_stripped_copy_removes_all_covered_flags():
    """Kopia strip nie zawiera ŻADNEGO klucza z 3 list pokrycia."""
    p = CT._stripped_flags_copy()
    assert p, "_stripped_flags_copy() zwrócił pustkę (fail-open) — strip martwy"
    stripped = json.load(open(p))
    leaked = sorted(set(stripped) & _covered())
    assert not leaked, f"strip przepuścił flagi decyzyjne: {leaked[:10]}"


def test_stripped_copy_preserves_noncovered_byte_identical():
    """Strip = usuwanie kluczy, zero mutacji reszty (subprocess czyta
    niedecyzyjne configi z flags.json — muszą być identyczne z żywym)."""
    p = CT._stripped_flags_copy()
    assert p
    stripped = json.load(open(p))
    live = json.load(open(_LIVE_FLAGS))
    covered = _covered()
    for k, v in live.items():
        if k in covered:
            continue
        assert k in stripped and stripped[k] == v, (
            f"strip zmutował/zgubił niedecyzyjny klucz {k}: "
            f"live={v!r} stripped={stripped.get(k)!r}")
    extra = sorted(set(stripped) - set(live))
    assert not extra, f"strip DODAŁ klucze spoza żywego pliku: {extra}"


def test_no_new_unstripped_flags_ratchet():
    """RATCHET: flags.json nie może urosnąć o flagę spoza pokrycia strip.

    FAIL tutaj = dodałeś(-aś) flagę do flags.json bez ETAP4_DECISION_FLAGS
    -> testy z założeniem OFF pobiegną cicho ON (subprocess-runnery), a
    flag_fingerprint jej nie widzi. Fix: dopisz flagę do
    ETAP4_DECISION_FLAGS (common.py), NIE do baseline tego testu.
    """
    live = json.load(open(_LIVE_FLAGS))
    survivors = {k for k in live
                 if k not in _covered() and not k.startswith("_comment")}
    new_leaks = sorted(survivors - _KNOWN_SURVIVORS_2026_07_05)
    assert not new_leaks, (
        "NOWE flagi w flags.json poza pokryciem conftest-strip (dopisz do "
        f"ETAP4_DECISION_FLAGS, nie do baseline): {new_leaks}")
    healed = sorted(_KNOWN_SURVIVORS_2026_07_05 - survivors)
    assert not healed, (
        "Flagi zniknęły z długu (przeniesione do rejestru / usunięte) — "
        f"zaktualizuj baseline W DÓŁ o: {healed}")
