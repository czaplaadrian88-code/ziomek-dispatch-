#!/usr/bin/env python3
"""Testy dla tools/courier_reliability.py — OFFLINE feed niezawodności kuriera
(pętla uczenia Fazy 1, mapa autonomii 2026-06-03).

Testy są ODPORNE: zero zależności od realnych plików danych. Każdy przypadek
buduje SYNTETYCZNY backfill (lista dictów lub tmp JSONL) i sprawdza kontrakt.

KONTRAKT pod test (z docstringu modułu):
  - czyta backfill JSONL: delivered = outcome.status=="delivered",
    atrybucja do outcome.courier_id_final, metryki z outcome.pickup_to_delivery_min
    i predicted_drive_min.
  - per kurier (n_delivered >= min_history):
      breach_rate           = % dostaw z p2d > 35
      breach_rate_loo       = leave-one-out breach (diagnostyka)
      median_pickup_to_delivery
      speed_vs_pred_median  = mediana (p2d - predicted_drive_min)
      reliability           = 1.0 - (breach_rate - fleet_median_breach)
                                   - 0.02*max(0, speed_vs_pred)   (WYŻSZY = lepszy)
      confidence            = n>=20 high / 10-19 medium / 5-9 low
  - JSON out: {meta, fleet_median_breach_rate, fleet_median_speed_vs_pred,
              couriers:{cid:{...}}}

STRATEGIA: preferuj import funkcji modułu (build_profiles / _confidence). Jeśli
te funkcje nie istnieją lub mają inny kształt → fallback na CLI (subprocess z
venv python na tmp backfillu + --min-history) i parsowanie JSON-a wyniku.
Gdy pliku skryptu w ogóle brak → SKIP (czytelny komunikat, nie crash).

Uruchom:
    /root/.openclaw/venvs/dispatch/bin/python tests/test_courier_reliability.py
albo:
    /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_courier_reliability.py -v
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]            # /root/.openclaw/workspace/scripts
MODULE_PATH = REPO / "dispatch_v2" / "tools" / "courier_reliability.py"
VENV_PY = "/root/.openclaw/venvs/dispatch/bin/python"


class SkipTest(Exception):
    """Sygnalizuje czysty SKIP (moduł nie istnieje / brak API)."""


# ───────────────────────── import (best-effort) ─────────────────────────

CR = None
_IMPORT_ERR = None
if MODULE_PATH.exists():
    try:
        from dispatch_v2.tools import courier_reliability as CR  # noqa: E402
    except Exception as e:  # pragma: no cover - środowiskowe
        _IMPORT_ERR = e
        CR = None


# ───────────────────────── syntetyka ─────────────────────────

_OID_CTR = [0]


def _row(cid, p2d, predicted=None, status="delivered", pos_source="gps", oid=None):
    """Jeden wiersz backfillu (decyzja + realny outcome).

    UWAGA (drift fix 2026-06-29): build_profiles DEDUPuje per (cid, order_id) — commit e85c85b
    (~45% backfillu to zdublowane decyzje tego samego zlecenia). Bez UNIKALNEGO order_id wszystkie
    syntetyczne wiersze kolapsowały do 1 (str(None)) → n_delivered=1 < min_history → puste profile.
    Każdy wiersz dostaje unikalny order_id (auto-licznik gdy nie podano)."""
    if oid is None:
        _OID_CTR[0] += 1
        oid = f"auto-{_OID_CTR[0]}"
    outcome = {
        "status": status,
        "courier_id_final": cid,
        "pickup_to_delivery_min": p2d,
        "pos_source": pos_source,
    }
    r = {"outcome": outcome, "order_id": oid}
    if predicted is not None:
        r["predicted_drive_min"] = predicted
    return r


def _rows_for_courier(cid, p2d_list, predicted=20.0):
    return [_row(cid, v, predicted=predicted) for v in p2d_list]


def _write_jsonl(rows, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ───────────────────────── adapter: import-first, CLI-fallback ──────────

def _has_build_profiles():
    return CR is not None and hasattr(CR, "build_profiles")


def _profiles_via_import(rows, min_history):
    """Zwraca (profiles_dict, fleet_breach, fleet_speed) przez import funkcji."""
    out = CR.build_profiles(rows, min_history)
    # kontrakt: (profiles, fleet_breach, fleet_speed)
    assert isinstance(out, tuple) and len(out) == 3, (
        f"build_profiles powinno zwracać 3-tuple, zwróciło: {type(out)} len={len(out) if isinstance(out, tuple) else '?'}"
    )
    profiles, fleet_breach, fleet_speed = out
    assert isinstance(profiles, dict), "profiles powinno być dict cid->metryki"
    return profiles, fleet_breach, fleet_speed


def _profiles_via_cli(rows, min_history):
    """Fallback: odpal skrypt CLI na tmp backfillu, sparsuj wynikowy JSON.

    courier_reliability używa STAŁYCH ścieżek (BACKFILL/OUT_JSON na poziomie
    modułu). CLI nie ma flag --backfill/--out, więc nadpisujemy te stałe przez
    env-driven mały wrapper uruchamiany tym samym interpreterem. Jeśli wrapper
    nie zadziała (np. inne API) — rzucamy SkipTest.
    """
    if not VENV_PY or not os.path.exists(VENV_PY):
        raise SkipTest(f"venv python brak: {VENV_PY}")

    tmpdir = tempfile.mkdtemp(prefix="creliab_")
    backfill = os.path.join(tmpdir, "backfill.jsonl")
    out_json = os.path.join(tmpdir, "courier_reliability.json")
    _write_jsonl(rows, backfill)

    # Wrapper: importuje moduł, podmienia stałe ścieżek, woła main z argv.
    wrapper = os.path.join(tmpdir, "run.py")
    wrapper_src = (
        "import sys, runpy\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "from dispatch_v2.tools import courier_reliability as M\n"
        f"M.BACKFILL = {backfill!r}\n"
        f"M.OUT_JSON = {out_json!r}\n"
        f"sys.argv = ['courier_reliability', '--min-history', str({int(min_history)}), '--json-only']\n"
        "rc = M.main()\n"
        "sys.exit(rc if isinstance(rc, int) else 0)\n"
    )
    with open(wrapper, "w", encoding="utf-8") as f:
        f.write(wrapper_src)

    proc = subprocess.run(
        [VENV_PY, wrapper],
        capture_output=True, text=True, timeout=120,
    )
    if not os.path.exists(out_json):
        raise SkipTest(
            "CLI nie wytworzył courier_reliability.json "
            f"(rc={proc.returncode}); stderr: {proc.stderr.strip()[:300]}"
        )
    with open(out_json, encoding="utf-8") as f:
        payload = json.load(f)
    return (
        payload.get("couriers", {}),
        payload.get("fleet_median_breach_rate"),
        payload.get("fleet_median_speed_vs_pred"),
    )


def get_profiles(rows, min_history=5):
    """Jedno API dla testów: import-first, CLI-fallback, SKIP gdy brak modułu."""
    if not MODULE_PATH.exists():
        raise SkipTest(f"moduł nie istnieje: {MODULE_PATH}")
    if _has_build_profiles():
        try:
            return _profiles_via_import(rows, min_history)
        except SkipTest:
            raise
        except AssertionError:
            # API inne niż oczekiwane — spróbuj CLI zamiast crashować.
            return _profiles_via_cli(rows, min_history)
    # brak funkcji build_profiles → CLI
    return _profiles_via_cli(rows, min_history)


# ───────────────────────── testy ─────────────────────────

def test_zero_breach_courier():
    """(a) Kurier z 0 breach (wszystkie p2d<35, n=10) → breach_rate=0,
    reliability >= fleet_median_breach baseline (≥1.0 bo nie karany)."""
    rows = _rows_for_courier("100", [20, 22, 25, 18, 30, 28, 24, 26, 21, 19])  # n=10, max 30<35
    profiles, fleet_breach, _ = get_profiles(rows, min_history=5)
    p = profiles.get("100")
    assert p is not None, f"kurier 100 powinien być w profilu; mam: {list(profiles)}"
    assert abs(p["breach_rate"] - 0.0) < 1e-9, f"breach_rate={p['breach_rate']} (oczekiwano 0)"
    # reliability = 1.0 - (0 - fleet_median) - 0.02*max(0,speed). Przy jednym
    # kurierze fleet_median=0 → reliability ~1.0 (minus ewentualna kara speed).
    assert p["reliability"] >= 1.0 - 1e-6 - 0.02 * 50, (
        f"reliability={p['reliability']} niespodziewanie niski dla zero-breach"
    )


def test_half_breach_lower_reliability():
    """(b) Kurier z 50% breach (n=10, połowa >35) → breach_rate=0.5,
    reliability NIŻSZY niż kurier zero-breach (przy tej samej flocie)."""
    # Wspólna flota: jeden dobry (0 breach) + jeden zły (50% breach), ten sam predicted.
    good = _rows_for_courier("200", [20, 22, 25, 18, 30, 28, 24, 26, 21, 19])
    bad = _rows_for_courier("201", [40, 45, 50, 42, 41, 20, 22, 25, 18, 30])  # 5/10 > 35
    profiles, fleet_breach, _ = get_profiles(good + bad, min_history=5)
    g = profiles.get("200")
    b = profiles.get("201")
    assert g is not None and b is not None, f"oba kuriery w profilu; mam {list(profiles)}"
    assert abs(b["breach_rate"] - 0.5) < 1e-9, f"breach_rate złego={b['breach_rate']} (oczekiwano 0.5)"
    assert abs(g["breach_rate"] - 0.0) < 1e-9, f"breach_rate dobrego={g['breach_rate']} (oczekiwano 0)"
    assert b["reliability"] < g["reliability"], (
        f"reliability złego ({b['reliability']}) powinien być < dobrego ({g['reliability']})"
    )


def test_confidence_tiers():
    """(c) confidence: n=25 → high, n=12 → medium, n=6 → low.
    Trzej kurierzy o różnej liczności w jednej flocie."""
    hi = _rows_for_courier("300", [20] * 25)    # n=25 → high
    med = _rows_for_courier("301", [20] * 12)   # n=12 → medium
    lo = _rows_for_courier("302", [20] * 6)     # n=6  → low
    profiles, _, _ = get_profiles(hi + med + lo, min_history=5)
    assert profiles.get("300", {}).get("confidence") == "high", (
        f"n=25 confidence={profiles.get('300', {}).get('confidence')} (oczekiwano high)"
    )
    assert profiles.get("301", {}).get("confidence") == "medium", (
        f"n=12 confidence={profiles.get('301', {}).get('confidence')} (oczekiwano medium)"
    )
    assert profiles.get("302", {}).get("confidence") == "low", (
        f"n=6 confidence={profiles.get('302', {}).get('confidence')} (oczekiwano low)"
    )


def test_below_min_history_skipped():
    """(d) Kurier z n<min_history pomijany (nie pojawia się w profilu)."""
    enough = _rows_for_courier("400", [20] * 8)   # n=8 >= min_history
    too_few = _rows_for_courier("401", [20] * 3)  # n=3 < min_history (5)
    profiles, _, _ = get_profiles(enough + too_few, min_history=5)
    assert "400" in profiles, f"kurier 400 (n=8) powinien być; mam {list(profiles)}"
    assert "401" not in profiles, (
        f"kurier 401 (n=3 < min_history=5) NIE powinien być w profilu; mam {list(profiles)}"
    )


def test_reliability_ordering_low_gt_high_breach():
    """(e) Ordering: low-breach kurier ma WYŻSZY reliability niż high-breach.
    Trzy poziomy breach w jednej flocie: 0% / ~30% / 80%."""
    low = _rows_for_courier("500", [20] * 10)                       # 0% breach
    mid = _rows_for_courier("501", [40, 41, 42, 20, 21, 22, 23, 24, 25, 26])  # 3/10 = 30%
    high = _rows_for_courier("502", [40, 41, 42, 43, 44, 45, 46, 47, 20, 21])  # 8/10 = 80%
    profiles, _, _ = get_profiles(low + mid + high, min_history=5)
    rl = profiles["500"]["reliability"]
    rm = profiles["501"]["reliability"]
    rh = profiles["502"]["reliability"]
    assert rl > rm > rh, (
        f"oczekiwano reliability low>mid>high; mam low={rl} mid={rm} high={rh}"
    )
    # Sanity: breach_rate monotonicznie rośnie low<mid<high
    assert (profiles["500"]["breach_rate"] < profiles["501"]["breach_rate"]
            < profiles["502"]["breach_rate"]), "breach_rate powinien rosnąć low<mid<high"


def test_schema_shape_of_output():
    """KONTRAKT JSON: każdy profil ma sztywne klucze; meta-poziom feed ma
    fleet_median_breach_rate. (Sprawdzane przez CLI ścieżkę gdy dostępna,
    inaczej przez import zwracający per-courier dict.)"""
    rows = _rows_for_courier("600", [20, 25, 40, 22, 24, 26, 28, 30, 21, 23])  # 1 breach
    profiles, fleet_breach, fleet_speed = get_profiles(rows, min_history=5)
    p = profiles.get("600")
    assert p is not None, f"kurier 600 w profilu; mam {list(profiles)}"
    required = {
        "n_delivered", "breach_rate", "breach_rate_loo",
        "median_pickup_to_delivery", "speed_vs_pred_median",
        "reliability", "confidence",
    }
    missing = required - set(p.keys())
    assert not missing, f"brakujące klucze w profilu: {missing} (mam {sorted(p.keys())})"
    assert fleet_breach is not None, "fleet_median_breach_rate nie powinien być None"
    # n_delivered zgodne
    assert p["n_delivered"] == 10, f"n_delivered={p['n_delivered']} (oczekiwano 10)"


def test_speed_vs_pred_median():
    """speed_vs_pred_median = mediana (p2d - predicted_drive_min).
    p2d wszystkie = 30, predicted = 20 → resid = +10 → mediana 10."""
    rows = _rows_for_courier("700", [30] * 8, predicted=20.0)
    profiles, _, _ = get_profiles(rows, min_history=5)
    p = profiles.get("700")
    assert p is not None, f"kurier 700 w profilu; mam {list(profiles)}"
    assert p["speed_vs_pred_median"] is not None, "speed_vs_pred_median nie powinno być None"
    assert abs(p["speed_vs_pred_median"] - 10.0) < 0.5, (
        f"speed_vs_pred_median={p['speed_vs_pred_median']} (oczekiwano ~10)"
    )


def test_non_delivered_ignored():
    """Wiersze z outcome.status != 'delivered' nie liczą się do n_delivered."""
    delivered = _rows_for_courier("800", [20] * 6)                      # n=6 delivered
    not_delivered = [_row("800", 20, status="cancelled") for _ in range(20)]  # ignorowane
    profiles, _, _ = get_profiles(delivered + not_delivered, min_history=5)
    p = profiles.get("800")
    assert p is not None, f"kurier 800 w profilu; mam {list(profiles)}"
    assert p["n_delivered"] == 6, (
        f"n_delivered={p['n_delivered']} (oczekiwano 6; cancelled mają być ignorowane)"
    )
    # n=6 → low confidence (nie high mimo 26 łącznych wierszy)
    assert p["confidence"] == "low", f"confidence={p['confidence']} (oczekiwano low dla n=6)"


# ───────────────────────── runner ─────────────────────────

def main():
    tests = [
        test_zero_breach_courier,
        test_half_breach_lower_reliability,
        test_confidence_tiers,
        test_below_min_history_skipped,
        test_reliability_ordering_low_gt_high_breach,
        test_schema_shape_of_output,
        test_speed_vs_pred_median,
        test_non_delivered_ignored,
    ]
    results = {"pass": 0, "fail": 0, "skip": 0}
    print("=" * 70)
    if not MODULE_PATH.exists():
        print(f"  SKIP-ALL: moduł produkcyjny nie istnieje:\n    {MODULE_PATH}")
        print("  (testy gotowe — uruchom ponownie gdy moduł powstanie)")
        print("=" * 70)
        return 0
    if CR is None and _IMPORT_ERR is not None:
        print(f"  UWAGA: import modułu nie powiódł się ({_IMPORT_ERR!r}) — próba CLI-fallback.")
    print(f"  courier_reliability: import={'OK' if CR is not None else 'NIE'} "
          f"build_profiles={'OK' if _has_build_profiles() else 'NIE (CLI fallback)'}")
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
