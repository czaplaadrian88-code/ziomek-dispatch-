"""bundle_calib_review — outcome-join + werdykt korpusu BUNDLE-CALIB shadow (Adrian 25.06: przegląd 02.07).

Czyta `bundle_calib_shadow.jsonl` (served vs CALIB pod objektywem O2 = overage + 1.5*czas_late,
ready-anchored, λ=1.5) i odpowiada: czy skalibrowany objektyw dałby LEPSZE bundle niż serwowany
kanon — materialnie (≥20% worków) i BEZ regresji świeżości? Outcome-join: order_ids → REALNE
delivered_at (sla_log), wiek liczony OD GOTOWOŚCI (czas_kuriera z logu) — czy served realnie
naruszał R6 tam gdzie CALIB przewiduje mniej.

Werdykt: GO (warto flipnąć silnik — trójka feasibility+route_simulator+plan_recheck razem,
Załącznik A) / NO-GO (nie warto / regresje) / INCONCLUSIVE (za mało worków — przedłużyć).

Read-only, jednorazowy. Werdykt na Telegram (grupa ziomka). Kryteria: memory
sweep-r6-anchor-pickup-vs-ready-2026-06-25.md. Uruchamiany przez one-shot timer 02.07.
"""
import os
import sys
import json
import statistics
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
CORPUS = f"{STATE_DIR}/bundle_calib_shadow.jsonl"
SLA_LOG = f"{STATE_DIR}/sla_log.jsonl"
R6_MAX_MIN = 35.0
MIN_MULTI = 20            # minimum UNIKALNYCH worków multi-order na pewny werdykt
MATERIAL_PCT = 20.0      # próg materialności (ETAP 5)
REGRESSION_PCT_MAX = 5.0 # max % worków regresji
MATERIAL_O2_MIN = 2.0    # ΔO2 (overage+1.5*czas_late) ≥ tyle min/worek = materialna poprawa
# UWAGA (2026-06-25, o2-consistent fix): objektyw, na którym SHADOW jest skalibrowany =
# O2 = overage + 1.5*czas_late (λ zatwierdzona przez Adriana). CALIB = argmin O2 po WSZYSTKICH
# poprawnych przeplotach (served jest jednym z nich) → CALIB NIGDY nie gorszy na O2 (regres_O2≡0
# by construction; empirycznie 0/16 na 25.06). Stara flaga `bundle_improved` + count-regres liczą
# LICZBĘ zleceń ponad 35 min — sprzeczne z O2 (zaniżają: cid 515 overage 67→30 = flaga False).
# Werdykt = bramka PIERWOTNA na O2 (spójna z objektywem); count-lens (late-klienci) = wtórny +
# jawne pytanie do Adriana gdy się rozjeżdża. Detal: memory bag-resequence-fill-deadtime-candidate.


def _read_jsonl(path):
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return out


def _parse(ts):
    if not ts or ts == "None":
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def _p90(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))
    return round(xs[i], 2)


def _o2_of(m):
    """O2 = overage + 1.5*czas_late (objektyw kalibrowany; ten sam λ co collector)."""
    m = m or {}
    return m.get("overage", 0.0) + 1.5 * m.get("czas_late", 0.0)


def _zkeys_in_corpus(corpus):
    """Wykryj klucze Z obecne w under_z (auto-adaptacja gdy collector zmieni Z_CAPS)."""
    for r in corpus:
        uz = r.get("under_z")
        if isinstance(uz, dict) and uz:
            return sorted(uz.keys(), key=lambda k: float(k))
    return []


def _calib_under_z(multi, differs, zkeys):
    """Kalibracja X/Y/Z (Opcja 3 Adriana, 2026-06-25): dla każdego TWARDEGO capa świeżości Z
    policz ile worków ma Z-zgodny przeplot (max wiek carried ≤ Z) bijący SERVED na O2 i jakiego
    detouru (Y min) wymaga → progi X/Y/Z. Liczone TYLKO po rekordach z polem `under_z`
    (collector dopiero od 25.06 — stare rekordy bez niego pomijane; coverage raportowany).
    feasible = istnieje Z-zgodny przeplot; improved = ten przeplot bije served o ≥MATERIAL_O2_MIN.
    calib_exceeds = surowy CALIB (bez capa) przekracza ten Z = miara freshness-blindness O2."""
    n_multi = max(len(multi), 1)
    have_uz = [r for r in differs if isinstance(r.get("under_z"), dict)]
    out = {"_coverage": len(have_uz),
           "_coverage_pct": round(100 * len(have_uz) / max(len(differs), 1), 1),
           "caps": {}}
    for zk in zkeys:
        z = float(zk)
        feasible = improved = calib_exceeds = 0
        gains, detours = [], []
        for r in have_uz:
            if (r.get("calib_max_carried_age") or 0) > z:
                calib_exceeds += 1
            uz = (r.get("under_z") or {}).get(zk)
            if uz is None:                       # żaden feasible przeplot pod capem Z
                continue
            feasible += 1
            ms = r.get("m_served")
            gain = _o2_of(ms) - _o2_of(uz)       # >0 = Z-zgodny przeplot lepszy niż served
            if gain >= MATERIAL_O2_MIN:
                improved += 1
                gains.append(round(gain, 2))
                sd, ud = (ms or {}).get("drive_min"), uz.get("drive_min")
                if sd is not None and ud is not None:
                    detours.append(round(ud - sd, 2))   # Y: detour (min); <0 = trasa krótsza
        out["caps"][zk] = {
            "feasible": feasible,
            "feasible_pct": round(100 * feasible / n_multi, 1),
            "improved": improved,
            "improved_pct": round(100 * improved / n_multi, 1),
            "med_gain_o2": _med(gains),
            "med_detour_min": _med(detours),
            "p90_detour_min": _p90(detours),
            "calib_exceeds_pct": round(100 * calib_exceeds / max(len(have_uz), 1), 1),
        }
    return out


def _sla_delivered_index():
    """{order_id: delivered_at(datetime)} z sla_log (ostatni wpis/oid) — do outcome-join."""
    idx = {}
    for r in _read_jsonl(SLA_LOG):
        d = _parse(r.get("delivered_at"))
        if d is not None:
            idx[str(r.get("order_id"))] = d
    return idx


def build_report():
    corpus = _read_jsonl(CORPUS)
    # UNIKALNE worki: dedup po (cid, bag_sig) — last-wins (ostatni stan worka)
    uniq = {}
    for r in corpus:
        if r.get("n_orders", 0) >= 2:
            uniq[(r.get("cid"), r.get("bag_sig"))] = r
    multi = list(uniq.values())
    differs = [r for r in multi if r.get("served_seq") != r.get("calib_seq")]
    improved = [r for r in multi if r.get("bundle_improved")]   # legacy flaga (progowa, zaniża)

    def delt(r, key):  # m_served - m_calib (>0 = CALIB lepszy)
        ms, mc = r.get("m_served") or {}, r.get("m_calib") or {}
        a, b = ms.get(key), mc.get(key)
        return (a - b) if (a is not None and b is not None) else None

    def o2(m):  # objektyw kalibrowany O2 = overage + 1.5*czas_late
        return (m or {}).get("overage", 0.0) + 1.5 * (m or {}).get("czas_late", 0.0)

    def d_o2(r):  # served - calib (>=0 by construction; >0 = CALIB lepszy)
        ms, mc = r.get("m_served"), r.get("m_calib")
        return (o2(ms) - o2(mc)) if (ms and mc) else None

    d_overage = [delt(r, "overage") for r in differs]
    d_czas = [delt(r, "czas_late") for r in differs]
    d_r6 = [delt(r, "r6_ready") for r in differs]
    d_finish = [delt(r, "finish_in_min") for r in differs]
    d_obj = [d_o2(r) for r in differs]

    # PIERWOTNE (objektyw O2): improved = ΔO2 materialny; regres = CALIB gorszy na O2 (≈0 by constr.)
    improved_o2 = [r for r in differs if (d_o2(r) or 0) >= MATERIAL_O2_MIN]
    regress_o2 = [r for r in differs if (d_o2(r) or 0) < -0.01]
    # WTÓRNE (count/late-klienci): regres = więcej zleceń ponad 35 LUB overage gorszy >2 min
    regress_count = [r for r in differs
                     if (delt(r, "r6_ready") or 0) < 0 or (delt(r, "overage") or 0) < -2.0]

    # OUTCOME-JOIN: realny wiek OD GOTOWOŚCI (delivered - czas_kuriera[oid]) z sla_log
    sla = _sla_delivered_index()
    joined = 0
    real_served_viol = 0   # ile zleceń realnie >35 min od gotowości (trasa SERWOWANA = rzeczywistość)
    real_total = 0
    for r in differs:
        cks = r.get("czas_kuriera") or {}
        hit = False
        for oid in (r.get("order_ids") or []):
            d = sla.get(str(oid))
            ck = _parse((cks or {}).get(str(oid)))
            if d is not None and ck is not None:
                hit = True
                real_total += 1
                if (d - ck).total_seconds() / 60.0 > R6_MAX_MIN:
                    real_served_viol += 1
        if hit:
            joined += 1

    # KALIBRACJA X/Y/Z (Opcja 3 Adriana) — best przeplot pod twardym capem świeżości carried.
    zkeys = _zkeys_in_corpus(corpus)
    uz_cal = _calib_under_z(multi, differs, zkeys)

    n_multi = len(multi)
    rep = {
        "corpus_rows": len(corpus),
        "multi_uniq": n_multi,
        "differs": len(differs),
        "differs_pct": round(100 * len(differs) / max(n_multi, 1), 1),
        # PIERWOTNE — objektyw O2 (na nim shadow skalibrowany):
        "improved_o2": len(improved_o2),
        "improved_o2_pct": round(100 * len(improved_o2) / max(n_multi, 1), 1),
        "med_d_o2": _med(d_obj),              # >0 = CALIB lepszy na objektywie (≥0 by constr.)
        "regress_o2": len(regress_o2),
        "regress_o2_pct": round(100 * len(regress_o2) / max(len(differs), 1), 1),
        # WTÓRNE — count/late-klienci + składowe:
        "bundle_improved_flag": len(improved),  # legacy progowa (zaniża)
        "bundle_improved_flag_pct": round(100 * len(improved) / max(n_multi, 1), 1),
        "med_d_overage": _med(d_overage),     # >0 = CALIB świeższy (min ponad 35)
        "med_d_czas_late": _med(d_czas),      # >0 = CALIB mniej spóźnia czasówki
        "med_d_r6_ready": _med(d_r6),         # >0 = CALIB mniej zleceń ponad 35
        "med_d_finish": _med(d_finish),       # >0 = CALIB szybciej domyka
        "regress_count": len(regress_count),
        "regress_count_pct": round(100 * len(regress_count) / max(len(differs), 1), 1),
        "real_joined_bags": joined,
        "real_served_viol_pct": round(100 * real_served_viol / max(real_total, 1), 1) if real_total else None,
        # KALIBRACJA X/Y/Z (Opcja 3 — twardy cap świeżości carried):
        "z_keys": zkeys,
        "under_z": uz_cal,
    }
    rep["verdict"], rep["recommendation"] = _verdict(rep)
    return rep


def _verdict(r):
    # DECYZJA ADRIANA 2026-06-25 (Opcja 3): flip = WĄSKA reguła pod TWARDYM capem świeżości
    # carried (Z). Surowy O2 (bez capa) = tylko PUŁAP (freshness-blind) — NIE polityka.
    # Policy-GO bazuje na under_z (Z-zgodne przeploty bijące served), nie na surowym O2.
    if r["multi_uniq"] < MIN_MULTI:
        return ("INCONCLUSIVE", f"Za mało worków multi-order ({r['multi_uniq']}<{MIN_MULTI}) — przedłużyć forward-shadow do uzbierania.")
    impO2 = r["improved_o2_pct"]
    do2 = r["med_d_o2"] or 0
    regO2 = r["regress_o2_pct"]
    uz = r.get("under_z") or {}
    caps = uz.get("caps") or {}
    cov = uz.get("_coverage", 0)
    ceil = (f"PUŁAP O2 (bez capa Z, freshness-blind, NIE polityka): "
            f"improved {impO2}% (ΔO2 med {do2}), regres_O2 {regO2}%")
    cap_tab = "; ".join(
        f"Z≤{zk}: policy-improved {c['improved_pct']}% (med ΔO2 {c['med_gain_o2']}, "
        f"detour med {c['med_detour_min']}/p90 {c['p90_detour_min']} min), "
        f"feasible {c['feasible_pct']}%, CALIB>cap {c['calib_exceeds_pct']}%"
        for zk, c in caps.items()) or "brak danych under_z"
    if not caps or cov < MIN_MULTI:
        return ("INCONCLUSIVE", f"Za mało rekordów z under_z (coverage {cov}<{MIN_MULTI}; collector dopiero od 25.06) — przedłużyć do uzbierania kalibracji X/Y/Z. {ceil}")
    passing = [zk for zk, c in caps.items() if c["improved_pct"] >= MATERIAL_PCT]
    if passing:
        zrec = min(passing, key=lambda k: float(k))   # najmniejszy cap dający materialność = max ochrona carried
        crec = caps[zrec]
        return ("GO", f"OPCJA 3 MATERIALNA pod capem świeżości. Capy z ≥{MATERIAL_PCT:.0f}% policy-improved: {{{', '.join('Z≤' + z for z in passing)}}}. ⭐ Rekom. Z={zrec} (najmniejszy cap dający materialność = max ochrona niesionego; X/Y = detour med {crec['med_detour_min']}/p90 {crec['p90_detour_min']} min). [{cap_tab}]. {ceil}. FLIP = wąska reguła (detour≤X/Y ORAZ carried≤Z) w trójce feasibility+route_simulator+plan_recheck RAZEM (Załącznik A), flaga OFF→shadow→ON, pełna regresja vs baseline + e2e + rollback (PRZYKAZANIE #0 ETAP 1-7). ⚠ NIE flipować surowego O2 (pułap) — łamie carried-first.")
    if impO2 >= MATERIAL_PCT:
        return ("NO-GO", f"Pułap O2 materialny ({impO2}%) ALE wyłącznie kosztem świeżości > każdego capa Z — pod Opcją 3 ŻADEN cap nie daje ≥{MATERIAL_PCT:.0f}% policy-improved. [{cap_tab}]. Lewar głównie wozi carried za długo → NIE flipować pod Opcją 3; rozważ dźwignię fleet-level. {ceil}")
    return ("NO-GO", f"Niematerialne nawet bez capa — Ziomek w większości worków już optymalny. [{cap_tab}]. {ceil}")


def _fmt(r):
    uz = r.get("under_z") or {}
    caps = uz.get("caps") or {}
    L = ["🔬 BUNDLE-CALIB przegląd (objektyw O2=overage+1.5·czas_late, ready-anchor; + kalibracja X/Y/Z Opcji 3)",
         f"Korpus: {r['corpus_rows']} wpisów / {r['multi_uniq']} unikalnych worków multi-order",
         f"CALIB≠served: {r['differs']} ({r['differs_pct']}%)",
         "",
         "① PUŁAP — surowy O2 bez capa Z (freshness-blind, NIE polityka):",
         f"   improved_O2 (ΔO2≥{MATERIAL_O2_MIN:.0f}min): {r['improved_o2']} ({r['improved_o2_pct']}%) · ΔO2 med: {r['med_d_o2']} min · regres_O2: {r['regress_o2']} ({r['regress_o2_pct']}%, ~0 by constr.)",
         "",
         "② SKŁADOWE / count-lens (mediana gdy CALIB≠, >0 = CALIB lepszy):",
         f"   overage Δ: {r['med_d_overage']} min · czasówka Δ: {r['med_d_czas_late']} min · liczba-R6 Δ: {r['med_d_r6_ready']} · domknięcie Δ: {r['med_d_finish']} min",
         f"   flaga-progowa (legacy, zaniża): {r['bundle_improved_flag']} ({r['bundle_improved_flag_pct']}%) · count-regres (więcej zleceń>35): {r['regress_count']} ({r['regress_count_pct']}%)",
         "",
         f"③ KALIBRACJA X/Y/Z (Opcja 3 — best przeplot pod TWARDYM capem świeżości carried; coverage under_z {uz.get('_coverage', 0)}/{r['differs']} = {uz.get('_coverage_pct', 0)}%):"]
    if caps:
        for zk, c in caps.items():
            L.append(f"   Z≤{zk} min: policy-improved {c['improved']} ({c['improved_pct']}%) · med ΔO2 {c['med_gain_o2']} · detour med {c['med_detour_min']}/p90 {c['p90_detour_min']} min · feasible {c['feasible_pct']}% · surowy CALIB>cap {c['calib_exceeds_pct']}%")
    else:
        L.append("   (brak rekordów z under_z — collector dopiero od 25.06; poczekać na napływ do 02.07)")
    L += ["",
          f"Outcome-join (real delivered_at, n={r['real_joined_bags']} worków): served realnie naruszał R6 (od gotowości) w {r['real_served_viol_pct']}% zleceń",
          "",
          f"➤ WERDYKT: {r['verdict']}",
          f"   {r['recommendation']}"]
    return "\n".join(L)


def main():
    rep = build_report()
    msg = _fmt(rep)
    print(msg)
    print("\nJSON:", json.dumps(rep, ensure_ascii=False))
    if "--no-telegram" not in sys.argv:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(msg, source="bundle_calib_review")
            print("\n[telegram] wysłano")
        except Exception as e:
            print(f"\n[telegram] fail: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
