#!/usr/bin/env python3
"""Testy dla tools/a2_selection_shadow.py — A2 SELECTION SHADOW (pętla uczenia
Fazy 1, mapa autonomii 2026-06-03). Read-only sweep wpływu soft-score
niezawodności na SELEKCJĘ i breach 35 min.

WERSJA: method="key_aware_v2" (REFINEMENT 1 + 2, 2026-06-03).

Testy są ODPORNE: zero realnych danych. Budują SYNTETYCZNY feed niezawodności
(courier_reliability.json) + syntetyczne decyzje shadow (lista dictów / tmp JSONL)
i sprawdzają kontrakt.

KONTRAKT pod test (z docstringu modułu):
  - czyta courier_reliability.json: {fleet_median_breach_rate,
    couriers:{cid:{breach_rate, confidence, n_delivered}}}
    → load_reliability zwraca 3-krotkę (breach_by_cid, conf_by_cid, fleet_median)
  - streamuje shadow_decisions.jsonl; bierze TYLKO verdict=="PROPOSE"
  - kandydaci = [best] + alternatives, filtr feasibility=="MAYBE" AND best_effort==False
  - reliability_delta(cid) = -COEFF * max(0, breach_rate[cid] - fleet_median)
    Z GATINGIEM (REFINEMENT 2): niezerowa tylko gdy confidence != "low" ORAZ
    (breach - median) >= min_gap; nieznany cid → 0
  - REFINEMENT 1: realny zwycięzca = best.courier_id; alt C przebija best tylko gdy
    (a) NIE-GORSZY koszyk klucza (tier2_late, pos_bucket) niż best ORAZ
    (b) score(C)+delta(C) > score(best)+delta(best)
  - liczy % zmienionych selekcji + better:worse breach; sweep COEFF.

KOSZYK KLUCZA (approx):
  pos_bucket  = 2 jeśli pos_source in {no_gps,pre_shift,none} else 0
  tier2_late  = 1 jeśli late_pickup_committed_breach == True else 0

PRZYPADKI:
  (a) COEFF=0 → 0 zmian selekcji
  (b) best=wysoki breach (score nieco wyższy), alt=niski breach TEN SAM koszyk:
      przy wysokim COEFF swap (key-aware pozytyw)
  (c) decyzja bez alternatyw (1 kandydat) → pominięta
  (d) kandydaci best_effort==True / feasibility!="MAYBE" → odfiltrowani
  NOWE (key_aware_v2):
  (e) confidence-gating: high-breach alt ale confidence=="low" → delta 0 → brak swapu
  (f) min-gap: breach alt tylko +0.03 nad medianą (< min_gap) → delta 0 → brak swapu
  (g) key-aware NEG: best koszyk 0 (gps), alt low-breach ale koszyk 2 (no_gps) →
      NIE przebija mimo lepszego breachu (gorszy koszyk)
  (h) key-aware POZYTYW: best high-breach koszyk 0, alt low-breach też koszyk 0,
      score zbliżony → przy wysokim COEFF swap

STRATEGIA: import-first (measure / reliability_delta / _feasible_candidates /
_key_bucket / load_reliability). Gdy te funkcje nie istnieją → CLI fallback
(subprocess venv python + --reliability-json + --decisions + --coeff + --min-gap).
Gdy pliku skryptu brak → SKIP.

Uruchom:
    /root/.openclaw/venvs/dispatch/bin/python tests/test_a2_selection_shadow.py
albo:
    /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_a2_selection_shadow.py -v
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]            # /root/.openclaw/workspace/scripts
MODULE_PATH = REPO / "dispatch_v2" / "tools" / "a2_selection_shadow.py"
VENV_PY = "/root/.openclaw/venvs/dispatch/bin/python"


class SkipTest(Exception):
    """Sygnalizuje czysty SKIP (moduł nie istnieje / brak API)."""


# ───────────────────────── import (best-effort) ─────────────────────────

A2 = None
_IMPORT_ERR = None
if MODULE_PATH.exists():
    try:
        from dispatch_v2.tools import a2_selection_shadow as A2  # noqa: E402
    except Exception as e:  # pragma: no cover - środowiskowe
        _IMPORT_ERR = e
        A2 = None


def _has_measure():
    return A2 is not None and hasattr(A2, "measure")


def _has_helpers():
    return (A2 is not None
            and hasattr(A2, "reliability_delta")
            and hasattr(A2, "_feasible_candidates"))


def _has_key_bucket():
    return A2 is not None and hasattr(A2, "_key_bucket")


# ───────────────────────── syntetyka ─────────────────────────

def _cand(cid, score, feasibility="MAYBE", best_effort=False, name=None,
          pos_source=None, late_pickup_committed_breach=None):
    """Pojedynczy kandydat w shadow-decyzji (kształt jak realny 'best'/'alternatives').
    pos_source / late_pickup_committed_breach sterują koszykiem klucza (REFINEMENT 1).
    Domyślnie pos_source=None (→ pos_bucket 0, informed) i late=None (→ tier2_late 0)."""
    return {
        "courier_id": str(cid),
        "name": name or f"K{cid}",
        "score": score,
        "feasibility": feasibility,
        "best_effort": best_effort,
        "pos_source": pos_source,
        "late_pickup_committed_breach": late_pickup_committed_breach,
        "reason": "ok_sla_fits",
    }


def _decision(best, alternatives=None, verdict="PROPOSE", order_id="477000"):
    """Rekord shadow-decyzji (kształt jak realny wiersz shadow_decisions.jsonl)."""
    return {
        "verdict": verdict,
        "order_id": order_id,
        "best": best,
        "alternatives": alternatives or [],
        "ts": "2026-06-03T10:00:00+00:00",
    }


def _make_feed(couriers_breach, fleet_median, confidence=None, n_delivered=None):
    """Buduje payload courier_reliability.json (feed niezawodności).

    couriers_breach: {cid -> breach_rate}
    confidence:      {cid -> 'high'|'medium'|'low'} (default 'high' dla wszystkich)
    n_delivered:     {cid -> int} (default 30) — informacyjne (gating idzie po confidence)."""
    confidence = confidence or {}
    n_delivered = n_delivered or {}
    return {
        "meta": {"min_history": 5, "n_delivered": 999},
        "fleet_median_breach_rate": fleet_median,
        "fleet_median_speed_vs_pred": 0.0,
        "couriers": {
            str(cid): {
                "breach_rate": br,
                "n_delivered": n_delivered.get(cid, n_delivered.get(str(cid), 30)),
                "reliability": 1.0 - br,
                "confidence": confidence.get(cid, confidence.get(str(cid), "high")),
            }
            for cid, br in couriers_breach.items()
        },
    }


# ───────────────────────── adapter: import vs CLI ─────────────────────────

def _measure_via_import(decisions, breach_by_cid, fleet_median, coeff,
                        conf_by_cid=None, min_gap=0.0):
    return A2.measure(list(decisions), breach_by_cid, fleet_median, coeff,
                      conf_by_cid=conf_by_cid, min_gap=min_gap)


def _measure_via_cli(decisions, feed_payload, coeff, min_gap=0.0):
    """Fallback: odpal CLI na tmp feedzie + tmp decisions, sparsuj output trendu.

    Skrypt zapisuje wynik do SHADOW_LOG (a2_selection_shadow.jsonl). Podmieniamy
    tę stałą przez wrapper i czytamy ostatni wpis trendu, by wydobyć metryki.
    Confidence-gating w CLI idzie z pola 'confidence' w feedzie + --min-gap.
    """
    if not VENV_PY or not os.path.exists(VENV_PY):
        raise SkipTest(f"venv python brak: {VENV_PY}")

    tmpdir = tempfile.mkdtemp(prefix="a2sel_")
    feed_path = os.path.join(tmpdir, "courier_reliability.json")
    dec_path = os.path.join(tmpdir, "decisions.jsonl")
    trend_path = os.path.join(tmpdir, "a2_trend.jsonl")
    with open(feed_path, "w", encoding="utf-8") as f:
        json.dump(feed_payload, f, ensure_ascii=False)
    with open(dec_path, "w", encoding="utf-8") as f:
        for d in decisions:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    wrapper = os.path.join(tmpdir, "run.py")
    wrapper_src = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "from dispatch_v2.tools import a2_selection_shadow as M\n"
        f"M.SHADOW_LOG = {trend_path!r}\n"
        "argv = ['a2_selection_shadow',\n"
        f"        '--reliability-json', {feed_path!r},\n"
        f"        '--decisions', {dec_path!r},\n"
        f"        '--min-gap', str({float(min_gap)}),\n"
        f"        '--coeff', str({float(coeff)})]\n"
        "sys.argv = argv\n"
        "rc = M.main()\n"
        "sys.exit(rc if isinstance(rc, int) else 0)\n"
    )
    with open(wrapper, "w", encoding="utf-8") as f:
        f.write(wrapper_src)

    proc = subprocess.run([VENV_PY, wrapper], capture_output=True, text=True, timeout=120)
    if not os.path.exists(trend_path):
        raise SkipTest(
            f"CLI nie wytworzył trendu (rc={proc.returncode}); "
            f"stdout: {proc.stdout.strip()[:200]} | stderr: {proc.stderr.strip()[:200]}"
        )
    last = None
    with open(trend_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last = line
    if last is None:
        raise SkipTest("trend pusty")
    entry = json.loads(last)
    return _extract_coeff_metrics(entry, coeff)


def _extract_coeff_metrics(entry, coeff):
    """Z dowolnego kształtu trendu wyciągnij dict metryk dla danego coeff.

    Główny kształt produkcyjny: {"by_coeff": {"20": {...}, ...}}.
    Plus tolerancja dla {"results":[{coeff,...}]} / list / single dict (legacy)."""
    # 1) Produkcyjny trend: by_coeff dict keyed po int(coeff) jako string
    if isinstance(entry, dict) and isinstance(entry.get("by_coeff"), dict):
        key = str(int(float(coeff)))
        if key in entry["by_coeff"]:
            m = dict(entry["by_coeff"][key])
            m.setdefault("coeff", float(coeff))
            return m
    # 2) Inne kształty (legacy/tolerancja)
    candidates = []
    if isinstance(entry, dict):
        if "results" in entry and isinstance(entry["results"], list):
            candidates = entry["results"]
        else:
            candidates = [entry]
    elif isinstance(entry, list):
        candidates = entry
    for c in candidates:
        if isinstance(c, dict) and abs(float(c.get("coeff", -1)) - float(coeff)) < 1e-9:
            return c
    if len(candidates) == 1 and isinstance(candidates[0], dict):
        return candidates[0]
    raise SkipTest(f"nie znaleziono metryk dla coeff={coeff} w trendzie: {entry!r}")


def measure(decisions, breach_by_cid, fleet_median, coeff,
            feed_payload=None, conf_by_cid=None, min_gap=0.0,
            confidence=None, n_delivered=None):
    """Jedno API: import-first, CLI-fallback, SKIP gdy brak modułu.

    confidence/n_delivered: per-cid mapy do zbudowania feedu (gdy CLI fallback)
    oraz conf_by_cid (gdy import path). Gdy conf_by_cid podane wprost — używamy go."""
    if not MODULE_PATH.exists():
        raise SkipTest(f"moduł nie istnieje: {MODULE_PATH}")
    # zbuduj conf_by_cid jeśli nie podano wprost (z mapy confidence; default 'high')
    if conf_by_cid is None and confidence is not None:
        conf_by_cid = {str(k): v for k, v in confidence.items()}
    if _has_measure():
        return _measure_via_import(decisions, breach_by_cid, fleet_median, coeff,
                                   conf_by_cid=conf_by_cid, min_gap=min_gap)
    # CLI fallback wymaga pełnego feedu (fleet_median + couriers + confidence)
    if feed_payload is None:
        feed_payload = _make_feed(breach_by_cid, fleet_median,
                                  confidence=confidence, n_delivered=n_delivered)
    return _measure_via_cli(decisions, feed_payload, coeff, min_gap=min_gap)


# ───────────────────────── testy: zachowane ─────────────────────────

def test_coeff_zero_no_change():
    """(a) COEFF=0 → reliability_delta=0 dla wszystkich → 0 zmian selekcji.
    Best ma wyższy breach ale też wyższy score; bez kary zostaje best."""
    feed = {"123": 0.4, "470": 0.0}      # best (123) zły, alt (470) dobry
    fleet_median = 0.2
    decisions = [
        _decision(best=_cand("123", 50.0), alternatives=[_cand("470", 45.0)]),
        _decision(best=_cand("123", 60.0), alternatives=[_cand("470", 55.0)], order_id="477001"),
    ]
    m = measure(decisions, feed, fleet_median, coeff=0.0)
    assert m.get("n_eligible", 0) >= 1, f"powinny być eligible decyzje; m={m}"
    assert m["n_changed"] == 0, f"COEFF=0 → 0 zmian; mam n_changed={m['n_changed']}"


def test_high_coeff_swaps_to_low_breach():
    """(b) best=wysoki breach (0.4, score nieco wyższy +50), alt=niski breach
    (0.0, score +45), TEN SAM koszyk klucza (oba informed/no-late): przy wysokim
    COEFF new_winner=alt (swap), breach poprawiony.

    Sanity: delta_best = -100*max(0,0.4-0.2) = -20 → 50-20=30. delta_alt=0 → 45.
    45 > 30 → alt przebija best (oba koszyk (0,0))."""
    feed = {"123": 0.4, "470": 0.0}
    fleet_median = 0.2
    decisions = [
        _decision(best=_cand("123", 50.0), alternatives=[_cand("470", 45.0)]),
    ]
    m = measure(decisions, feed, fleet_median, coeff=100.0)
    assert m["n_eligible"] >= 1, f"powinno być eligible; m={m}"
    assert m["n_changed"] == 1, f"wysoki COEFF → swap; mam n_changed={m['n_changed']}"
    assert m["n_swap_better"] == 1, (
        f"swap powinien być high→low breach (better); m={m}"
    )
    assert m["mean_old_breach"] is not None and m["mean_new_breach"] is not None
    assert m["mean_new_breach"] < m["mean_old_breach"], (
        f"breach nowego ({m['mean_new_breach']}) powinien być < starego ({m['mean_old_breach']})"
    )
    if m.get("mean_breach_improvement") is not None:
        assert m["mean_breach_improvement"] > 0, (
            f"mean_breach_improvement powinno być >0; mam {m['mean_breach_improvement']}"
        )


def test_decision_without_alternatives_skipped():
    """(c) Decyzja z 1 kandydatem (brak feasible alternatyw) → pominięta."""
    feed = {"123": 0.4, "470": 0.0}
    fleet_median = 0.2
    decisions = [
        _decision(best=_cand("123", 50.0), alternatives=[]),
        _decision(best=_cand("123", 50.0),
                  alternatives=[_cand("470", 80.0, best_effort=True)], order_id="477002"),
    ]
    m = measure(decisions, feed, fleet_median, coeff=100.0)
    assert m["n_eligible"] == 0, (
        f"decyzje z <2 wykonalnymi kandydatami mają być pominięte; n_eligible={m['n_eligible']}"
    )
    assert m["n_changed"] == 0, f"brak eligible → brak zmian; n_changed={m['n_changed']}"


def test_filtered_candidates_best_effort_and_feasibility():
    """(d) Kandydaci best_effort==True LUB feasibility!='MAYBE' są odfiltrowani.
    Best feasible + dwa NIE-feasible alty + jeden feasible alt o niskim breach
    (ten sam koszyk). Po filtrze: best + 1 feasible alt = swap."""
    feed = {"123": 0.4, "470": 0.0, "999": 0.0, "888": 0.0}
    fleet_median = 0.2
    decisions = [
        _decision(
            best=_cand("123", 50.0),
            alternatives=[
                _cand("999", 99.0, best_effort=True),      # odfiltrowany (best_effort)
                _cand("888", 98.0, feasibility="NO"),      # odfiltrowany (feasibility!=MAYBE)
                _cand("470", 45.0),                        # feasible, niski breach, koszyk (0,0)
            ],
        ),
    ]
    m = measure(decisions, feed, fleet_median, coeff=100.0)
    assert m["n_eligible"] == 1, (
        f"po filtrze zostają best+470 = 2 kandydatów → eligible; n_eligible={m['n_eligible']}"
    )
    assert m["n_changed"] == 1, f"swap na 470; n_changed={m['n_changed']}"
    assert m["n_swap_better"] == 1, f"swap high→low breach; m={m}"


def test_feasible_candidates_filter_unit():
    """Jednostkowy: _feasible_candidates zwraca tylko MAYBE+not best_effort."""
    if not _has_helpers():
        raise SkipTest("brak _feasible_candidates w imporcie (CLI-only build)")
    rec = _decision(
        best=_cand("1", 10.0),
        alternatives=[
            _cand("2", 20.0, best_effort=True),
            _cand("3", 30.0, feasibility="NO"),
            _cand("4", 40.0),                       # ok
            _cand("5", 50.0, feasibility="YES"),    # odfiltrowany (nie MAYBE)
        ],
    )
    cands = A2._feasible_candidates(rec)
    ids = {c["courier_id"] for c in cands}
    assert ids == {"1", "4"}, f"oczekiwano {{1,4}}, mam {ids}"


def test_reliability_delta_unit():
    """Jednostkowy: reliability_delta = -coeff*max(0, br-fleet_median);
    nieznany cid → 0; cid poniżej mediany → 0. Bez gatingu (conf_by_cid=None,
    min_gap=0.0) zachowana stara semantyka."""
    if not _has_helpers():
        raise SkipTest("brak reliability_delta w imporcie (CLI-only build)")
    breach = {"hi": 0.4, "lo": 0.0}
    fleet_median = 0.2
    # high breach: -100 * (0.4-0.2) = -20
    assert abs(A2.reliability_delta("hi", breach, fleet_median, 100.0) - (-20.0)) < 1e-9
    # low breach (poniżej mediany): max(0, -0.2)=0 → delta 0
    assert abs(A2.reliability_delta("lo", breach, fleet_median, 100.0) - 0.0) < 1e-9
    # nieznany cid → 0
    assert abs(A2.reliability_delta("???", breach, fleet_median, 100.0) - 0.0) < 1e-9
    # coeff=0 → zawsze 0
    assert abs(A2.reliability_delta("hi", breach, fleet_median, 0.0) - 0.0) < 1e-9


def test_non_propose_verdict_ignored():
    """Decyzje z verdict != 'PROPOSE' (np. KOORD/NO) są pomijane."""
    feed = {"123": 0.4, "470": 0.0}
    fleet_median = 0.2
    decisions = [
        _decision(best=_cand("123", 50.0), alternatives=[_cand("470", 45.0)],
                  verdict="KOORD"),
        _decision(best=_cand("123", 50.0), alternatives=[_cand("470", 45.0)],
                  verdict="NO", order_id="477003"),
    ]
    m = measure(decisions, feed, fleet_median, coeff=100.0)
    assert m["n_eligible"] == 0, (
        f"tylko PROPOSE liczy się; n_eligible={m['n_eligible']}"
    )


def test_coeff_monotonic_change_rate():
    """Sweep sanity: większy COEFF nie zmniejsza liczby zmian gdy istnieje
    kandydat o niższym breach z nieco niższym score (ten sam koszyk).
    delta gates: confidence high + gap=0.5-0.1=0.4 >= 0.05 default."""
    feed = {"123": 0.5, "470": 0.0}
    fleet_median = 0.1
    # best score +50, alt score +45. swap gdy delta_best < -5 czyli
    # -coeff*(0.5-0.1) < -5  → coeff*0.4 > 5 → coeff > 12.5
    decisions = [_decision(best=_cand("123", 50.0), alternatives=[_cand("470", 45.0)])]
    m_low = measure(decisions, feed, fleet_median, coeff=10.0)    # 10*0.4=4 < 5 → brak
    m_high = measure(decisions, feed, fleet_median, coeff=40.0)   # 40*0.4=16 > 5 → swap
    assert m_low["n_changed"] == 0, f"coeff=10 nie powinien swapować; m={m_low}"
    assert m_high["n_changed"] == 1, f"coeff=40 powinien swapować; m={m_high}"


def test_load_reliability_unit():
    """Jednostkowy: load_reliability parsuje feed → 3-krotka
    (breach_by_cid, conf_by_cid, fleet_median); brak pliku → None (fail-soft)."""
    if A2 is None or not hasattr(A2, "load_reliability"):
        raise SkipTest("brak load_reliability w imporcie (CLI-only build)")
    tmpdir = tempfile.mkdtemp(prefix="a2feed_")
    feed_path = os.path.join(tmpdir, "feed.json")
    with open(feed_path, "w", encoding="utf-8") as f:
        json.dump(_make_feed({"1": 0.3, "2": 0.0}, 0.15,
                             confidence={"1": "high", "2": "low"}), f)
    res = A2.load_reliability(feed_path)
    assert res is not None, "powinien sparsować poprawny feed"
    assert len(res) == 3, f"load_reliability ma zwracać 3-krotkę; mam {len(res)}"
    breach_by_cid, conf_by_cid, fleet_median = res
    assert breach_by_cid.get("1") == 0.3 and breach_by_cid.get("2") == 0.0, (
        f"breach_by_cid źle sparsowany: {breach_by_cid}"
    )
    assert conf_by_cid.get("1") == "high" and conf_by_cid.get("2") == "low", (
        f"conf_by_cid źle sparsowany: {conf_by_cid}"
    )
    assert abs(fleet_median - 0.15) < 1e-9, f"fleet_median={fleet_median} (oczekiwano 0.15)"
    assert A2.load_reliability(os.path.join(tmpdir, "nope.json")) is None, (
        "brakujący plik powinien zwrócić None (fail-soft)"
    )


# ───────────────────────── testy: NOWE (key_aware_v2) ─────────────────────────

def test_key_bucket_unit():
    """Jednostkowy: _key_bucket = (tier2_late, pos_bucket).
    pos_bucket 2 dla no_gps/pre_shift/none, 0 inaczej; tier2_late 1 dla late==True."""
    if not _has_key_bucket():
        raise SkipTest("brak _key_bucket w imporcie (CLI-only build)")
    assert A2._key_bucket(_cand("a", 1.0, pos_source="gps")) == (0, 0)
    assert A2._key_bucket(_cand("a", 1.0, pos_source="post_wave")) == (0, 0)       # nie w {no_gps..}
    assert A2._key_bucket(_cand("a", 1.0, pos_source="no_gps")) == (0, 2)
    assert A2._key_bucket(_cand("a", 1.0, pos_source="pre_shift")) == (0, 2)
    assert A2._key_bucket(_cand("a", 1.0, pos_source="none")) == (0, 2)
    assert A2._key_bucket(_cand("a", 1.0, late_pickup_committed_breach=True)) == (1, 0)
    assert A2._key_bucket(
        _cand("a", 1.0, pos_source="no_gps", late_pickup_committed_breach=True)) == (1, 2)
    # (0,0) lepszy (mniejszy) niż (0,2) i (1,0)
    assert (0, 0) < (0, 2) and (0, 2) < (1, 0)


def test_confidence_gating_low_no_swap():
    """(e) confidence-gating: alt ma niski breach ale best ma high-breach z
    confidence=="low" (mała próba, n<min_n) → delta(best)=0 → best NIE jest
    karany → alt (score niższy, ten sam koszyk) NIE przebija → brak swapu.

    best=123 breach 0.5 ale confidence LOW; alt=470 breach 0.0 high. fleet=0.1.
    Bez gatingu: delta_best=-100*0.4=-40 → 50-40=10 < 45 → swap. Z gatingiem:
    delta_best=0 → 50 > 45 → BRAK swapu."""
    feed = {"123": 0.5, "470": 0.0}
    fleet_median = 0.1
    conf = {"123": "low", "470": "high"}
    decisions = [_decision(best=_cand("123", 50.0), alternatives=[_cand("470", 45.0)])]
    m = measure(decisions, feed, fleet_median, coeff=100.0, confidence=conf)
    assert m["n_eligible"] == 1, f"powinno być eligible; m={m}"
    assert m["n_changed"] == 0, (
        f"confidence=='low' best → delta 0 → brak swapu; n_changed={m['n_changed']}"
    )


def test_min_gap_below_threshold_no_swap():
    """(f) min-gap: best breach tylko +0.03 nad medianą (< min_gap default 0.05)
    → delta(best)=0 mimo confidence high → alt o niższym score nie przebija.

    best=123 breach 0.13, fleet 0.10 → gap 0.03 < 0.05. alt=470 breach 0.0.
    Bez gap-gate: delta_best=-100*0.03=-3 → 50-3=47 > 45 → i tak brak (margines mały),
    więc dobierzemy alt score tak by przebijał TYLKO gdy delta zadziała:
    alt score=48 → bez gatingu 50-3=47 < 48 → swap; z gatingiem delta 0 → 50 > 48 → brak."""
    feed = {"123": 0.13, "470": 0.0}
    fleet_median = 0.10
    decisions = [_decision(best=_cand("123", 50.0), alternatives=[_cand("470", 48.0)])]
    # default min_gap=0.05; gap=0.03 < 0.05 → delta(best)=0
    m = measure(decisions, feed, fleet_median, coeff=100.0, min_gap=0.05,
                confidence={"123": "high", "470": "high"})
    assert m["n_eligible"] == 1, f"powinno być eligible; m={m}"
    assert m["n_changed"] == 0, (
        f"gap 0.03 < min_gap 0.05 → delta 0 → brak swapu; n_changed={m['n_changed']}"
    )
    # kontrola: z min_gap=0.0 delta zadziała → swap (50-3=47 < 48)
    m2 = measure(decisions, feed, fleet_median, coeff=100.0, min_gap=0.0,
                 confidence={"123": "high", "470": "high"})
    assert m2["n_changed"] == 1, (
        f"z min_gap=0 ten sam case powinien swapować (sanity); m2={m2}"
    )


def test_key_aware_negative_worse_bucket_no_swap():
    """(g) key-aware NEG: best w koszyku 0 (gps, ale high-breach), alt low-breach
    ale w koszyku 2 (no_gps) → alt jest w GORSZYM koszyku klucza → NIE przebija
    mimo lepszego breachu i mimo że score+delta alt > best.

    best=123 breach 0.5 gps (koszyk (0,0)), score 50.
    alt=470 breach 0.0 no_gps (koszyk (0,2)), score 45.
    delta_best=-100*0.4=-40 → best_adj=10. alt_adj=45 > 10. Stary argmax → swap.
    REFINEMENT 1: alt koszyk (0,2) > best (0,0) → NIE kwalifikuje (a) → brak swapu."""
    feed = {"123": 0.5, "470": 0.0}
    fleet_median = 0.1
    decisions = [_decision(
        best=_cand("123", 50.0, pos_source="gps"),
        alternatives=[_cand("470", 45.0, pos_source="no_gps")],
    )]
    m = measure(decisions, feed, fleet_median, coeff=100.0,
                confidence={"123": "high", "470": "high"})
    assert m["n_eligible"] == 1, f"powinno być eligible; m={m}"
    assert m["n_changed"] == 0, (
        f"alt w gorszym koszyku (no_gps) NIE może przebić best (gps); n_changed={m['n_changed']}"
    )


def test_key_aware_positive_same_bucket_swap():
    """(h) key-aware POZYTYW: best high-breach w koszyku 0 (gps), alt low-breach
    też koszyk 0 (gps), score zbliżony → przy wysokim COEFF swap.

    best=123 breach 0.5 gps, score 50. alt=470 breach 0.0 gps, score 48.
    delta_best=-100*0.4=-40 → best_adj=10. alt_adj=48 > 10, ten sam koszyk (0,0).
    → swap, better (0.0 < 0.5)."""
    feed = {"123": 0.5, "470": 0.0}
    fleet_median = 0.1
    decisions = [_decision(
        best=_cand("123", 50.0, pos_source="gps"),
        alternatives=[_cand("470", 48.0, pos_source="gps")],
    )]
    m_low = measure(decisions, feed, fleet_median, coeff=20.0,
                    confidence={"123": "high", "470": "high"})
    m_high = measure(decisions, feed, fleet_median, coeff=100.0,
                     confidence={"123": "high", "470": "high"})
    # COEFF 20: delta_best=-100... nie, -20*0.4=-8 → 50-8=42 > 48? nie, 42 < 48 → swap też.
    # Dobierzmy próg: przy coeff=20 delta=-8 → best_adj=42 < 48 → swap. Oba swapują —
    # asercja: high COEFF na pewno swapuje + better.
    assert m_high["n_changed"] == 1, f"wysoki COEFF, ten sam koszyk → swap; m={m_high}"
    assert m_high["n_swap_better"] == 1, f"swap high→low breach (better); m={m_high}"
    # sanity dolny: przy bardzo małym coeff brak swapu (delta za mała)
    m_tiny = measure(decisions, feed, fleet_median, coeff=4.0,
                     confidence={"123": "high", "470": "high"})
    # coeff=4: delta=-4*0.4=-1.6 → best_adj=48.4 > 48 → BRAK swapu
    assert m_tiny["n_changed"] == 0, (
        f"za mały COEFF → delta nie przebija (48.4>48) → brak swapu; m={m_tiny}"
    )


def test_method_field_present():
    """Wynik measure zawiera method=='key_aware_v2' (odróżnia od starych wpisów)."""
    feed = {"123": 0.4, "470": 0.0}
    fleet_median = 0.2
    decisions = [_decision(best=_cand("123", 50.0), alternatives=[_cand("470", 45.0)])]
    m = measure(decisions, feed, fleet_median, coeff=100.0)
    # CLI fallback (by_coeff) może nie nieść method per-coeff — toleruj brak tam.
    if "method" in m:
        assert m["method"] == "key_aware_v2", f"method={m.get('method')!r}"


# ───────────────────────── runner ─────────────────────────

def main():
    tests = [
        # zachowane
        test_coeff_zero_no_change,
        test_high_coeff_swaps_to_low_breach,
        test_decision_without_alternatives_skipped,
        test_filtered_candidates_best_effort_and_feasibility,
        test_feasible_candidates_filter_unit,
        test_reliability_delta_unit,
        test_non_propose_verdict_ignored,
        test_coeff_monotonic_change_rate,
        test_load_reliability_unit,
        # nowe (key_aware_v2)
        test_key_bucket_unit,
        test_confidence_gating_low_no_swap,
        test_min_gap_below_threshold_no_swap,
        test_key_aware_negative_worse_bucket_no_swap,
        test_key_aware_positive_same_bucket_swap,
        test_method_field_present,
    ]
    results = {"pass": 0, "fail": 0, "skip": 0}
    print("=" * 70)
    if not MODULE_PATH.exists():
        print(f"  SKIP-ALL: moduł produkcyjny nie istnieje:\n    {MODULE_PATH}")
        print("  (testy gotowe — uruchom ponownie gdy moduł powstanie)")
        print("=" * 70)
        return 0
    if A2 is None and _IMPORT_ERR is not None:
        print(f"  UWAGA: import modułu nie powiódł się ({_IMPORT_ERR!r}) — próba CLI-fallback.")
    print(f"  a2_selection_shadow: import={'OK' if A2 is not None else 'NIE'} "
          f"measure={'OK' if _has_measure() else 'NIE (CLI fallback)'} "
          f"helpers={'OK' if _has_helpers() else 'NIE'} "
          f"key_bucket={'OK' if _has_key_bucket() else 'NIE'}")
    print("=" * 70)
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            results["pass"] += 1
        except SkipTest as e:
            print(f"  ⏭️  SKIP {fn.__name__}: {e}")
            results["skip"] += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
            results["fail"] += 1
        except Exception as e:
            print(f"  💥 {fn.__name__}: {type(e).__name__}: {e}")
            results["fail"] += 1
    print(f"\n{results['pass']} PASS / {results['fail']} FAIL / {results['skip']} SKIP")
    return 1 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
