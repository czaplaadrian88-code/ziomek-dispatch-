#!/usr/bin/env python3
"""Monitor WYZWALANY reklasyfikacji tierów 207 (std->slow) + 289 (std->std+).

Kontekst (2026-06-10, [[lessons]] #179): 207/289 nie wchodzą w pętlę propozycji
Ziomka (self-assign z apki, 0 wystąpień w learning_log), pracują sporadycznie.
Dlatego zamiast jednorazowego strzału w peak — monitor wyzwalany: odzywa się TYLKO
gdy realnie dowożą, akumuluje realne czasy dostaw post-reklas i przy n>=PROG wydaje
WERDYKT (czy przypisany tier pasuje do rzeczywistości), po czym milknie.

Źródło: backfill_decisions_outcomes_v1.jsonl (regen codziennie 04:00 UTC, łapie też
self-assign — atrybucja outcome.courier_id_final; deduplikowane). Stan: mały ledger
tier_reclass_watch_state.json (last_reported_n + verdict_sent per cid).

Uruchamiane crontab 06:00 UTC (po regenie backfillu). Telegram tylko gdy NOWE dostawy
lub werdykt — inaczej cisza (zero spamu w dni niepracujące). READ-ONLY na danych Ziomka.

Ręcznie / test (bez wysyłki): TIER_MONITOR_NO_SEND=1 python .../tier_reclass_trigger_watch.py
"""
import json
import os
import statistics
import sys
import tempfile

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
BACKFILL = os.path.join(STATE_DIR, "backfill_decisions_outcomes_v1.jsonl")
LEDGER = os.path.join(STATE_DIR, "tier_reclass_watch_state.json")
RECLASS_DATE = "2026-06-10"            # liczymy dostawy delivered_ts >= tej daty
VERDICT_N = 10                          # min dostaw post-reklas do werdyktu (stabilna mediana)

# cid -> (imię, nowy tier, dolny-prog-OK, górny-prog-OK) — zakres realnej mediany potwierdzający tier
TARGET = {
    "207": ("Marek", "slow", 19.0, None),    # slow: real >= 19 min potwierdza; < 17 = za surowo
    "289": ("Grzegorz W", "std+", None, 16.0),  # std+: real <= 16 min potwierdza; > 19 = za hojnie
}


def _med(a):
    return round(statistics.median(a), 1) if a else None


def _p90(a):
    if not a:
        return None
    s = sorted(a)
    return round(s[int(round(0.9 * (len(s) - 1)))], 1)


def _load_ledger():
    try:
        with open(LEDGER) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_ledger(d):
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, LEDGER)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _deliveries_post_reclass():
    """Z backfillu: per cid lista pickup_to_delivery_min dla delivered_ts >= RECLASS_DATE."""
    out = {cid: [] for cid in TARGET}
    try:
        with open(BACKFILL, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                o = r.get("outcome") or {}
                if o.get("status") != "delivered":
                    continue
                cid = str(o.get("courier_id_final") or "")
                if cid not in TARGET:
                    continue
                dts = str(o.get("delivered_ts") or "")
                if dts[:10] < RECLASS_DATE:
                    continue
                m = o.get("pickup_to_delivery_min")
                if isinstance(m, (int, float)) and not isinstance(m, bool) and 0 < m < 180:
                    out[cid].append(float(m))
    except FileNotFoundError:
        pass
    return out


def _verdict(cid, med):
    name, tier, lo_ok, hi_ok = TARGET[cid]
    if tier == "slow":
        if med >= lo_ok:
            return f"✅ slow POTWIERDZONY (real med {med} min ≥ {lo_ok:.0f})"
        if med < 17:
            return f"⚠️ szybszy niż slow (real med {med} min) — rozważ powrót do std"
        return f"◻️ borderline std/slow (real med {med} min)"
    else:  # std+
        if med <= hi_ok:
            return f"✅ std+ POTWIERDZONY (real med {med} min ≤ {hi_ok:.0f})"
        if med > 19:
            return f"⚠️ wolniejszy niż std+ (real med {med} min) — rozważ powrót do std"
        return f"◻️ borderline std/std+ (real med {med} min)"


def main():
    deliv = _deliveries_post_reclass()
    ledger = _load_ledger()
    lines = []
    any_trigger = False
    all_done = True

    for cid, (name, tier, lo, hi) in TARGET.items():
        st = ledger.setdefault(cid, {"last_reported_n": 0, "verdict_sent": False})
        times = deliv.get(cid, [])
        n = len(times)
        med = _med(times)
        new_since = n - st.get("last_reported_n", 0)

        if not st.get("verdict_sent"):
            all_done = False

        # WERDYKT: osiągnięto próg i jeszcze nie wysłany
        if n >= VERDICT_N and not st.get("verdict_sent"):
            any_trigger = True
            lines.append(f"▸ {cid} {name} → {tier}: WERDYKT po {n} dostawach post-reklas")
            lines.append(f"    real: med {med} / p90 {_p90(times)} min")
            lines.append(f"    {_verdict(cid, med)}")
            st["verdict_sent"] = True
            st["last_reported_n"] = n
        # TRIGGER: nowe dostawy, werdykt jeszcze nie wydany → raport postępu
        elif new_since > 0 and not st.get("verdict_sent"):
            any_trigger = True
            lines.append(f"▸ {cid} {name} → {tier}: +{new_since} nowych dostaw "
                         f"(łącznie {n}/{VERDICT_N} do werdyktu)")
            lines.append(f"    real dotąd: med {med} / p90 {_p90(times)} min "
                         f"(oczek. {tier}: 207≈22 / 289≈13-16)")
            st["last_reported_n"] = n
        else:
            st["last_reported_n"] = n  # cisza (brak nowych albo już po werdykcie)

    _save_ledger(ledger)

    if not any_trigger:
        # cisza — nic nie wyzwolone (dzień niepracujący lub po obu werdyktach)
        print("[watch] brak triggera — cisza (no Telegram)")
        return

    header = "📡 Monitor reklasyfikacji tierów (wyzwalany) — 207 Marek→slow / 289 Grzegorz W→std+\n\n"
    footer = ""
    if all_done:
        footer = ("\n\n— Oba werdykty wydane. Monitor można wyłączyć: "
                  "`crontab -e` → usuń linię tier_reclass_trigger_watch.")
    msg = header + "\n".join(lines) + footer
    print(msg)

    if os.environ.get("TIER_MONITOR_NO_SEND") == "1":
        print("\n[telegram] SKIP (TIER_MONITOR_NO_SEND=1)")
        return
    try:
        sys.path.insert(0, "/root/.openclaw/workspace/scripts")
        from dispatch_v2 import telegram_utils
        ok = telegram_utils.send_admin_alert(msg)
        print("\n[telegram]", "sent" if ok else "send FAILED")
    except Exception as e:
        print(f"\n[telegram] import/send fail: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
