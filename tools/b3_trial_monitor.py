#!/usr/bin/env python3
"""B3-MONITOR — dzienny auto-monitor trialu flagi ENABLE_NO_GPS_UNCERTAINTY_PENALTY
(LIVE od 2026-06-20 12:39 UTC) z AUTO-ROLLBACKIEM przy breachu.

Cykl:
 1. Zbierz B3-propozycje od daty flipa: skan shadow_decisions.jsonl(+rotacje) po
    reason `no_gps_uncertainty_propose` → {order_id, cid, ts, applied_min}.
 2. Per propozycja OUTCOME:
    - override = finalny przydział ≠ proponowany cid (learning_log:
      PANEL_OVERRIDE actual≠proposed, lub TIMEOUT_SUPERSEDED = nie przyjęto).
      PANEL_AGREE z actual==proposed = przyjęte.
    - on_time = ontime_lib.compute_on_time (delivered − pickup_ready ≤ 35 min).
 3. Agregaty: n, override_rate, on_time_rate.
 4. KRYTERIA ROLLBACKU (KAŻDE bramkowane na SWOIM known-count, nie total n —
    bo nadpisane nie dostarczają, więc ontime_known << n):
      override breach: override_known≥B3_MIN_SAMPLES(15) ∧ override_rate>0.50
      on-time breach:  ontime_known≥B3_MIN_SAMPLES(15)   ∧ on_time_rate<0.60
    BREACH → (AUTO_ROLLBACK) set flaga false atomic + głośny alert; (alert-only)
    tylko alert, flaga nietknięta. Brak breach → SUMMARY. Żadne kryterium nie ma
    ≥15 znanych wyników → „za mało znanych wyników".

ENV:
  B3_MONITOR_AUTO_ROLLBACK (default 1; =0 → alert-only, NIE tyka flagi)
  B3_FLIP_DATE (default 2026-06-20; ISO; początek okna trialu)
  B3_MIN_SAMPLES (default 15)
  B3_OVERRIDE_MAX (default 0.50) / B3_ONTIME_MIN (default 0.60)

Bezpieczeństwo: auto-rollback celuje TYLKO w stan bezpieczny (flaga=false =
powrót do demote V3.16). Zapis flagi atomic (temp+fsync+rename) zachowuje
wszystkie pozostałe klucze. Telegram respektuje guard PYTEST. Fail-soft.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

# ścieżka pakietu (uruchamiany jako moduł lub plik)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

FLAGS_PATH = "/root/.openclaw/workspace/scripts/flags.json"
B3_FLAG = "ENABLE_NO_GPS_UNCERTAINTY_PENALTY"
SHADOW_LOGS = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
]
LEARNING_LOG = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"

B3_PROPOSE_MARKER = "no_gps_uncertainty_propose"


def _parse_ts(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _flip_date():
    raw = os.environ.get("B3_FLIP_DATE", "2026-06-20")
    d = _parse_ts(raw) or _parse_ts(raw + "T00:00:00+00:00")
    return d or datetime(2026, 6, 20, tzinfo=timezone.utc)


# ── 1. zbiór B3-propozycji ────────────────────────────────────────────────── #
def collect_b3_proposals(shadow_paths=None, since=None):
    shadow_paths = shadow_paths or SHADOW_LOGS
    since = since or _flip_date()
    props = {}  # order_id(str) -> {cid, ts, applied_min}
    for p in shadow_paths:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if B3_PROPOSE_MARKER not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if B3_PROPOSE_MARKER not in str(d.get("reason") or ""):
                    continue
                ts = _parse_ts(d.get("ts"))
                if ts is not None and since is not None and ts < since:
                    continue
                oid = str(d.get("order_id"))
                best = d.get("best") or {}
                m = best.get("metrics") if isinstance(best.get("metrics"), dict) else best
                props[oid] = {
                    "cid": str(best.get("courier_id")),
                    "ts": d.get("ts"),
                    "applied_min": (m or {}).get("no_gps_uncertainty_applied_min"),
                }
    return props


# ── 2. outcome: override + on_time ────────────────────────────────────────── #
def _override_index(learning_path=LEARNING_LOG):
    """order_id(str) -> 'override' | 'agree' | None (z learning_log, ostatni wpis)."""
    idx = {}
    if not os.path.exists(learning_path):
        return idx
    with open(learning_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "courier_id" not in line and "action" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            a = d.get("action")
            oid = str(d.get("order_id"))
            if a == "TIMEOUT_SUPERSEDED":
                idx[oid] = "override"  # propozycja nie przyjęta na czas
            elif a in ("PANEL_OVERRIDE", "PANEL_AGREE"):
                pc = d.get("proposed_courier_id")
                ac = d.get("actual_courier_id")
                if pc is None or ac is None:
                    idx[oid] = "override" if a == "PANEL_OVERRIDE" else "agree"
                else:
                    idx[oid] = "agree" if str(pc) == str(ac) else "override"
    return idx


def compute_outcomes(props, learning_path=LEARNING_LOG, ontime_fn=None):
    """Zwraca dict z n / override / on_time agregatami + per-order detale."""
    ov_idx = _override_index(learning_path)
    # on-time przez ontime_lib (lazy import — testy mogą wstrzyknąć ontime_fn)
    if ontime_fn is None:
        try:
            from dispatch_v2.tools import ontime_lib
            dec_idx, deliv_idx = ontime_lib.build_indices()

            def ontime_fn(oid):  # noqa: E306
                r = ontime_lib.compute_on_time(oid, dec_idx, deliv_idx)
                return r.get("on_time")
        except Exception:
            def ontime_fn(oid):  # noqa: E306
                return None

    n = 0
    overrides = 0
    override_known = 0
    on_time = 0
    ontime_known = 0
    details = []
    for oid, info in props.items():
        n += 1
        ov = ov_idx.get(oid)
        if ov is not None:
            override_known += 1
            if ov == "override":
                overrides += 1
        ot = ontime_fn(oid)
        if ot is not None:
            ontime_known += 1
            if ot:
                on_time += 1
        details.append({"oid": oid, "cid": info.get("cid"),
                        "override": ov, "on_time": ot})
    return {
        "n": n,
        "overrides": overrides, "override_known": override_known,
        "override_rate": (overrides / override_known) if override_known else None,
        "on_time": on_time, "ontime_known": ontime_known,
        "on_time_rate": (on_time / ontime_known) if ontime_known else None,
        "details": details,
    }


# ── 4. decyzja breach ─────────────────────────────────────────────────────── #
def evaluate_breach(agg, min_samples=None, override_max=None, ontime_min=None):
    """Czysta (testowalna). Zwraca (is_breach, reason_or_None).

    KAŻDE kryterium bramkowane na SWOIM known-count (nie na total n) — bo
    nadpisane propozycje nie dostarczają, więc ontime_known << n. Inaczej
    przy n=15 ale dostarczonych=4 (3 late) on_time_rate=25% → przedwczesny
    rollback na cienkich danych. Logika: nie rollbackuj kryterium, na które
    nie masz ≥min_samples ZNANYCH wyników. None-rate też nie rollbackuje. n
    jest tylko informacyjne (NIE bramkuje)."""
    min_samples = int(min_samples if min_samples is not None
                      else _env_float("B3_MIN_SAMPLES", 15))
    override_max = override_max if override_max is not None else _env_float("B3_OVERRIDE_MAX", 0.50)
    ontime_min = ontime_min if ontime_min is not None else _env_float("B3_ONTIME_MIN", 0.60)
    reasons = []
    # override breach — tylko gdy ≥min_samples ZNANYCH override-outcome'ów
    ovr = agg.get("override_rate")
    if (ovr is not None and ovr > override_max
            and (agg.get("override_known") or 0) >= min_samples):
        reasons.append(f"override_rate={ovr:.0%}>{override_max:.0%} "
                       f"(known={agg.get('override_known')})")
    # on-time breach — tylko gdy ≥min_samples ZNANYCH dostaw
    otr = agg.get("on_time_rate")
    if (otr is not None and otr < ontime_min
            and (agg.get("ontime_known") or 0) >= min_samples):
        reasons.append(f"on_time_rate={otr:.0%}<{ontime_min:.0%} "
                       f"(known={agg.get('ontime_known')})")
    if reasons:
        return True, " + ".join(reasons)
    return False, None


# ── 5. bezpieczny atomic zapis flagi (TYLKO → False) ──────────────────────── #
def set_flag_false_atomic(flags_path=FLAGS_PATH, flag=B3_FLAG):
    """Atomowo ustawia `flag` na False w flags.json, zachowując WSZYSTKIE inne
    klucze. temp w tym samym katalogu + fsync + os.replace. Zwraca
    (ok, prev_value). Fail-soft → (False, None)."""
    try:
        with open(flags_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        prev = data.get(flag)
        data[flag] = False
        d = os.path.dirname(flags_path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".flags_b3_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tf:
                json.dump(data, tf, ensure_ascii=False, indent=2)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp, flags_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True, prev
    except Exception:
        return False, None


def _send_telegram(text):
    try:
        from dispatch_v2 import telegram_utils
        return telegram_utils.send_admin_alert(text, source="b3_trial_monitor",
                                               priority="high")
    except Exception:
        return False


# ── główna pętla ──────────────────────────────────────────────────────────── #
def run(auto_rollback=None, shadow_paths=None, learning_path=LEARNING_LOG,
        flags_path=FLAGS_PATH, ontime_fn=None, telegram_fn=None,
        flag_setter=None, since=None):
    """Jeden tick monitora. Wszystkie zależności wstrzykiwalne (testy).
    Zwraca dict raportu (n, override_rate, on_time_rate, breach, action)."""
    if auto_rollback is None:
        auto_rollback = os.environ.get("B3_MONITOR_AUTO_ROLLBACK", "1") != "0"
    telegram_fn = telegram_fn or _send_telegram
    flag_setter = flag_setter or (lambda: set_flag_false_atomic(flags_path))

    props = collect_b3_proposals(shadow_paths=shadow_paths, since=since)
    agg = compute_outcomes(props, learning_path=learning_path, ontime_fn=ontime_fn)
    is_breach, reason = evaluate_breach(agg)

    n = agg["n"]
    ovr = agg.get("override_rate")
    otr = agg.get("on_time_rate")
    ovk = agg.get("override_known") or 0
    otk = agg.get("ontime_known") or 0
    ovr_s = f"{ovr:.0%}" if ovr is not None else "—"
    otr_s = f"{otr:.0%}" if otr is not None else "—"
    min_s = int(_env_float("B3_MIN_SAMPLES", 15))
    # czy KTÓREKOLWIEK kryterium ma dość znanych wyników by w ogóle ocenić?
    enough_to_judge = (ovk >= min_s) or (otk >= min_s)
    known_s = f"override_known={ovk}, ontime_known={otk}"

    report = {"n": n, "override_rate": ovr, "on_time_rate": otr,
              "override_known": ovk, "ontime_known": otk,
              "breach": is_breach, "reason": reason, "action": None,
              "auto_rollback": auto_rollback}

    if is_breach:
        if auto_rollback:
            ok, prev = flag_setter()
            report["action"] = "ROLLBACK_DONE" if ok else "ROLLBACK_FAILED"
            telegram_fn(
                f"🔴 B3 AUTO-ROLLBACK: powód={reason}; n={n}, "
                f"override={ovr_s}, on-time={otr_s}. "
                f"Flaga {B3_FLAG}→false ({'OK' if ok else 'ZAPIS NIEUDANY — sprawdź ręcznie!'}). "
                f"Powrót do demote V3.16."
            )
        else:
            report["action"] = "ALERT_ONLY"
            telegram_fn(
                f"🟠 B3 BREACH (alert-only, flaga NIETKNIĘTA): powód={reason}; "
                f"n={n}, override={ovr_s}, on-time={otr_s}. "
                f"AUTO_ROLLBACK wyłączony — rozważ ręczny rollback."
            )
    elif not enough_to_judge:
        report["action"] = "TOO_FEW"
        telegram_fn(
            f"🔵 B3 trial: n={n}, za mało znanych wyników ({known_s}; "
            f"próg {min_s} na kryterium), override={ovr_s}, on-time={otr_s}. "
            f"Monitoruję."
        )
    else:
        report["action"] = "SUMMARY_OK"
        telegram_fn(
            f"🟢 B3 trial: n={n} ({known_s}), override={ovr_s}, on-time={otr_s}. "
            f"Bez breachu — trial trwa."
        )
    return report


def main():
    rep = run()
    print(json.dumps(rep, ensure_ascii=False, default=str))
    return rep


if __name__ == "__main__":
    main()
