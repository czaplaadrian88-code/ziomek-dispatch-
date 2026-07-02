#!/usr/bin/env python3
"""Monitor MIGOTANIA PROPOZYCJI (proposal churn) — READ-ONLY, ZERO wpływu na decyzje.

Krok 0c roadmapy: stały, powtarzalny BASELINE migotania top-1 proponowanego
kuriera MIĘDZY TICKAMI, ZANIM wejdzie histereza. Bez baseline nie da się
udowodnić, że przyszła histereza faktycznie zmniejsza churn bez regresji.

ŹRÓDŁO: dispatch_state/reassignment_shadow.jsonl — forward-shadow reassignmentu
(timer dispatch-reassignment-shadow co ~3 min). Każdy rekord = ocena JEDNEGO
zlecenia w JEDNYM ticku: kto je trzyma (`holder_cid`) vs kogo silnik uważa za
NAJLEPSZEGO (`best_cid`). `best_cid` = "top-1 proponowany kurier" dla zlecenia.
Churn = jak często `best_cid` zmienia się wzdłuż sekwencji ticków tego samego
`order_id`.

⚠ POZIOM = PROPOZYCJA (shadow), NIE commitowany przydział. To migotanie tego,
co silnik BY zaproponował, nie tego, co realnie wykonano. Górna granica na to,
ile histereza mogłaby wygładzić na wyjściu propozycji.

DEKOMPOZYCJA przyczyn zmiany (na tyle, na ile pola pozwalają):
  Log NIE zapisuje IMIENNEJ listy członków puli — tylko LICZBĘ `pool_feasible`.
  Nie da się więc twardo rozstrzygnąć "poprzedni best wypadł z puli"
  (feasibility churn — histereza NIE naprawi) vs "poprzedni best dalej feasible,
  scoring się przetasował" (czysty flicker — histereza naprawi). Liczymy PROXY:
    - pool_shrank : liczność puli spadła → prawdopodobnie ktoś wypadł (kandydat
      na feasibility churn; histereza słabo pomoże).
    - pool_grew   : liczność wzrosła → pojawiła się nowa, lepsza opcja.
    - pool_same   : liczność bez zmian, best się przetasował → NAJLEPSZY kandydat
      na czysty flicker (histereza pomoże NAJBARDZIEJ).
  Sygnatury flickera (DOLNE granice "histereza by naprawiła"):
    - revert A→B→A (sąsiedni) — best wraca do poprzednika w kolejnym ticku.
    - reappears_later — poprzedni best jest znów best w PÓŹNIEJSZYM ticku tego
      zlecenia → nie wypadł trwale, więc zmiana była przetasowaniem/oscylacją.
  Instabilność pozycji: `a_pos_source` (źródło pozycji holdera) zmienia się na
  ticku zmiany → churn napędzany re-estymacją pozycji (gps↔interp↔last_*), osobny
  root-cause od scoringu.

Uruchom (READ-ONLY, wypis na stdout):
  python3 -m dispatch_v2.tools.proposal_churn_monitor            # ostatnie 7 dni
  python3 -m dispatch_v2.tools.proposal_churn_monitor --all      # pełne okno
  python3 -m dispatch_v2.tools.proposal_churn_monitor --window-days 7
  python3 -m dispatch_v2.tools.proposal_churn_monitor --since 2026-06-25 --until 2026-07-02
  python3 -m dispatch_v2.tools.proposal_churn_monitor --per-day  # tabela dzienna

NIE pisze do dispatch_state ani nigdzie — tylko stdout. Zero git, zero systemd.
Propozycja timera (do przyszłego wdrożenia ZA ACK) na końcu tego pliku.
"""
import argparse
import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, Dict, List, Optional

try:
    from dispatch_v2.tools import ledger_io
except ImportError:  # uruchomienie z katalogu tools/
    _PKG_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _PKG_PARENT not in sys.path:
        sys.path.insert(0, _PKG_PARENT)
    from dispatch_v2.tools import ledger_io

SHADOW = "/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl"
DEFAULT_WINDOW_DAYS = 7


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """ts writera reassignment_shadow = aware ISO UTC. Przez kanon parse_sla_ts
    (aware → UTC), spójnie z resztą repo."""
    return ledger_io.parse_sla_ts(value)


def _parse_bound(s: Optional[str], end: bool) -> Optional[datetime]:
    """--since/--until: data 'YYYY-MM-DD' lub pełny ISO → aware UTC.
    Gołą datę traktujemy jako granicę doby UTC (until = koniec dnia)."""
    if not s:
        return None
    dt = _parse_ts(s)
    if dt is None:
        return None
    # goła data → fromisoformat da północ; --until ma objąć cały dzień
    if end and len(s.strip()) == 10:
        dt = dt + timedelta(days=1) - timedelta(microseconds=1)
    return dt


def load_records(since: Optional[datetime], until: Optional[datetime]) -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    if not os.path.exists(SHADOW):
        return recs
    with open(SHADOW) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = _parse_ts(r.get("ts"))
            if ts is None:
                continue
            if since and ts < since:
                continue
            if until and ts > until:
                continue
            r["_ts"] = ts
            recs.append(r)
    return recs


def analyze(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_order: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in recs:
        by_order[r.get("order_id")].append(r)

    ticks_per_order: List[int] = []
    changes_per_order: List[int] = []
    orders_multi = 0  # >=2 ticki (jedyne, które mogą churnować)
    ge1 = 0
    ge3 = 0

    total_changes = 0
    pool_cls = collections.Counter()   # shrank / same / grew
    holder_cls = collections.Counter() # appears / disappears / swap_between_others
    revert = 0
    reappear_later = 0
    possrc_at_change = 0
    possrc_no_change = 0
    would_reassign_share = collections.Counter()  # best==holder vs best!=holder na wszystkich rekordach

    for oid, rl in by_order.items():
        rl.sort(key=lambda r: r["_ts"])
        ticks_per_order.append(len(rl))
        bests = [r.get("best_cid") for r in rl]
        for r in rl:
            would_reassign_share["reassign_proposed" if r.get("would_reassign") else "hold"] += 1
        if len(rl) < 2:
            changes_per_order.append(0)
            continue
        orders_multi += 1
        ch = 0
        for i in range(1, len(rl)):
            prev, cur = rl[i - 1], rl[i]
            ps_changed = prev.get("a_pos_source") != cur.get("a_pos_source")
            if bests[i] != bests[i - 1]:
                ch += 1
                total_changes += 1
                pd = (cur.get("pool_feasible") or 0) - (prev.get("pool_feasible") or 0)
                if pd < 0:
                    pool_cls["pool_shrank"] += 1
                elif pd > 0:
                    pool_cls["pool_grew"] += 1
                else:
                    pool_cls["pool_same"] += 1
                h = cur.get("holder_cid")
                if bests[i - 1] == h and bests[i] != h:
                    holder_cls["propose_appears(holder->other)"] += 1
                elif bests[i - 1] != h and bests[i] == h:
                    holder_cls["propose_disappears(other->holder)"] += 1
                else:
                    holder_cls["swap_between_others"] += 1
                if i >= 2 and bests[i - 2] == bests[i]:
                    revert += 1
                if bests[i - 1] in bests[i + 1:]:
                    reappear_later += 1
                if ps_changed:
                    possrc_at_change += 1
            else:
                if ps_changed:
                    possrc_no_change += 1
        changes_per_order.append(ch)
        if ch >= 1:
            ge1 += 1
        if ch >= 3:
            ge3 += 1

    # rozkład zmian/zlecenie (tylko orders z >=2 tickami)
    dist = collections.Counter(c for c, n in zip(changes_per_order, ticks_per_order) if n >= 2)

    n_orders = len(by_order)
    return {
        "n_records": len(recs),
        "n_orders": n_orders,
        "n_orders_multitick": orders_multi,
        "ticks_median": median(ticks_per_order) if ticks_per_order else 0,
        "ticks_mean": round(mean(ticks_per_order), 2) if ticks_per_order else 0,
        "ticks_max": max(ticks_per_order) if ticks_per_order else 0,
        "ge1": ge1,
        "ge3": ge3,
        "mean_changes_per_order": round(mean(changes_per_order), 3) if changes_per_order else 0,
        "total_changes": total_changes,
        "pool_cls": dict(pool_cls),
        "holder_cls": dict(holder_cls),
        "revert": revert,
        "reappear_later": reappear_later,
        "possrc_at_change": possrc_at_change,
        "possrc_no_change": possrc_no_change,
        "would_reassign_share": dict(would_reassign_share),
        "dist": dict(sorted(dist.items())),
    }


def _pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.1f}%" if b else "n/d"


def render(rep: Dict[str, Any], label: str) -> str:
    L: List[str] = []
    N = rep["n_orders_multitick"]  # denom churnu = zlecenia z >=2 tickami
    tc = rep["total_changes"]
    L.append(f"=== PROPOSAL CHURN — {label} ===")
    L.append(f"rekordów(ticków)={rep['n_records']}  zleceń={rep['n_orders']}  "
             f"zleceń≥2ticki={N}  ticki/zlec: mediana={rep['ticks_median']} "
             f"śr={rep['ticks_mean']} max={rep['ticks_max']}")
    if N == 0:
        L.append("brak zleceń z ≥2 tickami w oknie — churn nieokreślony.")
        return "\n".join(L)
    L.append("")
    L.append("— MIGOTANIE top-1 (best_cid) —")
    L.append(f"  ≥1 zmiana:  {rep['ge1']}/{N} = {_pct(rep['ge1'], N)}")
    L.append(f"  ≥3 zmiany:  {rep['ge3']}/{N} = {_pct(rep['ge3'], N)}")
    L.append(f"  śr. zmian/zlecenie: {rep['mean_changes_per_order']}")
    L.append(f"  łącznie zdarzeń zmiany: {tc}")
    L.append("")
    L.append("— DEKOMPOZYCJA zmian (proxy; log ma LICZNOŚĆ puli, nie skład) —")
    L.append("  wg zmiany liczności puli feasible:")
    for k in ("pool_same", "pool_shrank", "pool_grew"):
        v = rep["pool_cls"].get(k, 0)
        L.append(f"    {k:12} {v:5} ({_pct(v, tc)})")
    L.append("  wg udziału holdera:")
    for k, v in sorted(rep["holder_cls"].items(), key=lambda kv: -kv[1]):
        L.append(f"    {k:34} {v:5} ({_pct(v, tc)})")
    L.append("  sygnatury czystego flickera (dolne granice; histereza BY naprawiła):")
    L.append(f"    revert A→B→A (sąsiedni):        {rep['revert']:5} ({_pct(rep['revert'], tc)})")
    L.append(f"    prev-best wraca jako best później:{rep['reappear_later']:5} ({_pct(rep['reappear_later'], tc)})")
    L.append(f"    pool_same (najczystszy proxy):   {rep['pool_cls'].get('pool_same',0):5} ({_pct(rep['pool_cls'].get('pool_same',0), tc)})")
    L.append("  instabilność pozycji holdera:")
    L.append(f"    a_pos_source zmienił się NA zmianie best: {rep['possrc_at_change']} ({_pct(rep['possrc_at_change'], tc)})")
    L.append(f"    a_pos_source zmienił się BEZ zmiany best: {rep['possrc_no_change']}")
    L.append("")
    L.append("— rozkład liczby zmian/zlecenie (zlec≥2ticki) —")
    dist = rep["dist"]
    for c in sorted(dist):
        bar = "#" * min(60, dist[c])
        L.append(f"    {c:3} zmian: {dist[c]:5} {bar}")
    wr = rep["would_reassign_share"]
    tot_wr = sum(wr.values()) or 1
    L.append("")
    L.append("— udział ticków proponujących reassign (kontekst) —")
    for k, v in sorted(wr.items(), key=lambda kv: -kv[1]):
        L.append(f"    {k:18} {v:6} ({_pct(v, tot_wr)})")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Proposal churn baseline (read-only).")
    ap.add_argument("--since", help="YYYY-MM-DD lub ISO (dolna granica, aware UTC)")
    ap.add_argument("--until", help="YYYY-MM-DD lub ISO (górna granica, aware UTC)")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                    help=f"okno wstecz gdy brak --since (default {DEFAULT_WINDOW_DAYS})")
    ap.add_argument("--all", action="store_true", help="pełne dostępne okno (ignoruje window-days)")
    ap.add_argument("--per-day", action="store_true", help="dodatkowo tabela per doba UTC")
    ap.add_argument("--json", action="store_true", help="wypisz też surowy słownik metryk (JSON)")
    args = ap.parse_args()

    since = _parse_bound(args.since, end=False)
    until = _parse_bound(args.until, end=True)

    if not args.all and since is None:
        # okno-dni wstecz od NAJPÓŹNIEJSZEGO rekordu (a nie od "teraz" — log może
        # nie sięgać dziś); until domyślnie = koniec danych
        all_recs = load_records(None, None)
        if not all_recs:
            print("[proposal_churn_monitor] brak rekordów w", SHADOW)
            return 1
        max_ts = max(r["_ts"] for r in all_recs)
        since = max_ts - timedelta(days=args.window_days)
        recs = [r for r in all_recs if r["_ts"] >= since and (until is None or r["_ts"] <= until)]
    else:
        recs = load_records(since, until)

    if not recs:
        print("[proposal_churn_monitor] brak rekordów w wybranym oknie")
        return 1

    lo = min(r["_ts"] for r in recs)
    hi = max(r["_ts"] for r in recs)
    label = f"{lo.date()}→{hi.date()} ({'pełne okno' if args.all else ('od '+args.since if args.since else f'{args.window_days}d')})"
    rep = analyze(recs)
    print(render(rep, label))

    if args.per_day:
        print("\n=== PER DOBA (UTC) ===")
        buckets: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
        for r in recs:
            buckets[r["_ts"].date().isoformat()].append(r)
        print(f"{'dzień':11} {'zlec≥2':>7} {'≥1%':>6} {'≥3%':>6} {'śr':>5} {'flick_same%':>11}")
        for day in sorted(buckets):
            d = analyze(buckets[day])
            N = d["n_orders_multitick"]
            same = d["pool_cls"].get("pool_same", 0)
            print(f"{day:11} {N:7} {_pct(d['ge1'],N):>6} {_pct(d['ge3'],N):>6} "
                  f"{d['mean_changes_per_order']:>5} {_pct(same, d['total_changes']):>11}")

    if args.json:
        rep.pop("_ts", None)
        print("\n=== JSON ===")
        print(json.dumps(rep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ─────────────────────────────────────────────────────────────────────────────
# PROPOZYCJA TIMERA (DO PRZYSZŁEGO WDROŻENIA ZA ACK — NIE INSTALOWAĆ TERAZ)
#
# /etc/systemd/system/dispatch-proposal-churn.service
#   [Unit]
#   Description=Proposal churn baseline (read-only, reassignment shadow)
#   [Service]
#   Type=oneshot
#   WorkingDirectory=/root/.openclaw/workspace/scripts
#   ExecStart=/usr/bin/python3 -m dispatch_v2.tools.proposal_churn_monitor --window-days 7 --per-day
#   # read-only: nie pisze do dispatch_state; wynik ląduje w journalu.
#
# /etc/systemd/system/dispatch-proposal-churn.timer
#   [Unit]
#   Description=Codzienny baseline migotania propozycji
#   [Timer]
#   OnCalendar=*-*-* 05:15:00 UTC
#   Persistent=true
#   [Install]
#   WantedBy=timers.target
#
# Po histerezie: ten sam tool na tym samym oknie → porównanie ≥1%/≥3%/śr/flick_same%
# ON vs OFF = dowód redukcji churnu bez regresji (parytet OFF musi zostać ~baseline).
