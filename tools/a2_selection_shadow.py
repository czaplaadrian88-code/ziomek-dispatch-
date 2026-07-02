#!/usr/bin/env python3
"""a2_selection_shadow.py — A2 SHADOW: mierzy OFFLINE, READ-ONLY, jak soft-score
niezawodności kuriera zmieniłby SELEKCJĘ na przeszłych decyzjach i jaki to ma
wpływ na realne złamanie reguły 35 min (R6).

PĘTLA UCZENIA (audyt autonomii 2026-06-03, Faza 1 — dźwignia A2):
  retro_learning.py        → profile niezawodności per kurier (A2_COURIER_PROFILES)
  courier_reliability.py    → produkuje feed `courier_reliability.json` (breach_rate
                              per cid + fleet_median) — KONTRAKT czytany tutaj
  a2_selection_shadow.py    → APLIKUJE soft-score niezawodności w cieniu na puli
                              kandydatów z przeszłych decyzji → liczy: ile decyzji
                              zmieniłaby selekcja i czy nowy zwycięzca ma niższy
                              realny breach (= oczekiwana poprawa R6).
  → na tej podstawie decydujesz świadomie o flipie soft-score na żywo.

DLACZEGO OFFLINE, NIE HOT-PATH:
  Kod shadow wpięty w gorącą ścieżkę dispatch_pipeline już raz wywalił produkcję
  (incydent V3.27.4 NameError). Tu liczymy WYŁĄCZNIE z logów, które już istnieją
  (shadow_decisions.jsonl), zero ryzyka dla produkcji. Zgodne z Z2/Z3.

CO MIERZY (method="key_aware_v2", od 2026-06-03):
  Dla każdej decyzji verdict=="PROPOSE" z >=1 alternatywą:
    kandydaci = [best] + alternatives, odfiltrowani do feasibility=="MAYBE"
    i best_effort==False (realnie wykonalni, nie awaryjni).
    reliability_delta(cid) = -COEFF * max(0, breach_rate[cid] - fleet_median)
      Z CONFIDENCE-GATINGIEM (REFINEMENT 2): delta jest niezerowa TYLKO gdy
      confidence(cid) != "low" (czyli n_delivered >= --min-n) ORAZ
      (breach_rate[cid] - fleet_median) >= --min-gap. Inaczej delta = 0.
      Nieznany cid → 0 (brak kary).

  REALNY zwycięzca = best.courier_id (FAKT z logu, NIE argmax(score)).
  REFINEMENT 1 — mierzymy WZGLĘDEM realnego best, nie argmax(score). Realna
  selekcja Ziomka to klucz LEKSYKOGRAFICZNY (best bywa ma niższy score niż alt —
  w danych ~57% PROPOSE). Dlatego kandydat C może "przebić" best PRZEZ deltę
  niezawodności tylko jeśli:
    (a) C jest w NIE-GORSZYM koszyku kategorycznym klucza niż best, ORAZ
    (b) C.score + delta(C) > best.score + delta(best).
  SELEKCJA ZMIENIONA = istnieje taki C != best. new_winner = ten o max
  (score+delta) wśród spełniających (a). To eliminuje fałszywe zmiany napędzane
  samym score (stary argmax(score+delta) zawyżał % zmian ~46% i dawał ~28%
  "gorszych" swapów).

  KOSZYK KATEGORYCZNY (APROKSYMACJA klucza leksykograficznego z pól logu):
    pos_bucket = 2 jeśli pos_source in {"no_gps","pre_shift","none"}, inaczej 0
                 (informed). Pomijamy pośredni "other"=1 — przybliżenie.
    tier2_late = 1 jeśli late_pickup_committed_breach == True, inaczej 0
                 (tier-2 hard demote).
    "nie-gorszy koszyk": (tier2_late(C), pos_bucket(C)) <= (tier2_late(best),
                          pos_bucket(best)) leksykograficznie.
  Sweep COEFF ∈ {20, 40, 60, 100} (override: --coeff).

  Metryki per COEFF:
    - % decyzji zmienionych (wg nowej definicji REFINEMENT 1)
    - breach old→new (best vs new_winner z feedu) = oczekiwana poprawa
    - better:worse — swapy high→low breach vs swapy podnoszące breach
      (worse powinno DRAMATYCZNIE spaść vs 28% starego argmax).

OGRANICZENIA (jawne — patrz też raport):
  1. Koszyk kategoryczny = APROKSYMACJA klucza leksykograficznego (tylko 2 pola
     z logu: pos_source → {0,2}, late_pickup_committed_breach → {0,1}). Pełny
     klucz Ziomka ma więcej wymiarów i pośredni pos_bucket "other"=1 (pominięty).
  2. Liczymy TYLKO verdict=="PROPOSE" z >=2 wykonalnymi kandydatami (MAYBE,
     best_effort==False). Brak licznika dla KOORD i decyzji bez alternatyw.
  3. breach_rate to historyczny profil kuriera, nie predykcja per-to-zlecenie.

READ-ONLY. Output: raport po polsku + dispatch_state/a2_selection_shadow.jsonl (trend).
Uruchom:
  /root/.openclaw/venvs/dispatch/bin/python tools/a2_selection_shadow.py
Opcje: --coeff F (pojedyncza wartość zamiast sweepu), --max-lines N (default 200000),
       --min-n N (próg n_delivered/confidence dla gatingu delty, default 15),
       --min-gap F (min nadwyżka breach nad medianą by delta zadziałała, default 0.05),
       --reliability-json PATH, --decisions PATH (dla testu syntetycznego).
"""
import argparse
import json
import os
import statistics
import sys
import tempfile
from datetime import datetime, timezone

RELIABILITY_JSON = "/root/.openclaw/workspace/dispatch_state/courier_reliability.json"

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). Domyślny zbiór decyzji =
# files_in_window(ledger_io.LEDGER['shadow']) (pełny łańcuch .N.gz→.1→żywy);
# łączny limit --max-lines i re-użycie custom --decisions NIETKNIĘTE.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io
# Live (mały) + rotowany (duży — STRUMIENIOWANY linia po linii, nie wczytywany w całości).
SHADOW_LOG = "/root/.openclaw/workspace/dispatch_state/a2_selection_shadow.jsonl"

COEFF_SWEEP = [20.0, 40.0, 60.0, 100.0]


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ───────────────────────── feed niezawodności ─────────────────────────

def load_reliability(path):
    """Wczytuje feed courier_reliability.json (kontrakt). Zwraca
    (breach_by_cid: {cid:str -> breach_rate:float},
     conf_by_cid:   {cid:str -> confidence:str},   # "high"/"medium"/"low"
     fleet_median:float)
    albo None gdy plik nie istnieje / jest niezgodny.

    conf_by_cid jest potrzebne dla confidence-gatingu (REFINEMENT 2): kurierzy
    z confidence=="low" (mała próba) NIE dostają kary niezawodności — szum małej
    próby nie powinien przestawiać selekcji. Brak pola confidence → "low"
    (konserwatywnie: traktuj jako niepewny → bez kary)."""
    if not os.path.exists(path):
        print(f"BRAK feedu niezawodności: {path}\n"
              f"→ uruchom najpierw courier_reliability.py (produkuje ten plik).",
              file=sys.stderr)
        return None
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print(f"Zły JSON w {path} ({e}) — uruchom ponownie courier_reliability.py", file=sys.stderr)
        return None
    fleet_median = d.get("fleet_median_breach_rate")
    if not _num(fleet_median):
        print(f"Feed bez 'fleet_median_breach_rate' ({path}) — niezgodny kontrakt.", file=sys.stderr)
        return None
    couriers = d.get("couriers") or {}
    breach_by_cid = {}
    conf_by_cid = {}
    for cid, prof in couriers.items():
        prof = prof or {}
        br = prof.get("breach_rate")
        if _num(br):
            breach_by_cid[str(cid)] = float(br)
            conf = prof.get("confidence")
            conf_by_cid[str(cid)] = str(conf) if conf is not None else "low"
    return breach_by_cid, conf_by_cid, float(fleet_median)


# ───────────────────────── pula kandydatów per decyzja ─────────────────────────

def iter_decisions(paths, max_lines):
    """Strumieniuje rekordy decyzji linia po linii z podanych ścieżek (NIE wczytuje
    dużego pliku w całości). Zatrzymuje się po max_lines przeczytanych liniach
    łącznie. Zwraca generator dictów."""
    read = 0
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        with _rotated_logs.open_maybe_gz(path) as f:
            for line in f:
                if read >= max_lines:
                    return
                read += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue


def _feasible_candidates(rec):
    """[best] + alternatives, odfiltrowane do feasibility=='MAYBE' i best_effort==False.
    Zwraca listę dictów kandydatów (z polami courier_id, score, ...)."""
    out = []
    best = rec.get("best")
    if isinstance(best, dict):
        out.append(best)
    alts = rec.get("alternatives") or []
    for a in alts:
        if isinstance(a, dict):
            out.append(a)
    return [c for c in out
            if c.get("feasibility") == "MAYBE" and c.get("best_effort") is False]


_POS_BUCKET2 = frozenset({"no_gps", "pre_shift", "none"})


def _pos_bucket(cand):
    """Przybliżenie wymiaru "pewność pozycji" klucza leksykograficznego.
    2 = pozycja syntetyczna / brak GPS (no_gps/pre_shift/none) — twardy demote;
    0 = informed (cokolwiek innego, w tym gps, post_wave, last_*).
    Pomijamy pośredni "other"=1 (przybliżenie — patrz OGRANICZENIA)."""
    return 2 if (cand.get("pos_source") in _POS_BUCKET2) else 0


def _tier2_late(cand):
    """Przybliżenie wymiaru "tier-2 late-pickup hard demote".
    1 = late_pickup_committed_breach == True; 0 inaczej (None/False)."""
    return 1 if (cand.get("late_pickup_committed_breach") is True) else 0


def _key_bucket(cand):
    """Koszyk kategoryczny (tier2_late, pos_bucket) — krotka porównywalna
    leksykograficznie. NIŻSZA krotka = LEPSZY (preferowany) koszyk klucza."""
    return (_tier2_late(cand), _pos_bucket(cand))


def reliability_delta(cid, breach_by_cid, fleet_median, coeff,
                      conf_by_cid=None, min_gap=0.0):
    """Kara soft-score niezawodności z CONFIDENCE-GATINGIEM (REFINEMENT 2).

    Kara jest niezerowa TYLKO gdy SPEŁNIONE OBA warunki:
      (1) confidence(cid) != "low"  — kurier ma dość historii (n_delivered
          >= próg producenta feedu, sterowany --min-n na wejściu);
      (2) (breach_rate[cid] - fleet_median) >= min_gap — nadwyżka nad medianą
          jest realna, nie szum (--min-gap).
    Inaczej delta = 0. Nieznany cid (brak w feedzie) → 0.

    Bez gatingu (conf_by_cid=None i min_gap=0.0) zachowuje starą semantykę:
    delta = -coeff * max(0, breach - fleet_median). To pozwala testom
    jednostkowym sprawdzać samą funkcję kary, a gatingowi — odcinać szum."""
    br = breach_by_cid.get(str(cid))
    if br is None:
        return 0.0
    gap = br - fleet_median
    if gap < min_gap:
        return 0.0
    if conf_by_cid is not None and str(conf_by_cid.get(str(cid), "low")) == "low":
        return 0.0
    return -coeff * max(0.0, gap)


# ───────────────────────── pomiar per COEFF ─────────────────────────

def measure(decisions, breach_by_cid, fleet_median, coeff,
            conf_by_cid=None, min_gap=0.0):
    """Liczy metryki dla jednej wartości COEFF na strumieniu decyzji
    (method="key_aware_v2" — REFINEMENT 1 + 2).

    decisions: iterowalny rekordów (musi być re-iterowalny lub lista — patrz main).
    conf_by_cid / min_gap: gating delty (REFINEMENT 2). Gdy None/0.0 → kara bez
      gatingu (zachowanie zgodne ze starą semantyką funkcji kary).

    REFINEMENT 1: realny zwycięzca = best.courier_id (FAKT z logu). Kandydat C
    "przebija" best tylko gdy (a) NIE-GORSZY koszyk klucza niż best ORAZ
    (b) score(C)+delta(C) > score(best)+delta(best). new_winner = max
    (score+delta) wśród spełniających (a). Eliminuje fałszywe zmiany napędzane
    samym score.

    Zwraca dict z licznikami i listami breach_rate dla zmienionych decyzji."""
    n_eligible = 0          # PROPOSE z best(score) + >=1 wykonalnym alt(score)
    n_changed = 0           # selekcja zmieniona (key-aware)
    n_swap_better = 0       # new breach < old breach
    n_swap_worse = 0        # new breach > old breach (powinno DRAMATYCZNIE spaść)
    n_swap_equal_known = 0  # oba znane, breach identyczny
    old_breach_vals = []    # breach_rate(old_winner=best) dla zmienionych (znane)
    new_breach_vals = []    # breach_rate(new_winner) dla zmienionych (znane)

    def _delta(cid):
        return reliability_delta(cid, breach_by_cid, fleet_median, coeff,
                                 conf_by_cid=conf_by_cid, min_gap=min_gap)

    for rec in decisions:
        if rec.get("verdict") != "PROPOSE":
            continue
        cands = _feasible_candidates(rec)
        if len(cands) < 2:
            continue
        best = rec.get("best") or {}
        current = best.get("courier_id")
        best_score = best.get("score")
        # best MUSI być wykonalnym kandydatem z poprawnym score i cid
        if current is None or not _num(best_score):
            continue
        # best musi rzeczywiście być w puli wykonalnych (a nie odfiltrowany)
        if not any(str(c.get("courier_id")) == str(current) for c in cands):
            continue

        best_bucket = _key_bucket(best)
        best_adj = best_score + _delta(current)

        # alty z poprawnym score (C != best); zliczamy do eligible + filtrujemy
        # do tych w NIE-GORSZYM koszyku klucza (REFINEMENT 1 warunek (a)).
        n_alt_with_score = 0
        eligible_alts = []          # [(adj, cid)] dla C w nie-gorszym koszyku
        for c in cands:
            cid = c.get("courier_id")
            s = c.get("score")
            if cid is None or not _num(s) or str(cid) == str(current):
                continue
            n_alt_with_score += 1
            if _key_bucket(c) <= best_bucket:        # (a) nie-gorszy koszyk
                eligible_alts.append((s + _delta(cid), cid))

        # Decyzja kwalifikuje się gdy ma best(ze score) + >=1 wykonalny alt(score)
        # — niezależnie od koszyka, by % liczyć od pełnej puli porównywalnych.
        if n_alt_with_score < 1:
            continue
        n_eligible += 1

        if not eligible_alts:
            continue
        cand_adj, cand_cid = max(eligible_alts, key=lambda t: t[0])
        # (b) przebicie best przez deltę: ŚCIŚLE wyższy (score+delta) niż best
        if not (cand_adj > best_adj):
            continue

        new_winner = cand_cid
        n_changed += 1
        old_br = breach_by_cid.get(str(current))
        new_br = breach_by_cid.get(str(new_winner))
        if old_br is not None and new_br is not None:
            old_breach_vals.append(old_br)
            new_breach_vals.append(new_br)
            if new_br < old_br:
                n_swap_better += 1
            elif new_br > old_br:
                n_swap_worse += 1
            else:
                n_swap_equal_known += 1

    return {
        "coeff": coeff,
        "method": "key_aware_v2",
        "n_eligible": n_eligible,
        "n_changed": n_changed,
        "changed_rate": round(n_changed / n_eligible, 4) if n_eligible else None,
        "n_swap_better": n_swap_better,
        "n_swap_worse": n_swap_worse,
        "n_swap_equal_known": n_swap_equal_known,
        "mean_old_breach": round(statistics.mean(old_breach_vals), 4) if old_breach_vals else None,
        "mean_new_breach": round(statistics.mean(new_breach_vals), 4) if new_breach_vals else None,
        "mean_breach_improvement": (
            round(statistics.mean(old_breach_vals) - statistics.mean(new_breach_vals), 4)
            if old_breach_vals and new_breach_vals else None
        ),
        "n_changed_both_known": len(old_breach_vals),
    }


# ───────────────────────── raport ─────────────────────────

def print_report(results, n_decisions_read, fleet_median, n_couriers_in_feed,
                 min_n=None, min_gap=None):
    print("=" * 90)
    print("  A2-SELECTION SHADOW (key_aware_v2) — wpływ soft-score niezawodności na SELEKCJĘ + R6")
    print("=" * 90)
    print(f"  Przeczytane rekordy decyzji: {n_decisions_read}")
    print(f"  Mediana breach floty (z feedu): {fleet_median:.4f}  |  kurierów w feedzie: {n_couriers_in_feed}")
    if min_n is not None or min_gap is not None:
        print(f"  Gating delty (REFINEMENT 2): confidence != 'low' (n_delivered >= {min_n}) "
              f"ORAZ breach−mediana >= {min_gap}")
    if results:
        print(f"  Kwalifikujące się decyzje (PROPOSE, best + >=1 wykonalny alt ze score): "
              f"{results[0]['n_eligible']}")
    print()
    hdr = (f"  {'COEFF':>6}{'zmienione':>12}{'% zmian':>10}"
           f"{'breach old':>12}{'breach new':>12}{'Δ poprawa':>11}"
           f"{'better':>8}{'worse':>7}{'b:w':>9}")
    print(hdr)
    print("  " + "-" * 86)
    for m in results:
        cr = f"{m['changed_rate']*100:.1f}%" if m["changed_rate"] is not None else "—"
        ob = f"{m['mean_old_breach']:.3f}" if m["mean_old_breach"] is not None else "—"
        nb = f"{m['mean_new_breach']:.3f}" if m["mean_new_breach"] is not None else "—"
        imp = f"{m['mean_breach_improvement']:+.3f}" if m["mean_breach_improvement"] is not None else "—"
        bw = (f"{m['n_swap_better']}:{m['n_swap_worse']}")
        print(f"  {int(m['coeff']):>6}{m['n_changed']:>12}{cr:>10}"
              f"{ob:>12}{nb:>12}{imp:>11}"
              f"{m['n_swap_better']:>8}{m['n_swap_worse']:>7}{bw:>9}")
    print("  " + "-" * 86)
    print("  Legenda: '% zmian' = decyzje gdzie alt przebija REALNEGO best przez deltę (REFINEMENT 1:")
    print("           tylko z nie-gorszego koszyka klucza + score+delta>best). 'breach old/new' = śr.")
    print("           historyczny breach_rate best vs new_winner (zmiany z oboma cid znanymi). 'Δ poprawa'")
    print("           = old−new (>0 = mniej breachy). 'better:worse' = swapy high→low : low→high breach")
    print("           (worse powinno DRAMATYCZNIE spaść vs ~28% starego argmax(score+delta)).")
    print()
    print("  OGRANICZENIA:")
    print("   1. Koszyk kategoryczny = APROKSYMACJA klucza leksykograficznego — tylko 2 pola z logu:")
    print("      pos_source → {0=informed, 2=no_gps/pre_shift/none} (pośredni 'other'=1 pominięty),")
    print("      late_pickup_committed_breach → {0,1}. Pełny klucz Ziomka ma więcej wymiarów.")
    print("   2. Liczone TYLKO verdict=='PROPOSE' z best(score) + >=1 wykonalnym alt(score) (MAYBE,")
    print("      best_effort==False). Brak licznika dla KOORD i decyzji bez alternatyw.")
    print("   3. breach_rate = historyczny profil kuriera, nie predykcja per-zlecenie.")
    print(f"  → Decyzja flip soft-score = po 5-7 dniach trendu w {os.path.basename(SHADOW_LOG)}.")
    print("=" * 90)


# ───────────────────────── trend log (atomic append) ─────────────────────────

def append_trend(results, n_decisions_read, fleet_median, n_couriers_in_feed,
                 reliability_path, min_n=None, min_gap=None):
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": "key_aware_v2",          # odróżnia od starych wpisów (argmax score)
        "min_n": min_n,
        "min_gap": min_gap,
        "reliability_feed": reliability_path,
        "fleet_median_breach_rate": fleet_median,
        "n_couriers_in_feed": n_couriers_in_feed,
        "n_decisions_read": n_decisions_read,
        "n_eligible": results[0]["n_eligible"] if results else 0,
        "by_coeff": {str(int(m["coeff"])): {
            "n_changed": m["n_changed"],
            "changed_rate": m["changed_rate"],
            "mean_old_breach": m["mean_old_breach"],
            "mean_new_breach": m["mean_new_breach"],
            "mean_breach_improvement": m["mean_breach_improvement"],
            "n_swap_better": m["n_swap_better"],
            "n_swap_worse": m["n_swap_worse"],
        } for m in results},
    }
    try:
        line = json.dumps(rec, ensure_ascii=False)
        with open(SHADOW_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        print(f"\n✓ Trend dopisany: {SHADOW_LOG}")
    except Exception as e:
        print(f"⚠ nie zapisano trendu: {e}", file=sys.stderr)


# ───────────────────────── main ─────────────────────────

def main():
    ap = argparse.ArgumentParser(description="A2 selection shadow — offline, read-only.")
    ap.add_argument("--coeff", type=float, default=None,
                    help="pojedyncza wartość COEFF zamiast sweepu [20,40,60,100]")
    ap.add_argument("--max-lines", type=int, default=200000,
                    help="max linii decyzji do przeczytania łącznie (default 200000)")
    ap.add_argument("--min-n", type=int, default=15,
                    help="próg n_delivered/confidence dla gatingu delty (REFINEMENT 2): "
                         "kurier z confidence=='low' (n < min-n) NIE dostaje kary (default 15)")
    ap.add_argument("--min-gap", type=float, default=0.05,
                    help="min nadwyżka breach_rate nad medianą floty by delta zadziałała "
                         "(REFINEMENT 2, odcina szum małych różnic; default 0.05)")
    ap.add_argument("--reliability-json", default=RELIABILITY_JSON,
                    help="ścieżka do feedu courier_reliability.json")
    ap.add_argument("--decisions", default=None,
                    help="override ścieżki decyzji (pojedynczy plik; dla testu syntetycznego). "
                         "Domyślnie: pełny łańcuch rotation-aware (.N.gz→.1→żywy)")
    ap.add_argument("--no-trend", action="store_true",
                    help="nie dopisuj do trend logu (dla testów)")
    args = ap.parse_args()

    feed = load_reliability(args.reliability_json)
    if feed is None:
        return 1
    # load_reliability zwraca 3-krotkę (breach_by_cid, conf_by_cid, fleet_median).
    # conf_by_cid napędza confidence-gating (REFINEMENT 2) wewnątrz measure/reliability_delta.
    breach_by_cid, conf_by_cid, fleet_median = feed

    coeffs = [args.coeff] if args.coeff is not None else list(COEFF_SWEEP)

    if args.decisions:
        decision_paths = [args.decisions]
    else:
        decision_paths = _rotated_logs.files_in_window(ledger_io.LEDGER["shadow"])

    # Materializujemy decyzje raz (re-iterowalne dla sweepu COEFF). max_lines chroni RAM:
    # przy domyślnych 200k linii to akceptowalny narzut na offline tool.
    decisions = list(iter_decisions(decision_paths, args.max_lines))
    n_decisions_read = len(decisions)
    if n_decisions_read == 0:
        print("Brak rekordów decyzji do analizy (puste/nieistniejące pliki).", file=sys.stderr)
        return 1

    results = [measure(decisions, breach_by_cid, fleet_median, c,
                       conf_by_cid=conf_by_cid, min_gap=args.min_gap) for c in coeffs]

    print_report(results, n_decisions_read, fleet_median, len(breach_by_cid),
                 min_n=args.min_n, min_gap=args.min_gap)

    if not args.no_trend:
        append_trend(results, n_decisions_read, fleet_median, len(breach_by_cid),
                     args.reliability_json, min_n=args.min_n, min_gap=args.min_gap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
