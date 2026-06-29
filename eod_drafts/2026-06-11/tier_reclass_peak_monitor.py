#!/usr/bin/env python3
"""Monitor efektu reklasyfikacji tierów 207 (std->slow) + 289 (std->std+) w peaku 2026-06-11.

Kontekst (2026-06-10): zreklasyfikowano 207 Marek std->slow, 289 Grzegorz W std->std+,
370 Jakub OL std+->std (kalibracja czasów do tierów, [[lessons]] #179). ALE diagnoza
przed deployem: 207/289 NIE pojawiają się w learning_log (0 propozycji w 3 dni) — łapią
zlecenia sami z apki (self-assign), nie pracowali od 06-07/06-06. Więc „efekt selekcji"
może być zero-sygnał. Ten monitor odpowiada DEFINITYWNIE w każdym przypadku:

  1. Czy 207/289 w ogóle pracowali w peaku (orders_state delivered, czasy real).
  2. Czy Ziomek ich proponował (learning_log: best/alt/win + czy nowy tier zastosowany).
  3. Realne czasy dostaw vs przypisany tier (207 slow~22min / 289 fast~13-16min).
  4. Sanity A+B floty: czy nowe wartości DWELL/mult lecą w peakowych decyzjach.

READ-ONLY. Wysyła werdykt na Telegram Adriana. Uruchamiane przez `at` ~13:15 UTC
(15:15 Warsaw) po peaku 11-14 Warsaw + dojazdach. Ręcznie:
    cd /root/.openclaw/workspace/scripts
    /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/eod_drafts/2026-06-11/tier_reclass_peak_monitor.py
"""
import json
import os
import statistics
from datetime import datetime, date
from zoneinfo import ZoneInfo

STATE = "/root/.openclaw/workspace/dispatch_state"
LEARNING = os.path.join(STATE, "learning_log.jsonl")
ORDERS = os.path.join(STATE, "orders_state.json")
WARSAW = ZoneInfo("Europe/Warsaw")

TARGET = {"207": ("Marek", "slow", (20.0, 24.0)),       # (imię, nowy tier, oczekiwany zakres real-min)
          "289": ("Grzegorz W", "std+", (13.0, 17.0))}
# Peak Warsaw 11:00-14:00; dla dostaw okno do 14:45 (dojazdy). Data celu = dziś (dzień uruchomienia).
PEAK_W_START, PEAK_W_END = 11, 14            # godzina Warsaw (decyzje)
DELIV_W_END_H, DELIV_W_END_M = 14, 45        # koniec okna dostaw Warsaw


def _today_warsaw():
    return datetime.now(WARSAW).date()


def _med(a):
    return round(statistics.median(a), 1) if a else None


def _parse_naive_warsaw(s):
    """orders_state czasy to stringi Warsaw 'YYYY-MM-DD HH:MM:SS'."""
    if not s or not isinstance(s, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _load_learning():
    rows = []
    try:
        with open(LEARNING, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return rows


def _ts_utc_hour(r):
    d = r.get("decision") or {}
    ts = str(r.get("ts") or d.get("ts") or "")
    return ts


def real_deliveries_today(day):
    """Z orders_state: realne dostawy per cid w oknie peaku (pickup->delivery min)."""
    try:
        with open(ORDERS) as f:
            data = json.load(f)
    except Exception as e:
        return {}, f"orders_state read fail: {e}"
    out = {cid: [] for cid in TARGET}
    iterable = data.values() if isinstance(data, dict) else data
    for o in iterable:
        if not isinstance(o, dict):
            continue
        cid = str(o.get("courier_id") or "")
        if cid not in TARGET:
            continue
        pu = _parse_naive_warsaw(o.get("picked_up_at"))
        dl = _parse_naive_warsaw(o.get("delivered_at"))
        if not pu or not dl:
            continue
        if pu.date() != day:
            continue
        # pickup w oknie peaku (11:00 - 14:45 Warsaw)
        end = pu.replace(hour=DELIV_W_END_H, minute=DELIV_W_END_M, second=0)
        start = pu.replace(hour=PEAK_W_START, minute=0, second=0)
        if not (start <= pu <= end):
            continue
        mins = (dl - pu).total_seconds() / 60.0
        if 0 < mins < 180:
            out[cid].append((o.get("order_id"), round(mins, 1)))
    return out, None


def ziomek_appearances(rows, day):
    """learning_log: wystąpienia 207/289 jako best/alternative w oknie peaku + czy nowy tier."""
    daystr = day.isoformat()
    res = {cid: {"best": 0, "alt": 0, "won_bag": [], "tier_applied": set(), "mult": set()} for cid in TARGET}
    for r in rows:
        ts = _ts_utc_hour(r)
        if not ts.startswith(daystr):
            continue
        # okno peaku w UTC = 09-12 (11-14 Warsaw, lato +2)
        try:
            hh = int(ts[11:13])
        except (ValueError, IndexError):
            continue
        if not (9 <= hh < 13):
            continue
        d = r.get("decision") or {}
        b = d.get("best") or {}
        bcid = str(b.get("courier_id") or "")
        if bcid in TARGET:
            res[bcid]["best"] += 1
            res[bcid]["won_bag"].append(b.get("r6_bag_size"))
            if b.get("dwell_tier"):
                res[bcid]["tier_applied"].add(b.get("dwell_tier"))
            if b.get("v326_speed_multiplier") is not None:
                res[bcid]["mult"].add(b.get("v326_speed_multiplier"))
        for a in (d.get("alternatives") or []):
            acid = str(a.get("courier_id") or "")
            if acid in TARGET:
                res[acid]["alt"] += 1
                if a.get("dwell_tier"):
                    res[acid]["tier_applied"].add(a.get("dwell_tier"))
                if a.get("v326_speed_multiplier") is not None:
                    res[acid]["mult"].add(a.get("v326_speed_multiplier"))
    return res


def fleet_ab_sanity(rows, day):
    """Czy nowe DWELL lecą w peakowych decyzjach floty (potwierdza A+B live)."""
    daystr = day.isoformat()
    NEW_DWELL = {"gold": 1.5, "std+": 2.5, "std": 4.5, "slow": 6.5, "new": 6.5}
    seen = {}   # tier -> set(dwell)
    n = 0
    for r in rows:
        ts = _ts_utc_hour(r)
        if not ts.startswith(daystr):
            continue
        try:
            hh = int(ts[11:13])
        except (ValueError, IndexError):
            continue
        if not (9 <= hh < 13):
            continue
        d = r.get("decision") or {}
        for node in [d.get("best")] + list(d.get("alternatives") or []):
            if not isinstance(node, dict):
                continue
            t = node.get("dwell_tier")
            dd = node.get("dwell_dropoff_min")
            if t and dd is not None:
                seen.setdefault(t, set()).add(dd)
                n += 1
    lines = []
    for t in ("gold", "std+", "std", "slow", "new"):
        if t in seen:
            ok = NEW_DWELL[t] in seen[t]
            lines.append(f"   {t}: dwell={sorted(seen[t])} {'✅nowe' if ok else '⚠️STARE?'}")
    return n, lines


def main():
    day = _today_warsaw()
    rows = _load_learning()
    deliv, deliv_err = real_deliveries_today(day)
    appear = ziomek_appearances(rows, day)
    n_ab, ab_lines = fleet_ab_sanity(rows, day)

    L = []
    L.append(f"📊 MONITOR reklasyfikacji tierów — peak {day.isoformat()} (11-14 Warsaw)")
    L.append("Reklas. 10.06: 207 Marek std→slow · 289 Grzegorz W std→std+")
    L.append("")
    for cid, (name, tier, (lo, hi)) in TARGET.items():
        d = deliv.get(cid, [])
        a = appear.get(cid, {})
        L.append(f"▸ {cid} {name} (teraz {tier}):")
        # praca
        if d:
            times = [m for _, m in d]
            med = _med(times)
            zgodne = (lo - 2) <= med <= (hi + 3) if med is not None else False
            L.append(f"   pracował: {len(d)} dostaw, mediana {med} min "
                     f"(oczek. {tier}≈{lo:.0f}-{hi:.0f}) {'✅zgodne' if zgodne else '⚠️rozjazd'}")
        else:
            L.append("   NIE pracował w peaku (brak dostaw w orders_state)")
        # propozycje Ziomka
        if a.get("best") or a.get("alt"):
            tiers = ",".join(sorted(a["tier_applied"])) or "?"
            mults = ",".join(str(m) for m in sorted(a["mult"])) or "?"
            bags = [b for b in a["won_bag"] if b is not None]
            L.append(f"   Ziomek proponował: best={a['best']} kandydat={a['alt']} | "
                     f"tier_zastosowany={tiers} mult={mults}"
                     + (f" | worki(best)={bags}" if bags else ""))
        else:
            L.append("   Ziomek NIE proponował (0 wystąpień — self-assign jak dotąd)")
        L.append("")

    # werdykt syntetyczny
    worked = any(deliv.get(c) for c in TARGET)
    proposed = any(appear[c].get("best") or appear[c].get("alt") for c in TARGET)
    if not worked and not proposed:
        L.append("WERDYKT: 207/289 nie pracowali ani nie byli proponowani w peaku — "
                 "zero sygnału (zgodnie z przewidywaniem: sporadyczni + self-assign). "
                 "Reklas. zadziała dopiero gdy realnie wejdą w pulę Ziomka. Obserwuj kolejne dni robocze.")
    elif worked and not proposed:
        L.append("WERDYKT: pracowali ale poza pętlą Ziomka (self-assign) — realne czasy walidują "
                 "przypisany tier, ale efekt cap/score niewidoczny (Ziomek ich nie dispatchuje).")
    else:
        L.append("WERDYKT: byli w propozycjach Ziomka — sprawdź wyżej czy nowy tier/cap/score "
                 "zadziałał poprawnie (207 deprio+mały worek, 289 boost+większy worek).")

    L.append("")
    L.append(f"— Sanity A+B floty (peak, {n_ab} węzłów): nowe DWELL lecą?")
    L.extend(ab_lines or ["   (brak decyzji peakowych do sprawdzenia)"])
    if deliv_err:
        L.append(f"⚠️ {deliv_err}")

    msg = "\n".join(L)
    print(msg)
    if os.environ.get("TIER_MONITOR_NO_SEND") == "1":
        print("\n[telegram] SKIP (TIER_MONITOR_NO_SEND=1)")
        return
    try:
        import sys
        sys.path.insert(0, "/root/.openclaw/workspace/scripts")  # robust gdy uruchamiane jako skrypt (at-job)
        from dispatch_v2 import telegram_utils
        ok = telegram_utils.send_admin_alert(msg)
        print("\n[telegram]", "sent" if ok else "send FAILED")
    except Exception as e:
        print(f"\n[telegram] import/send fail: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
