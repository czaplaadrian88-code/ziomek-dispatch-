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

SHADOW = "/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl"
SLA = "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"
R6 = 35.0


def _load_sla():
    sla = {}
    for ln in open(SLA):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        sla[str(d.get("order_id"))] = {
            "cid": str(d.get("courier_id")), "dt": d.get("delivery_time_minutes"),
            "ok": d.get("sla_ok")}
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

    # PRECYZJA RATUNKU: z ratunków, które człowiek ZOSTAWIŁ u holdera — ile realnie breachowało
    r_left = r_left_breach = 0
    for d in rescue:
        s = sla.get(str(d.get("order_id")))
        if not s:
            continue
        if s["cid"] in {str(d.get("holder_cid"))}:   # zostawione u holdera (nie przerzucone)
            r_left += 1
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
        f"• zleceń z quality_*: {len(perq)} | ratunek: {len(rescue)} | oszczędność: {len(saving)} | bez przerzutu: {len(noflag)}",
        f"• PRECYZJA RATUNKU: z {r_left} ratunków zostawionych u obecnego, REALNIE breach: "
        f"{r_left_breach} = {prec:.0f}%" if prec is not None else
        "• PRECYZJA RATUNKU: brak ratunków zostawionych u obecnego (za mało danych)",
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
