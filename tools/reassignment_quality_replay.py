"""reassignment_quality_replay — WERDYKT Krok 1: czy gate JAKOŚCI (quality_reassign) celuje dobrze.
Read-only. Join `reassignment_shadow.jsonl` (rekordy z quality_*) → `sla_log.jsonl` (realny wynik:
courier, delivery_time_minutes, sla_ok). Mierzy:
  • RAMIĘ RATUNEK (a_late): czy zlecenia, które gate chciał przerzucić „bo obecny się spóźni", REALNIE
    breachowały u obecnego (gdy człowiek je zostawił) = PRECYZJA ratunku;
  • RAMIĘ OSZCZĘDNOŚĆ (save≥próg): ile i jak duże save (counterfactual — raportowane, nie werdykt);
  • OVER-EAGER kontrola: quality_reassign=False a zostawione = powinny być on-time.
Werdykt materiału (precyzja ratunku), nie autonomii. Telegram opcjonalny (--notify)."""
import json
import argparse
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

SHADOW = "/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl"
SLA = "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"
# #1/#5b (top10): fizyczna prawda dostawy z GPS — PRIORYTET nad klikiem przy liczeniu
# „realnego breachu" (klik ZAWYŻA wiek o medianę +2min → zawyżałby precyzję ratunku).
GPS_TRUTH = "/root/.openclaw/workspace/dispatch_state/gps_delivery_truth.jsonl"
R6 = 35.0
_WAW = ZoneInfo("Europe/Warsaw")


def _ep(ts):
    if not ts:
        return None
    s = str(ts).strip()
    try:
        if "+" in s or s.endswith("Z") or (("T" in s) and s[-6] in "+-"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        return datetime.strptime(s.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_WAW).timestamp()
    except Exception:
        return None


def _physical_index():
    """{order_id: physical_delivered_at(epoch)} z gps_delivery_truth.jsonl (#1)."""
    idx = {}
    try:
        for ln in open(GPS_TRUTH):
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            e = _ep(d.get("physical_delivered_at"))
            if e is not None:
                idx[str(d.get("order_id"))] = e
    except FileNotFoundError:
        pass
    return idx


def _load_sla():
    phys = _physical_index()
    sla = {}
    for ln in open(SLA):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        oid = str(d.get("order_id"))
        dt = d.get("delivery_time_minutes")
        src = "button"
        # #1: jeśli jest fizyczny przyjazd GPS → policz dt OD GOTOWOŚCI fizycznie (picked_up→GPS-arrival)
        _pu = _ep(d.get("picked_up_at"))
        _ph = phys.get(oid)
        if _pu is not None and _ph is not None:
            dt = round((_ph - _pu) / 60.0, 1)
            src = "physical"
        sla[oid] = {
            "cid": str(d.get("courier_id")), "dt": dt,
            "ok": (dt <= R6) if isinstance(dt, (int, float)) else d.get("sla_ok"),
            "src": src}
    return sla


def _breach(s):
    dt = s["dt"]
    return (s["ok"] is False) or (isinstance(dt, (int, float)) and dt > R6)


def build_report(since=None):
    sla = _load_sla()
    # per-order: ostatni rekord z quality_* (gate liczony tylko gdy flaga ON)
    perq = {}
    for ln in open(SHADOW):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if "quality_reassign" not in d:
            continue
        if since and str(d.get("ts", "")) < since:
            continue
        perq[str(d.get("order_id"))] = d
    if not perq:
        return ("🔁 Q-GATE replay: brak rekordów z quality_* (flaga ENABLE_REASSIGN_QUALITY_GATE "
                "OFF lub collector nie zebrał — sprawdź drop-in/timer).")

    rescue = [d for d in perq.values() if d.get("quality_reassign") and d.get("a_late")]
    saving = [d for d in perq.values() if d.get("quality_reassign") and not d.get("a_late")]
    noflag = [d for d in perq.values() if not d.get("quality_reassign")]

    # #7 audyt 28.06: a_late = (a_cand None = infeasible/transient — holder poza pulą GPS/lag) LUB
    # (realna predykcja ETA a_bag_time>r6_late). PRECYZJA ma sens TYLKO dla gałęzi LATE-ETA;
    # infeasible (a_pred_deliver=None) NIE jest predykcją „obecny się spóźni" → liczona razem
    # zawyżała mianownik i dawała mylące „0%". Inwariant audytu: a_late ⇔ a_pred=None = 100%
    # infeasible, gałąź ETA 0 rek. → precyzja ETA = N/A (brak danych), NIE „0% (gate zły)".
    rescue_eta = [d for d in rescue if d.get("a_pred_deliver") is not None]
    rescue_infeasible = [d for d in rescue if d.get("a_pred_deliver") is None]

    # PRECYZJA RATUNKU (TYLKO gałąź LATE-ETA): z ratunków zostawionych u holdera — ile realnie
    # breachowało. Infeasible-transient WYKLUCZONE (nie są predykcją spóźnienia → nie mierzą gate'u).
    r_left = r_left_breach = r_left_phys = 0
    for d in rescue_eta:
        s = sla.get(str(d.get("order_id")))
        if not s:
            continue
        if s["cid"] in {str(d.get("holder_cid"))}:   # zostawione u holdera (nie przerzucone)
            r_left += 1
            if s.get("src") == "physical":           # #1: breach liczony na fizycznym przyjeździe GPS
                r_left_phys += 1
            if _breach(s):
                r_left_breach += 1
    # OVER-EAGER kontrola: noflag zostawione u holdera — ile on-time (powinno być ~wszystkie)
    n_left = n_left_ontime = 0
    for d in noflag:
        s = sla.get(str(d.get("order_id")))
        if s and s["cid"] in {str(d.get("holder_cid"))}:
            n_left += 1
            if not _breach(s):
                n_left_ontime += 1
    saves = [d.get("save_min") for d in saving if isinstance(d.get("save_min"), (int, float))]
    med_save = sorted(saves)[len(saves)//2] if saves else None

    prec = (100*r_left_breach/r_left) if r_left else None
    lines = [
        f"🔁 Q-GATE (gate jakości przerzutu) replay {since or 'all'} — MATERIAŁ",
        f"• zleceń z quality_*: {len(perq)} | ratunek: {len(rescue)} (late-ETA {len(rescue_eta)}, infeasible-transient {len(rescue_infeasible)}) | oszczędność: {len(saving)} | bez przerzutu: {len(noflag)}",
        f"• PRECYZJA RATUNKU (gałąź LATE-ETA): z {r_left} zostawionych u obecnego, REALNIE breach: "
        f"{r_left_breach} = {prec:.0f}% (#1: {r_left_phys}/{r_left} na FIZYCZNYM GPS, reszta klik)" if prec is not None else
        f"• PRECYZJA RATUNKU (gałąź LATE-ETA): N/A — {len(rescue_eta)} ratunków z realną predykcją ETA "
        f"({len(rescue_infeasible)} infeasible-transient WYKLUCZONE, nie mierzą gate'u). Gałąź ETA jeszcze nie dała danych.",
        f"• OSZCZĘDNOŚĆ: {len(saving)} propozycji, mediana save {med_save} min (counterfactual — nie werdykt)",
        f"• KONTROLA over-eager: z {n_left} 'bez przerzutu' zostawionych, on-time: "
        f"{n_left_ontime} = {100*n_left_ontime/n_left:.0f}%" if n_left else
        "• KONTROLA over-eager: brak danych",
        "",
        "📊 GO ramienia RATUNEK jeśli precyzja wysoka (gate trafnie wskazuje realne breachy) ORAZ "
        "over-eager-on-time wysoki (nie rusza dobrych). Decyzja ghost→live = Adrian.",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    rep = build_report(a.since)
    print(rep)
    if a.notify:
        try:
            from dispatch_v2 import telegram_utils as T
            T.send_admin_alert(rep)
        except Exception as e:
            print(f"[notify fail] {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
