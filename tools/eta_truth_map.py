#!/usr/bin/env python3
"""eta_truth_map — segmentowana mapa błędu ETA Ziomka (Krok 0a „Pomiar Czasów 2.0").

READ-ONLY. Liczy błąd predykcji czasu ZE ZNAKIEM per segment, ROZDZIELNIE dla
DWÓCH nóg trasy:

  * NOGA ODBIORU (dojazd-po-odbiór): obiecany czas odbioru vs realny odbiór.
    predicted = plan.pickup_at[oid] (silnik, UTC aware).
    real      = sla_log.picked_up_at (przycisk „odebrane", NAIVE = czas Warszawy).
    błąd = predicted − real  [min].  ZNAK: „−" = kurier odebrał PÓŹNIEJ niż silnik
    obiecał = OPTYMIZM silnika.

  * NOGA DOSTAWY (dostawa): obiecany czas jazdy od odbioru vs realny.
    predicted = plan.per_order_delivery_times[oid] (min od odbioru, anchor-free).
    real      = sla_log.delivery_time_minutes (delivered − picked_up).
    błąd = predicted − real  [min].  ZNAK: „−" = dostawa TRWAŁA DŁUŻEJ niż obiecano
    = OPTYMIZM silnika.

Konwencja znaku (− = optymizm) jest CELOWA i zgodna z zadaniem 0a. UWAGA: to
ODWROTNY znak niż `eta_calibration_logger.eta_error_min` (tam + = za późno).

Dopasowanie predykcji do REALNEGO kuriera (nie do `best`): dla każdego zlecenia
z sla_log szukamy w puli kandydatów shadow (best + alternatives) kuriera ==
realny kurier, bierzemy JEGO plan (jak eta_calibration_logger v2). Gdy realnego
kuriera nie ma w żadnej puli → wiersz NIEDOPASOWANY (pomijany w metryce, liczony
w pokryciu). To jedyna metryka, na której wolno kalibrować.

ANTY-KŁAMSTWO PRZYRZĄDU:
  * timestampy sla_log (naive = Warszawa) parsowane przez KANONICZNY
    `ledger_io.parse_sla_ts` (naive→Warsaw→UTC). NIE zgadujemy stref.
  * czytanie logów przez `ledger_io.iter_sla` / `iter_shadow_decisions`
    (rotation-aware — naiwny odczyt żywego pliku gubi ~29% okna 7 dni: logrotate).
  * każda liczba ma jawne n; segmenty z n < --min-n → „ZA MAŁO DANYCH" bez liczby.
  * czasówki DOMYŚLNIE wykluczone (hold pod restauracją zaburza nogę odbioru);
    --include-czasowka włącza z powrotem.
  * założenia i bucketowanie wypisane w nagłówku raportu.

Uruchomienie (venv dispatch):
    /root/.openclaw/venvs/dispatch/bin/python tools/eta_truth_map.py \
        --since 2026-06-28 [--until 2026-07-02] [--min-n 20] [--out raport.md]
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# --- import kanonicznego ledger_io (rotation-aware readery + parse_sla_ts) ---
_THIS = os.path.dirname(os.path.abspath(__file__))
if _THIS not in sys.path:
    sys.path.insert(0, _THIS)
try:
    import ledger_io  # gdy uruchamiane z tools/ jako skrypt
except Exception:  # pragma: no cover — package-mode
    from dispatch_v2.tools import ledger_io

WARSAW = ZoneInfo("Europe/Warsaw")
PEAK_HOURS = frozenset({11, 12, 13, 17, 18, 19})
SHOULDER_HOURS = frozenset({10, 14, 15, 16, 20})


def _hour_bucket(hour):
    if hour is None:
        return None
    if hour in PEAK_HOURS:
        return "peak"
    if hour in SHOULDER_HOURS:
        return "shoulder"
    return "offpeak"


def _load_bucket(pf):
    """Bucket obciążenia floty z pool_feasible_count (proxy podaży kurierów)."""
    if pf is None:
        return "brak"
    try:
        pf = int(pf)
    except (TypeError, ValueError):
        return "brak"
    if pf <= 3:
        return "ciasno (<=3)"
    if pf <= 6:
        return "srednio (4-6)"
    if pf <= 9:
        return "luzno (7-9)"
    return "duza pula (>=10)"


def _cid(v):
    if v is None:
        return None
    return str(v).strip()


def _candidates(rec):
    out = []
    best = rec.get("best")
    if isinstance(best, dict) and best:
        out.append(best)
    for a in rec.get("alternatives") or []:
        if isinstance(a, dict):
            out.append(a)
    return out


def _bag_final(cand):
    """Finalny rozmiar baga: r6_bag_size+1 (fallbacki jak w eta_calibration_logger)."""
    b = cand.get("r6_bag_size")
    if b is None:
        b = cand.get("bag_size_before")
    if b is None:
        b = cand.get("r7_bag_size")
    return (b + 1) if isinstance(b, (int, float)) else None


def build_shadow_index(cutoff_dt):
    """oid -> lista (ts_utc, rec) posortowana rosnąco. Rotation-aware."""
    idx = {}
    for rec in ledger_io.iter_shadow_decisions(cutoff_dt):
        oid = rec.get("order_id")
        if oid is None:
            continue
        ts = ledger_io.parse_sla_ts(rec.get("ts"))
        if ts is None:
            continue
        idx.setdefault(str(oid), []).append((ts, rec))
    for oid in idx:
        idx[oid].sort(key=lambda x: x[0])
    return idx


def pick_for_real_courier(oid, real_cid, delivered_at, shadow_recs):
    """Najnowsza (<= delivered_at) decyzja, w której realny kurier jest w puli.

    Zwraca (ts, rec, cand) albo None. cand = kandydat realnego kuriera z planem.
    """
    if not shadow_recs:
        return None
    before = [(ts, r) for ts, r in shadow_recs
              if delivered_at is None or ts <= delivered_at] or shadow_recs
    if not real_cid:
        return None
    for ts, rec in reversed(before):
        for cand in _candidates(rec):
            if _cid(cand.get("courier_id")) != real_cid:
                continue
            plan = cand.get("plan") or {}
            has_pick = (plan.get("pickup_at") or {}).get(oid) is not None
            has_deliv = (plan.get("per_order_delivery_times") or {}).get(oid) is not None
            if has_pick or has_deliv:
                return (ts, rec, cand)
    return None


def build_rows(since_dt, until_dt, include_czasowka):
    """Buduje wiersze błędu per zlecenie (dopasowane do realnego kuriera).

    Zwraca (rows, stats) — rows: lista dict z pickup_err/deliv_err + wymiary
    segmentów; stats: liczniki pokrycia.
    """
    # Predykcje bywają robione ~40 min przed odbiorem — poszerzamy okno shadow
    # wstecz o 3h, żeby złapać decyzję dla zlecenia dostarczonego tuż po --since.
    shadow_cutoff = since_dt - timedelta(hours=3)
    shadow_idx = build_shadow_index(shadow_cutoff)

    rows = []
    stats = {"sla_in_window": 0, "czasowka_skipped": 0, "no_shadow": 0,
             "no_real_courier_match": 0, "matched": 0,
             "pickup_ok": 0, "deliv_ok": 0}

    for sla in ledger_io.iter_sla(since_dt):
        delivered_at = ledger_io.parse_sla_ts(sla.get("delivered_at"))
        if delivered_at is None:
            continue
        if delivered_at < since_dt or (until_dt is not None and delivered_at > until_dt):
            continue
        stats["sla_in_window"] += 1

        if sla.get("was_czasowka") and not include_czasowka:
            stats["czasowka_skipped"] += 1
            continue

        oid = str(sla.get("order_id"))
        real_cid = _cid(sla.get("courier_id"))
        picked_up_at = ledger_io.parse_sla_ts(sla.get("picked_up_at"))
        real_deliv_min = sla.get("delivery_time_minutes")

        recs = shadow_idx.get(oid)
        if not recs:
            stats["no_shadow"] += 1
            continue
        picked = pick_for_real_courier(oid, real_cid, delivered_at, recs)
        if picked is None:
            stats["no_real_courier_match"] += 1
            continue
        stats["matched"] += 1
        shadow_ts, rec, cand = picked
        plan = cand.get("plan") or {}

        # NOGA ODBIORU: predicted_pickup − real_pickup (min). − = optymizm.
        pickup_err = None
        pred_pick = ledger_io.parse_sla_ts((plan.get("pickup_at") or {}).get(oid))
        if pred_pick is not None and picked_up_at is not None:
            pickup_err = round((pred_pick - picked_up_at).total_seconds() / 60.0, 2)
            stats["pickup_ok"] += 1

        # NOGA DOSTAWY: predicted_delivery_min − real_delivery_min. − = optymizm.
        deliv_err = None
        pred_deliv = (plan.get("per_order_delivery_times") or {}).get(oid)
        if isinstance(pred_deliv, (int, float)) and isinstance(real_deliv_min, (int, float)):
            deliv_err = round(pred_deliv - real_deliv_min, 2)
            stats["deliv_ok"] += 1

        if pickup_err is None and deliv_err is None:
            continue

        hour = picked_up_at.astimezone(WARSAW).hour if picked_up_at else None
        bag = _bag_final(cand)
        rows.append({
            "oid": oid,
            "pickup_err": pickup_err,
            "deliv_err": deliv_err,
            "courier": real_cid,
            "tier": cand.get("v326_speed_tier_used"),
            "bag_size": bag,
            "solo_bundle": ("bundle" if (bag is not None and bag >= 2)
                            else ("solo" if bag is not None else None)),
            "load_bucket": _load_bucket(rec.get("pool_feasible_count")),
            "pool_feasible": rec.get("pool_feasible_count"),
            "hour": hour,
            "hour_bucket": _hour_bucket(hour),
            "restaurant": sla.get("restaurant"),
            "prediction_age_min": (round((picked_up_at - shadow_ts).total_seconds() / 60.0, 1)
                                   if picked_up_at is not None else None),
        })
    return rows, stats


def _seg_stats(values):
    """(n, median, p10, p90) z listy floatów. Zwraca None-y gdy pusto."""
    v = sorted(x for x in values if x is not None)
    if not v:
        return (0, None, None, None)
    n = len(v)
    med = statistics.median(v)
    p10 = v[max(0, int(round(0.10 * (n - 1))))]
    p90 = v[min(n - 1, int(round(0.90 * (n - 1))))]
    return (n, med, p10, p90)


def _fmt_row(label, values, min_n):
    n, med, p10, p90 = _seg_stats(values)
    if n < min_n:
        return f"| {label} | {n} | ZA MAŁO DANYCH | — | — |"
    return f"| {label} | {n} | {med:+.1f} | {p10:+.1f} | {p90:+.1f} |"


def _segment_table(title, rows, key_fn, leg_key, min_n, sort_by_n=False, top=None):
    groups = {}
    for r in rows:
        if r.get(leg_key) is None:
            continue
        k = key_fn(r)
        if k is None:
            k = "(brak)"
        groups.setdefault(str(k), []).append(r[leg_key])
    if not groups:
        return f"### {title}\n_(brak wierszy z tą nogą)_\n"
    items = list(groups.items())
    if sort_by_n:
        items.sort(key=lambda kv: -len(kv[1]))
    else:
        items.sort(key=lambda kv: kv[0])
    if top:
        items = items[:top]
    lines = [f"### {title}",
             "| segment | n | mediana (min, − = optymizm) | p10 | p90 |",
             "|---|---|---|---|---|"]
    for k, vals in items:
        lines.append(_fmt_row(k, vals, min_n))
    return "\n".join(lines) + "\n"


def _leg_block(leg_name, rows, leg_key, min_n):
    out = [f"## NOGA: {leg_name}"]
    overall = [r[leg_key] for r in rows if r.get(leg_key) is not None]
    n, med, p10, p90 = _seg_stats(overall)
    if n < min_n:
        out.append(f"**Ogółem: n={n} — ZA MAŁO DANYCH**\n")
    else:
        out.append(f"**Ogółem: n={n}  mediana={med:+.1f} min  p10={p10:+.1f}  "
                   f"p90={p90:+.1f}**  (− = optymizm silnika)\n")
    out.append(_segment_table("Wg tieru kuriera (v326_speed_tier_used)",
                              rows, lambda r: r["tier"], leg_key, min_n))
    out.append(_segment_table("Wg solo vs bundle",
                              rows, lambda r: r["solo_bundle"], leg_key, min_n))
    out.append(_segment_table("Wg rozmiaru baga (bag_size)",
                              rows, lambda r: r["bag_size"], leg_key, min_n))
    out.append(_segment_table("Wg obciążenia floty (pool_feasible)",
                              rows, lambda r: r["load_bucket"], leg_key, min_n))
    out.append(_segment_table("Wg pory dnia (bucket)",
                              rows, lambda r: r["hour_bucket"], leg_key, min_n))
    out.append(_segment_table("Wg godziny (Warsaw)",
                              rows, lambda r: r["hour"], leg_key, min_n))
    out.append(_segment_table("Wg kuriera (top 25 po n)",
                              rows, lambda r: r["courier"], leg_key, min_n,
                              sort_by_n=True, top=25))
    out.append(_segment_table("Wg restauracji (top 25 po n)",
                              rows, lambda r: r["restaurant"], leg_key, min_n,
                              sort_by_n=True, top=25))
    return "\n".join(out)


def _parse_day(s):
    if s is None:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(s, "%Y-%m-%d")
    if dt.tzinfo is None:
        # dzień bez godziny podany jako czas Warszawy (spójnie z sensem okna)
        dt = dt.replace(tzinfo=WARSAW)
    return dt.astimezone(timezone.utc)


def build_report(rows, stats, since_dt, until_dt, min_n, include_czasowka):
    now = datetime.now(WARSAW).isoformat()
    ages = [r["prediction_age_min"] for r in rows if r.get("prediction_age_min") is not None]
    age_med = statistics.median(ages) if ages else None
    lines = [
        "# Pomiar Czasów 2.0 — segmentowana mapa błędu ETA (Krok 0a)",
        f"_Wygenerowano: {now}  ·  narzędzie: tools/eta_truth_map.py (read-only)_",
        "",
        "## Okno i założenia",
        f"- Okno (delivered_at): **{since_dt.isoformat()} → "
        f"{until_dt.isoformat() if until_dt else 'teraz'}** (UTC).",
        f"- Próg segmentu: **--min-n = {min_n}** (segment poniżej → ZA MAŁO DANYCH).",
        f"- Czasówki: **{'WLICZONE' if include_czasowka else 'WYKLUCZONE'}** "
        "(domyślnie wykluczone — hold pod restauracją zaburza nogę odbioru).",
        "- **Znak błędu: minus = OPTYMIZM silnika** (odbiór później / dostawa dłużej "
        "niż obiecano). Uwaga: to odwrotny znak niż `eta_error_min` w "
        "eta_calibration_logger.",
        "- Predykcja = plan REALNEGO kuriera (dopasowanie best+alternatives po "
        "courier_id), nie `best`. Niedopasowane zlecenia pominięte w metryce.",
        "- Timestampy sla_log (naive) parsowane jako czas Warszawy → UTC przez "
        "kanoniczny `ledger_io.parse_sla_ts`. Logi czytane rotation-aware.",
        "- Noga ODBIORU: `plan.pickup_at[oid]` − `sla.picked_up_at`.",
        "- Noga DOSTAWY: `plan.per_order_delivery_times[oid]` − "
        "`sla.delivery_time_minutes` (obie = minuty od odbioru, anchor-free).",
        f"- Bucket obciążenia z `pool_feasible_count`: ciasno<=3 / 4-6 / 7-9 / >=10.",
        f"- Mediana wieku predykcji (shadow_ts→odbiór): "
        f"{f'{age_med:.0f} min' if age_med is not None else 'brak'}.",
        "",
        "## Pokrycie joinu (uczciwe n)",
        f"- Zleceń w oknie (sla, delivered): **{stats['sla_in_window']}**",
        f"- Czasówki pominięte: {stats['czasowka_skipped']}",
        f"- Bez rekordu shadow (utracone okno / brak decyzji): {stats['no_shadow']}",
        f"- Realny kurier poza pulą kandydatów (niedopasowane): "
        f"{stats['no_real_courier_match']}",
        f"- **Dopasowane do realnego kuriera: {stats['matched']}**",
        f"  - z nogą ODBIORU (pickup_err): {stats['pickup_ok']}",
        f"  - z nogą DOSTAWY (deliv_err): {stats['deliv_ok']}",
        "",
    ]
    lines.append(_leg_block("ODBIÓR (dojazd-po-odbiór)", rows, "pickup_err", min_n))
    lines.append("")
    lines.append(_leg_block("DOSTAWA (od odbioru do klienta)", rows, "deliv_err", min_n))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Segmentowana mapa błędu ETA (read-only).")
    ap.add_argument("--since", default=None,
                    help="Start okna delivered_at (ISO lub YYYY-MM-DD; domyślnie 7 dni wstecz).")
    ap.add_argument("--until", default=None, help="Koniec okna (ISO lub YYYY-MM-DD; domyślnie teraz).")
    ap.add_argument("--min-n", type=int, default=20, help="Min n segmentu (domyślnie 20).")
    ap.add_argument("--include-czasowka", action="store_true",
                    help="Wlicz czasówki (domyślnie wykluczone).")
    ap.add_argument("--out", default=None, help="Zapis raportu do pliku (domyślnie stdout).")
    args = ap.parse_args(argv)

    since_dt = _parse_day(args.since) if args.since else (
        datetime.now(timezone.utc) - timedelta(days=7))
    until_dt = _parse_day(args.until) if args.until else None

    rows, stats = build_rows(since_dt, until_dt, args.include_czasowka)
    report = build_report(rows, stats, since_dt, until_dt, args.min_n, args.include_czasowka)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print(f"raport zapisany: {args.out}  (wierszy dopasowanych: {stats['matched']})")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
