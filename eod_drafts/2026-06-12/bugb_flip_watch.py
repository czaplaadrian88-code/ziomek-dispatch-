#!/usr/bin/env python3
"""BUG-B flip watch (2026-06-12) — pierwszy odczyt po flipie 4.0/km z 11.06 ~20:46 UTC.

Czyta shadow_decisions.jsonl od momentu flipu i raportuje watch-metryki
z werdyktu (eod_drafts/2026-06-11/VERDICT_bug_a_b.md §7) na Telegram:
  - share best z niezerowym bonus_r5_pickup_detour_penalty (oczekiwane ~43%),
  - KOORD share vs baseline 14.4% (alarm > +2 p.p.),
  - mediana detour best (baseline 2.01 km — cel: spadek),
  - markery r5_detour_extreme,
  - sanity: BUG-A wciąż OFF (bonus_bag_time_* aplikowane = 0, *_shadow liczone).

Uruchamiany jednorazowo przez `at` (15:00 Warsaw po lunch peaku). Read-only.
"""
import json
import statistics
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.tools._rotated_logs import iter_jsonl_records  # noqa: E402

SHADOW_LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
FLIP_TS = "2026-06-11T20:46"
BASELINE_KOORD = 14.4
BASELINE_DETOUR_MED = 2.01


def main():
    n = 0
    propose = 0
    koord = 0
    nonzero_b = 0
    shadow_field_seen = 0
    extreme = 0
    detours = []
    a_applied_nonzero = 0
    a_shadow_nonzero = 0
    for d in iter_jsonl_records(SHADOW_LOG):
        ts = d.get("ts", "")
        if ts < FLIP_TS:
            continue
        n += 1
        v = d.get("verdict")
        if v == "KOORD":
            koord += 1
        b = d.get("best") or {}
        if v != "KOORD":
            propose += 1
            if (b.get("bonus_r5_pickup_detour_penalty") or 0) != 0:
                nonzero_b += 1
            if b.get("bonus_r5_pickup_detour_penalty_shadow") is not None:
                shadow_field_seen += 1
            dk = b.get("r5_pickup_detour_total_km")
            if isinstance(dk, (int, float)):
                detours.append(float(dk))
            if b.get("r5_detour_extreme"):
                extreme += 1
            if (b.get("bonus_bag_time_max") or 0) != 0:
                a_applied_nonzero += 1
            if (b.get("bonus_bag_time_max_shadow") or 0) != 0:
                a_shadow_nonzero += 1

    if n == 0:
        msg = ("🟡 BUG-B flip watch (12.06): ZERO decyzji od flipu (20:46 UTC "
               "11.06) — sprawdź czy dispatch-shadow przetwarza zlecenia.")
    else:
        koord_pct = 100.0 * koord / n
        nz_pct = (100.0 * nonzero_b / propose) if propose else 0.0
        sf_pct = (100.0 * shadow_field_seen / propose) if propose else 0.0
        med = round(statistics.median(detours), 2) if detours else None
        alarm = []
        if propose and sf_pct < 50:
            alarm.append("⛔ pola *_shadow < 50% PROPOSE — nowy kod NIE liczy?")
        if propose >= 20 and nz_pct < 10:
            alarm.append("⛔ bonus_r5 niezerowy <10% (oczekiwane ~43%) — flaga nie działa?")
        if koord_pct > BASELINE_KOORD + 2.0 and n >= 50:
            alarm.append(f"⚠ KOORD {koord_pct:.1f}% > baseline {BASELINE_KOORD}+2pp — "
                         "obserwuj; 2 dni z rzędu = rollback (ALWAYS-PROPOSE)")
        if a_applied_nonzero:
            alarm.append(f"⛔ BUG-A APLIKOWANY ({a_applied_nonzero}×) mimo flagi OFF!")
        status = "🔴" if any(a.startswith("⛔") for a in alarm) else ("🟠" if alarm else "🟢")
        msg = (
            f"{status} BUG-B flip watch (4.0/km od 11.06 20:46 UTC) — {n} decyzji "
            f"({propose} PROPOSE / {koord} KOORD={koord_pct:.1f}%, baseline 14.4%)\n"
            f"• bonus_r5 niezerowy: {nonzero_b}/{propose} = {nz_pct:.1f}% (oczek. ~43%)\n"
            f"• pola shadow obecne: {sf_pct:.0f}% PROPOSE\n"
            f"• mediana detour best: {med} km (baseline 2.01 — cel: spadek)\n"
            f"• r5_detour_extreme: {extreme}× (>7.5 km ∧ bag≥2)\n"
            f"• BUG-A: aplikowany 0 (OK, flaga OFF), shadow liczony {a_shadow_nonzero}×\n"
            + ("\n".join(alarm) + "\n" if alarm else "")
            + "Eskalacja do 8.0/km + flip BUG-A (max+FIFO, SUM=0): po ≥7 dniach "
              "czystych metryk (~18-19.06, ACK Adriana)."
        )

    print(msg)
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg)
        print("telegram:", "OK" if ok else "FAIL")
    except Exception as e:
        print(f"telegram fail: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
