#!/usr/bin/env python3
"""
measure_nogps_shadow.py — READ-ONLY pre-stage pomiar dla kandydata
ENABLE_NO_GPS_NEUTRAL_SCORE_DIST (commit 88acde3, worktree wt-nogps-pkgroot,
NIE zmergowany/NIE zdeployowany).

Bug (memory ziomek-nogps-center-score-bug-2026-07-19): kurier bez realnej
pozycji (pos_source w POSITION_UNKNOWN_SOURCES: no_gps/pre_shift/none/pin/
post_shift_start_synthetic/working_override_synthetic) jest planowany w
fikcyjnym punkcie BIALYSTOK_CENTER. Ta fikcja zasilała SCORE (nie tylko
display) -> no-GPS 24.8% puli kandydatow ale 50.5% zwyciezcow propozycji
(16-19.07, 835 decyzji), mixed-pool head-to-head win 66%, cid 179+413 = 50%
wszystkich propozycji #1.

Fix (eod_drafts/2026-07-19/NOGPS_NEUTRAL_SCORE_CANDIDATE.md): neutralizuje
s_dystans/score kandydatow o nieznanej pozycji MEDIANA road_km kandydatow
o pozycji znanej. Flaga domyslnie OFF; SHADOW ZAWSZE emituje telemetrie
bonus_nogps_neutral_{raw_km,km,dist_delta,applied} per kandydat (prefix
bonus_ => auto-serializacja L1.1 do shadow_decisions.jsonl LOCATION A+B),
niezaleznie od stanu flagi. Apply do c.score/score.components dzieje sie
TYLKO gdy ENABLE_NO_GPS_NEUTRAL_SCORE_DIST=true.

Ten skrypt NIE modyfikuje zadnych plikow poza wlasnym katalogiem wyjsciowym
(--json-out). Czyta WYLACZNIE logs/shadow_decisions.jsonl (i ew. rotowane
pliki .1/.2...) przekazane przez --input.

Tryby:
  1) Pojedyncze okno: --since/--until (domyslnie caly plik).
  2) Porownanie dwoch okien (PRZED vs PO deployu kandydata w kodzie, flaga
     nadal OFF): --window-a START,END --window-b START,END -> delta-tabela.

Co liczy:
  - winner-share / pool-share per cid (domyslnie 179,413) + zbiorczo dla
    grupy "pozycja nieznana" (POSITION_UNKNOWN_SOURCES, kanoniczna klasyfikacja
    F-3 z courier_resolver.py:842-852) ORAZ waski widok "no_gps only" (zgodny
    z liczbami z memory bug-notu, dla porownywalnosci).
  - mixed-pool head-to-head win-rate (pula ma jednoczesnie known+unknown).
  - koncentracja top-2 / top-N cid wsrod zwyciezcow.
  - rozklad km_to_pickup (DISPLAY, nie zawsze = km ktore zasililo score dla
    no_gps przed fixem - patrz UWAGA w sekcji raportu) dla known vs unknown.
  - statystyki bonus_nogps_neutral_* GDY OBECNE w oknie (GRACEFUL: dane
    historyczne sprzed deployu kandydata ich nie maja - jawny komunikat).
  - kontrfaktyczny re-sort ON vs OZ (score +/- bonus_nogps_neutral_dist_delta
    wg bonus_nogps_neutral_applied) -> would_flip_winner, winner-share ON.
  - regresja-guardy: KOORD-rate, "cisza" (_AP_KOORD_SILENCE_PREFIXES z
    shadow_dispatcher.py:1099) rate, best_effort rate, mediana km zwyciezcow.
  - wariancja historyczna winner-share w oknach czasowych (bucket_hours) jako
    baza do progow GO/NO-GO (patrz GO_NO_GO.md w tym katalogu).

Przyklady:
  python3 measure_nogps_shadow.py
  python3 measure_nogps_shadow.py --since 2026-07-18T00:00:00 --until 2026-07-20T00:00:00
  python3 measure_nogps_shadow.py --window-a 2026-07-16T00:00:00,2026-07-18T00:00:00 \\
                                   --window-b 2026-07-18T00:00:00,2026-07-20T00:00:00
  python3 measure_nogps_shadow.py --input a.jsonl b.jsonl --json-out report.json
"""
import argparse
import collections
import json
import math
import statistics
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Kanoniczne stale skopiowane 1:1 z zywego kodu (READ-ONLY probe, zero
# importu z repo zeby nie zalezec od sciezek/venv Ziomka):
#   courier_resolver.py:842-852  POSITION_UNKNOWN_SOURCES + is_position_known
#   shadow_dispatcher.py:1099    _AP_KOORD_SILENCE_PREFIXES
# Jesli te stale zmienia sie w repo, zsynchronizuj tutaj recznie (to jest
# offline analytics tool, nie konsument runtime).
# ---------------------------------------------------------------------------
POSITION_UNKNOWN_SOURCES = frozenset({
    "no_gps", "pre_shift", "none", "pin",
    "post_shift_start_synthetic", "working_override_synthetic",
})
SILENCE_PREFIXES = (
    "best_effort_r6_breach_v2", "best_effort_r6_breach",
    "all_candidates_low_score", "best_effort_low_score", "no_solo_candidates",
)
DEFAULT_INPUT = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DEFAULT_TARGET_CIDS = ["179", "413"]
MIN_BUCKET_N = 20  # bucket czasowy z mniejsza liczba decyzji = zbyt szumny, pomijany w wariancji
# Empirycznie potwierdzone (probe 19.07): kandydaci z hard-cap/would_hard_cap
# dostaja score sentinel ~-1e9 (feasibility=MAYBE, np. cid=531 pos_source=gps,
# score=-1000000017.1) zeby NIGDY nie wygrac, ale wciaz sa serializowani do
# alternatives dla widocznosci shadow. To NIE jest realny wynik scoringu -
# psuje arytmetyke (roznice/srednie). Wykluczamy z metryk opartych na
# WARTOSCI score (margin); winner-share/argmax NIE wymaga filtra (sentinel
# przegrywa z definicji, max() go naturalnie odrzuca).
SENTINEL_SCORE_ABS_THRESHOLD = 1_000_000.0


def is_position_known(pos_source):
    """Mirror 1:1 courier_resolver.py:848-852 (jedyne zrodlo klasyfikacji F-3).
    UWAGA: to jest klasyfikacja pos_source (F-3), NIE definicja "donora
    mediany" silnika - patrz _is_engine_donor nizej, to DWIE ROZNE rzeczy
    (korekta CTO/nogps-v2 19.07 wieczor, patrz komentarz przy DONOR_*)."""
    if pos_source is None:
        return False
    return str(pos_source) not in POSITION_UNKNOWN_SOURCES


# --- Donor-mediany dla walidacji "DONOR-FILTER" (sekcja 5b GO_NO_GO.md) ----
# KANONICZNA definicja donora WG SILNIKA v2 (dispatch_pipeline.py:776-786,
# commit 15ecc79, `_nogps_neutral_score_pass`):
#   NOT metrics.road_km_from_synthetic_pos AND feasibility_verdict=='MAYBE'
#   AND km_to_pickup liczbowy.
# TO NIE JEST TOZSAME z `is_position_known(pos_source) AND MAYBE` - moja
# pierwsza (bledna) wersja tego narzedzia. Rozjazd w 2 klasach brzegowych
# (potwierdzone przez nogps-v2 w eod_drafts/2026-07-19/
# NOGPS_NEUTRAL_SCORE_CANDIDATE.md sekcja "ROZBIEZNOSC WZOROW", korekta
# przekazana przez team-lead 19.07 wieczor):
#   1. no_gps/pre_shift Z KOTWICA (anchor/bag-tail): road_km REALNY
#      (synth=False) -> SILNIK: donor. is_position_known("no_gps")=False ->
#      MOJA STARA WERSJA: bledne wykluczenie. DODATKOWO: petla display F1.7
#      NADPISUJE ich km_to_pickup w LOGU (no_gps->fleet_avg/mediana,
#      pre_shift->None) PO passie mediany - wiec nawet WIEDZAC ze sa
#      donorami, ich PRAWDZIWY km jest NIEODTWARZALNY z samego logu. Pula z
#      takim kandydatem = wylaczona z twardego gate'u, liczona osobno jako
#      "niemierzalna bezposrednio" (coverage metric).
#   2. post_wave PO PRZEMIANOWANIU (F2.1c rename, ale zrodlowa pozycja byla
#      Unknown, road z centrum): synth=True -> SILNIK: NIE-donor.
#      is_position_known("post_wave")=True -> MOJA STARA WERSJA: bledne
#      wliczenie. Naprawione: uzywamy `synth` wprost, nie pos_source.
RECONSTRUCTABLE_KM_POS_SOURCES_EXCLUDE = frozenset({"no_gps", "pre_shift"})


def _is_engine_donor(c):
    """Czy kandydat KWALIFIKUJE SIE jako donor mediany wg SILNIKA (nie wg
    pos_source-owej klasyfikacji F-3). `synth` musi byc jawnie False -
    None/brak pola traktujemy jako NIE-donor (konserwatywnie; nogps-v2
    potwierdzil ze pole serializuje sie zawsze, wiec None powinno byc
    rzadkie/zerowe w realnych danych)."""
    if c["synth"] is not False:
        return False
    return c["feasibility"] == "MAYBE" and isinstance(c["km"], (int, float))


def _donor_km_reconstructable(c):
    """Czy logged km_to_pickup TEGO kandydata jest wciaz jego PRAWDZIWA
    wartoscia donora, a nie nadpisana przez petle display F1.7 PO passie
    mediany (klasa brzegowa 1 wyzej)."""
    return c["pos_source"] not in RECONSTRUCTABLE_KM_POS_SOURCES_EXCLUDE


def parse_ts(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def parse_window_arg(s, name):
    """'ISO_SINCE,ISO_UNTIL' -> (datetime, datetime); puste pole = otwarte."""
    if s is None:
        return None
    parts = s.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"{name}: oczekiwano 'ISO_SINCE,ISO_UNTIL' (jedna strona moze byc pusta), dostalem: {s!r}")
    since_s, until_s = (p.strip() for p in parts)
    since = parse_ts(since_s) if since_s else None
    until = parse_ts(until_s) if until_s else None
    if since_s and since is None:
        raise argparse.ArgumentTypeError(f"{name}: niepoprawny ISO8601 'since': {since_s!r}")
    if until_s and until is None:
        raise argparse.ArgumentTypeError(f"{name}: niepoprawny ISO8601 'until': {until_s!r}")
    return (since, until)


def parse_single_ts_arg(s):
    if s is None:
        return None
    dt = parse_ts(s)
    if dt is None:
        raise argparse.ArgumentTypeError(f"niepoprawny ISO8601: {s!r}")
    return dt


# ---------------------------------------------------------------------------
# Wczytywanie (streaming, lekki summary per decyzje - NIE trzymamy calego
# surowego JSON w pamieci, kazdy rekord ma setki pol / ~100KB na linie).
# ---------------------------------------------------------------------------

def _cand_view(c):
    if not isinstance(c, dict):
        return None
    cid = c.get("courier_id")
    return {
        "cid": str(cid) if cid is not None else None,
        "name": c.get("name"),
        "pos_source": c.get("pos_source"),
        "feasibility": c.get("feasibility"),  # = c.feasibility_verdict w silniku (shadow_dispatcher.py:297/729)
        # road_km_from_synthetic_pos: KANONICZNE zrodlo "czy km tego kandydata
        # pochodzi z fikcji" wg silnika (core/candidates.py, dispatch_pipeline.py
        # _nogps_neutral_score_pass donor-check, commit 15ecc79). NIE mylic z
        # is_position_known(pos_source) - to DWIE ROZNE rzeczy w 2 klasach
        # brzegowych (kotwica przy Unknown-source; post_wave rename) - korekta
        # CTO/nogps-v2 19.07 wieczor po mojej pierwszej (blednej) wersji gate'u.
        "synth": c.get("road_km_from_synthetic_pos"),
        "score": c.get("score") if isinstance(c.get("score"), (int, float)) else None,
        "km": c.get("km_to_pickup") if isinstance(c.get("km_to_pickup"), (int, float)) else None,
        "best_effort": bool(c.get("best_effort")),
        # telemetria kandydata (moze byc nieobecna w danych sprzed deployu):
        "bonus_raw_km": c.get("bonus_nogps_neutral_raw_km"),
        "bonus_neutral_km": c.get("bonus_nogps_neutral_km"),
        "bonus_delta": c.get("bonus_nogps_neutral_dist_delta"),
        "bonus_applied": c.get("bonus_nogps_neutral_applied"),  # True/False/None(nieobecne)
    }


def build_summary(d):
    ts = parse_ts(d.get("ts"))
    best_raw = d.get("best") if isinstance(d.get("best"), dict) else None
    alts_raw = d.get("alternatives")
    alts = [a for a in alts_raw if isinstance(a, dict)] if isinstance(alts_raw, list) else []
    pool_raw = ([best_raw] if best_raw else []) + alts
    pool = [c for c in (_cand_view(c) for c in pool_raw) if c is not None]
    return {
        "ts": ts,
        "order_id": d.get("order_id"),
        "verdict": d.get("verdict"),
        "reason": d.get("reason") or "",
        "pool_feasible_count": d.get("pool_feasible_count"),
        "pool_total_count": d.get("pool_total_count"),
        "n_serialized": len(pool),
        "best": pool[0] if pool else None,
        "pool": pool,  # zawiera best pod indeksem 0
    }


def load_summaries(paths, since=None, until=None):
    """Zwraca (list[summary], counters). READ-ONLY, jedno przejscie na plik."""
    out = []
    counters = collections.Counter()
    for path in paths:
        try:
            fh = open(path, "r", errors="replace")
        except OSError as e:
            print(f"UWAGA: nie moge otworzyc {path}: {e}", file=sys.stderr)
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                counters["lines_total"] += 1
                try:
                    d = json.loads(line)
                except Exception:
                    counters["lines_parse_error"] += 1
                    continue
                counters["lines_parsed"] += 1
                ts = parse_ts(d.get("ts"))
                if since is not None and (ts is None or ts < since):
                    continue
                if until is not None and (ts is None or ts >= until):
                    continue
                counters["lines_in_window"] += 1
                out.append(build_summary(d))
    out.sort(key=lambda s: (s["ts"] is None, s["ts"]))
    return out, counters


# ---------------------------------------------------------------------------
# Statystyki pomocnicze
# ---------------------------------------------------------------------------

def stat(values):
    a = sorted(x for x in values if isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x)))
    if not a:
        return {"n": 0}
    n = len(a)
    return {
        "n": n,
        "min": round(a[0], 3),
        "p50": round(statistics.median(a), 3),
        "mean": round(statistics.mean(a), 3),
        "p90": round(a[int(0.9 * (n - 1))], 3),
        "max": round(a[-1], 3),
        "stdev": round(statistics.pstdev(a), 3) if n > 1 else 0.0,
    }


def stat_str(s):
    if s.get("n", 0) == 0:
        return "n=0"
    return (f"n={s['n']} min={s['min']} p50={s['p50']} mean={s['mean']} "
            f"p90={s['p90']} max={s['max']} stdev={s['stdev']}")


# ---------------------------------------------------------------------------
# Rdzen metryk dla jednego okna
# ---------------------------------------------------------------------------

def compute_window_metrics(summaries, target_cids):
    decisions = summaries  # wszystkie rekordy w oknie (KOORD tez, best moze byc None)
    with_best = [d for d in decisions if d["best"] is not None]
    n_total = len(decisions)
    n_with_best = len(with_best)

    m = {"n_total_records": n_total, "n_decisions_with_best": n_with_best}
    if n_total == 0:
        m["empty"] = True
        return m

    # --- pokrycie serializacji alternatives vs pool_feasible_count --------
    trunc = 0
    trunc_checked = 0
    for d in decisions:
        pfc = d.get("pool_feasible_count")
        if isinstance(pfc, (int, float)):
            trunc_checked += 1
            if d["n_serialized"] < pfc:
                trunc += 1
    m["pool_serialization_truncated_rate"] = round(trunc / trunc_checked, 4) if trunc_checked else None
    m["pool_serialization_checked_n"] = trunc_checked

    # --- winner-share vs pool-share: grupa "pozycja nieznana" (kanon F-3) -
    def is_unknown_view(cv):
        return not is_position_known(cv["pos_source"]) if cv else False

    total_pool_slots = sum(len(d["pool"]) for d in decisions)
    unknown_pool_slots = sum(1 for d in decisions for c in d["pool"] if is_unknown_view(c))
    unknown_winners = sum(1 for d in with_best if is_unknown_view(d["best"]))

    m["pool_share_unknown"] = round(unknown_pool_slots / total_pool_slots, 4) if total_pool_slots else None
    m["winner_share_unknown"] = round(unknown_winners / n_with_best, 4) if n_with_best else None
    m["total_pool_slots"] = total_pool_slots
    m["unknown_pool_slots"] = unknown_pool_slots
    m["unknown_winners"] = unknown_winners

    # --- waski widok "no_gps only" (zgodny z liczbami z memory bug-notu) --
    def is_nogps_only(cv):
        return cv is not None and cv["pos_source"] == "no_gps"

    nogps_pool_slots = sum(1 for d in decisions for c in d["pool"] if is_nogps_only(c))
    nogps_winners = sum(1 for d in with_best if is_nogps_only(d["best"]))
    m["pool_share_nogps_only"] = round(nogps_pool_slots / total_pool_slots, 4) if total_pool_slots else None
    m["winner_share_nogps_only"] = round(nogps_winners / n_with_best, 4) if n_with_best else None

    # --- mixed-pool head-to-head -------------------------------------------
    mixed_pools = 0
    unknown_wins_mixed = 0
    for d in decisions:
        flags = [is_position_known(c["pos_source"]) for c in d["pool"]]
        if True in flags and False in flags:
            mixed_pools += 1
            if d["best"] is not None and not is_position_known(d["best"]["pos_source"]):
                unknown_wins_mixed += 1
    m["mixed_pools_n"] = mixed_pools
    m["mixed_pool_unknown_win_rate"] = round(unknown_wins_mixed / mixed_pools, 4) if mixed_pools else None

    # --- koncentracja top-2 / target cids ----------------------------------
    winner_counter = collections.Counter(
        d["best"]["cid"] for d in with_best if d["best"]["cid"] is not None)
    m["winner_counter_top12"] = winner_counter.most_common(12)
    top2 = sum(c for _, c in winner_counter.most_common(2))
    m["top2_concentration"] = round(top2 / n_with_best, 4) if n_with_best else None
    target_wins = sum(winner_counter.get(cid, 0) for cid in target_cids)
    m["target_cids"] = list(target_cids)
    m["target_cids_winners_n"] = target_wins
    m["target_cids_winner_share"] = round(target_wins / n_with_best, 4) if n_with_best else None
    target_pool = sum(1 for d in decisions for c in d["pool"] if c["cid"] in target_cids)
    m["target_cids_pool_share"] = round(target_pool / total_pool_slots, 4) if total_pool_slots else None
    m["per_cid_winner_pool"] = {}
    for cid in target_cids:
        w = winner_counter.get(cid, 0)
        p = sum(1 for d in decisions for c in d["pool"] if c["cid"] == cid)
        m["per_cid_winner_pool"][cid] = {
            "winners": w,
            "winner_share": round(w / n_with_best, 4) if n_with_best else None,
            "pool_slots": p,
            "pool_share": round(p / total_pool_slots, 4) if total_pool_slots else None,
        }

    # --- post_wave residual (CTO 19.07: "post_wave-dziura REALNA ale
    #     MARGINALNA" - Sol REJECT pkt 4 potwierdza mechanizm: pos_source
    #     moze zostac przemianowany na "post_wave" (poza POSITION_UNKNOWN_
    #     SOURCES, wiec pass go NIE neutralizuje) mimo ze road_km bywa dalej
    #     syntetyczny. post_wave wymaga NIEPUSTEGO bagu z konstrukcji ->
    #     strukturalnie NIE moze wchlonac glownego wzorca (ktory jest
    #     no_gps+PUSTY worek). To jest niezalezna od telemetrii kandydata
    #     obserwacja - dziala na dzisiejszych danych (pos_source="post_wave"
    #     juz istnieje w logu). REKOMENDACJA CTO: swiadomy wyjatek F2.1c,
    #     zostaje + monitoring w 48h cieniu (nie blokuje flipa, ale pilnuj
    #     ze nie rosnie). -------------------------------------------------
    post_wave_winners_all = sum(1 for d in with_best if d["best"]["pos_source"] == "post_wave")
    post_wave_winners_target = sum(
        1 for d in with_best if d["best"]["pos_source"] == "post_wave" and d["best"]["cid"] in target_cids)
    m["post_wave_winners_all"] = post_wave_winners_all
    m["post_wave_winners_all_share"] = round(post_wave_winners_all / n_with_best, 4) if n_with_best else None
    m["post_wave_winners_target_cids"] = post_wave_winners_target
    m["post_wave_share_of_target_wins"] = (
        round(post_wave_winners_target / target_wins, 4) if target_wins else None)
    m["post_wave_share_of_target_plus_nogps_wins"] = None
    nogps_target_wins = sum(
        1 for d in with_best if d["best"]["pos_source"] == "no_gps" and d["best"]["cid"] in target_cids)
    denom = nogps_target_wins + post_wave_winners_target
    if denom:
        m["post_wave_share_of_target_plus_nogps_wins"] = round(post_wave_winners_target / denom, 4)

    # --- km_to_pickup (DISPLAY) known vs unknown ---------------------------
    km_unknown = [c["km"] for d in decisions for c in d["pool"] if is_unknown_view(c) and c["km"] is not None]
    km_known = [c["km"] for d in decisions for c in d["pool"] if not is_unknown_view(c) and c["km"] is not None]
    km_winner_all = [d["best"]["km"] for d in with_best if d["best"]["km"] is not None]
    m["km_display_unknown_pool"] = stat(km_unknown)
    m["km_display_known_pool"] = stat(km_known)
    m["km_display_winner_all"] = stat(km_winner_all)

    # --- score margin best vs alt0 (alt list zakladana score-sorted, jak
    #     w analyze_179.py/179b.py). Wyklucza pary dotkniete sentinel score
    #     (hard-cap ~-1e9) - patrz SENTINEL_SCORE_ABS_THRESHOLD wyzej. -------
    margins = []
    margins_sentinel_skipped = 0
    for d in decisions:
        if len(d["pool"]) >= 2:
            b, a0 = d["pool"][0], d["pool"][1]
            if b["score"] is not None and a0["score"] is not None:
                if (abs(b["score"]) >= SENTINEL_SCORE_ABS_THRESHOLD
                        or abs(a0["score"]) >= SENTINEL_SCORE_ABS_THRESHOLD):
                    margins_sentinel_skipped += 1
                    continue
                margins.append(b["score"] - a0["score"])
    m["score_margin_best_vs_alt0"] = stat(margins)
    m["score_margin_sentinel_skipped"] = margins_sentinel_skipped

    # --- regresja-guardy -----------------------------------------------------
    koord_n = sum(1 for d in decisions if d["verdict"] == "KOORD")
    m["koord_rate"] = round(koord_n / n_total, 4)
    m["koord_n"] = koord_n
    silence_n = sum(1 for d in decisions if any(d["reason"].startswith(p) for p in SILENCE_PREFIXES))
    m["silence_rate"] = round(silence_n / n_total, 4)
    m["silence_n"] = silence_n
    best_effort_n = sum(1 for d in with_best if d["best"]["best_effort"])
    m["best_effort_rate"] = round(best_effort_n / n_with_best, 4) if n_with_best else None

    # --- telemetria kandydata bonus_nogps_neutral_* -------------------------
    telemetry_candidates = [c for d in decisions for c in d["pool"] if c["bonus_applied"] is not None]
    m["telemetry_present"] = len(telemetry_candidates) > 0
    m["telemetry_candidate_n"] = len(telemetry_candidates)
    if telemetry_candidates:
        raw_kms = [c["bonus_raw_km"] for c in telemetry_candidates if isinstance(c["bonus_raw_km"], (int, float))]
        neutral_kms = [c["bonus_neutral_km"] for c in telemetry_candidates if isinstance(c["bonus_neutral_km"], (int, float))]
        deltas = [c["bonus_delta"] for c in telemetry_candidates if isinstance(c["bonus_delta"], (int, float))]
        applied_true = sum(1 for c in telemetry_candidates if c["bonus_applied"] is True)
        m["bonus_raw_km_stat"] = stat(raw_kms)
        m["bonus_neutral_km_stat"] = stat(neutral_kms)
        m["bonus_dist_delta_stat"] = stat(deltas)
        m["bonus_applied_true_n"] = applied_true
        m["bonus_applied_true_rate"] = round(applied_true / len(telemetry_candidates), 4)

        # kontrfaktyczny re-sort ON vs OFF per decyzja
        flips = 0
        counted = 0
        winner_on_unknown = 0
        winner_on_target = 0
        for d in decisions:
            pool = d["pool"]
            if not pool:
                continue
            has_telemetry = any(c["bonus_applied"] is not None for c in pool)
            if not has_telemetry:
                continue
            scored_off = []
            scored_on = []
            for c in pool:
                base = c["score"]
                if base is None:
                    continue
                delta = c["bonus_delta"] if isinstance(c["bonus_delta"], (int, float)) else 0.0
                applied = c["bonus_applied"]
                if applied is True:
                    off_score, on_score = base - delta, base
                elif applied is False:
                    off_score, on_score = base, base + delta
                else:
                    off_score, on_score = base, base
                scored_off.append((off_score, c))
                scored_on.append((on_score, c))
            if not scored_off:
                continue
            counted += 1
            winner_off = max(scored_off, key=lambda t: t[0])[1]
            winner_on = max(scored_on, key=lambda t: t[0])[1]
            if winner_off["cid"] != winner_on["cid"]:
                flips += 1
            if is_unknown_view(winner_on):
                winner_on_unknown += 1
            if winner_on["cid"] in target_cids:
                winner_on_target += 1
        m["counterfactual_decisions_n"] = counted
        m["counterfactual_would_flip_n"] = flips
        m["counterfactual_would_flip_rate"] = round(flips / counted, 4) if counted else None
        m["counterfactual_winner_share_unknown_on"] = round(winner_on_unknown / counted, 4) if counted else None
        m["counterfactual_target_cids_winner_share_on"] = round(winner_on_target / counted, 4) if counted else None

        # --- DONOR-FILTER VALIDATION (Sol REJECT 19.07 pkt 2, POTWIERDZONY
        #     przez CTO; formula CORRECTED 19.07 wieczor po korekcie CTO/
        #     nogps-v2 przekazanej przez team-lead - moja pierwsza wersja
        #     uzywala is_position_known(pos_source), silnik uzywa
        #     road_km_from_synthetic_pos wprost - patrz komentarz przy
        #     _is_engine_donor). NIEZALEZNIE przeliczamy jaka mediana
        #     wyszlaby z KANONICZNEGO (silnikowego) filtra donorow i
        #     porownujemy do logged bonus_nogps_neutral_km. Pule zawierajace
        #     donora klasy brzegowej 1 (no_gps/pre_shift Z KOTWICA - ich
        #     prawdziwy km jest nadpisany w logu PO passie, nieodtwarzalny)
        #     sa WYLACZONE z twardego match-rate i liczone osobno jako
        #     "niemierzalne bezposrednio" (coverage metric) - inaczej
        #     match_rate<100% bylby ARTEFAKTEM tego narzedzia, nie defektem
        #     silnika (dokladnie ostrzezenie z eod_drafts NOGPS_NEUTRAL_
        #     SCORE_CANDIDATE.md sekcja "ROZBIEZNOSC WZOROW"). -------------
        donor_match = 0
        donor_mismatch = 0
        donor_mismatch_examples = []
        donor_checked = 0
        donor_unmeasurable = 0
        donor_synth_none_seen = 0
        for d in decisions:
            pool = d["pool"]
            logged_vals = {c["bonus_neutral_km"] for c in pool if isinstance(c["bonus_neutral_km"], (int, float))}
            if not logged_vals:
                continue
            donor_checked += 1
            donor_synth_none_seen += sum(1 for c in pool if c["synth"] is None)
            # klasa brzegowa 1: pula zawiera prawdziwego donora, ktorego km
            # zostal nadpisany w logu PO passie -> nieodtwarzalna wprost.
            if any(_is_engine_donor(c) and not _donor_km_reconstructable(c) for c in pool):
                donor_unmeasurable += 1
                continue
            logged_km = sorted(logged_vals)[0] if len(logged_vals) == 1 else None
            correct_donor_kms = sorted(c["km"] for c in pool if _is_engine_donor(c))
            if correct_donor_kms:
                _n = len(correct_donor_kms)
                _mid = _n // 2
                expected_km = (correct_donor_kms[_mid] if _n % 2 == 1
                               else 0.5 * (correct_donor_kms[_mid - 1] + correct_donor_kms[_mid]))
            else:
                expected_km = 5.0  # mirror F1.7/pass fallback gdy brak donorow
            if logged_km is not None and abs(logged_km - round(expected_km, 2)) <= 0.01:
                donor_match += 1
            else:
                donor_mismatch += 1
                if len(donor_mismatch_examples) < 5:
                    donor_mismatch_examples.append({
                        "order_id": d["order_id"], "logged_km": logged_km,
                        "expected_km_engine_formula": round(expected_km, 2),
                        "n_engine_donors": len(correct_donor_kms),
                    })
        donor_measurable = donor_checked - donor_unmeasurable
        m["donor_filter_checked_n"] = donor_checked
        m["donor_filter_unmeasurable_n"] = donor_unmeasurable
        m["donor_filter_measurable_n"] = donor_measurable
        m["donor_filter_coverage_rate"] = round(donor_measurable / donor_checked, 4) if donor_checked else None
        m["donor_filter_match_n"] = donor_match
        m["donor_filter_mismatch_n"] = donor_mismatch
        # match-rate WYLACZNIE na mierzalnej podpuli (klucz korekty!):
        m["donor_filter_match_rate"] = round(donor_match / donor_measurable, 4) if donor_measurable else None
        m["donor_filter_mismatch_examples"] = donor_mismatch_examples
        m["donor_filter_synth_none_seen"] = donor_synth_none_seen
    else:
        m["telemetry_note"] = ("telemetria kandydata (bonus_nogps_neutral_*) NIEOBECNA w tym oknie "
                                "- dane sprzed deployu kandydata 88acde3 (kod jeszcze w worktree, "
                                "nie zmergowany). Kontrfaktyczny ON/OFF niepoliczalny z tych danych.")

    return m


def compute_bucket_variance(summaries, bucket_hours, target_cids, min_bucket_n=MIN_BUCKET_N):
    """Wariancja winner-share w oknach czasowych bucket_hours - baza do progow
    GO/NO-GO (ile odchylenia jest 'normalne' historycznie, zanim cokolwiek
    sie zmieni w kodzie)."""
    buckets = collections.defaultdict(list)
    for d in summaries:
        if d["ts"] is None or d["best"] is None:
            continue
        idx = int(d["ts"].timestamp() // (bucket_hours * 3600))
        buckets[idx].append(d)

    rows = []
    for idx in sorted(buckets):
        bucket = buckets[idx]
        n = len(bucket)
        if n < min_bucket_n:
            continue
        unknown_w = sum(1 for d in bucket if not is_position_known(d["best"]["pos_source"]))
        nogps_w = sum(1 for d in bucket if d["best"]["pos_source"] == "no_gps")
        pool_slots_n = sum(len(d["pool"]) for d in bucket)
        unknown_pool_n = sum(1 for d in bucket for c in d["pool"] if not is_position_known(c["pos_source"]))
        nogps_pool_n = sum(1 for d in bucket for c in d["pool"] if c["pos_source"] == "no_gps")
        target_w = sum(1 for d in bucket if d["best"]["cid"] in target_cids)
        koord_n = sum(1 for d in bucket if d["verdict"] == "KOORD")
        silence_n = sum(1 for d in bucket if any(d["reason"].startswith(p) for p in SILENCE_PREFIXES))
        best_effort_n = sum(1 for d in bucket if d["best"]["best_effort"])
        km_vals = [d["best"]["km"] for d in bucket if d["best"]["km"] is not None]
        mixed_n = 0
        mixed_unknown_win_n = 0
        for d in bucket:
            flags = [is_position_known(c["pos_source"]) for c in d["pool"]]
            if True in flags and False in flags:
                mixed_n += 1
                if not is_position_known(d["best"]["pos_source"]):
                    mixed_unknown_win_n += 1
        t0 = min(d["ts"] for d in bucket)
        rows.append({
            "bucket_start": t0.isoformat(),
            "n": n,
            "winner_share_unknown": round(unknown_w / n, 4),
            "winner_share_nogps_only": round(nogps_w / n, 4),
            "pool_share_unknown": round(unknown_pool_n / pool_slots_n, 4) if pool_slots_n else None,
            "pool_share_nogps_only": round(nogps_pool_n / pool_slots_n, 4) if pool_slots_n else None,
            "gap_unknown": round(unknown_w / n - unknown_pool_n / pool_slots_n, 4) if pool_slots_n else None,
            "target_cids_winner_share": round(target_w / n, 4),
            "koord_rate": round(koord_n / n, 4),
            "silence_rate": round(silence_n / n, 4),
            "best_effort_rate": round(best_effort_n / n, 4),
            "km_winner_p50": round(statistics.median(km_vals), 3) if km_vals else None,
            "mixed_pool_n": mixed_n,
            "mixed_pool_unknown_win_rate": round(mixed_unknown_win_n / mixed_n, 4) if mixed_n >= 5 else None,
        })

    def series_stats(key):
        vals = [r[key] for r in rows if r[key] is not None]
        if len(vals) < 2:
            return {"n_buckets": len(vals), "mean": (vals[0] if vals else None), "stdev": None}
        return {
            "n_buckets": len(vals),
            "mean": round(statistics.mean(vals), 4),
            "stdev": round(statistics.pstdev(vals), 4),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
        }

    return {
        "bucket_hours": bucket_hours,
        "min_bucket_n": min_bucket_n,
        "rows": rows,
        "winner_share_unknown_variance": series_stats("winner_share_unknown"),
        "target_cids_winner_share_variance": series_stats("target_cids_winner_share"),
        "koord_rate_variance": series_stats("koord_rate"),
        "silence_rate_variance": series_stats("silence_rate"),
        "best_effort_rate_variance": series_stats("best_effort_rate"),
        "km_winner_p50_variance": series_stats("km_winner_p50"),
        "mixed_pool_unknown_win_rate_variance": series_stats("mixed_pool_unknown_win_rate"),
        "winner_share_nogps_only_variance": series_stats("winner_share_nogps_only"),
        "pool_share_unknown_variance": series_stats("pool_share_unknown"),
        "pool_share_nogps_only_variance": series_stats("pool_share_nogps_only"),
        "gap_unknown_variance": series_stats("gap_unknown"),
    }


# ---------------------------------------------------------------------------
# Raport tekstowy
# ---------------------------------------------------------------------------

def fmt_pct(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def fmt_pp(x):
    return "n/a" if x is None else f"{100 * x:.2f}pp"


def print_report(m, label, counters=None):
    print(f"\n{'=' * 78}")
    print(f"OKNO: {label}")
    print("=" * 78)
    if counters:
        print(f"  linie total={counters.get('lines_total', 0)} parsed={counters.get('lines_parsed', 0)} "
              f"parse_error={counters.get('lines_parse_error', 0)} w_oknie={counters.get('lines_in_window', 0)}")
    if m.get("empty"):
        print("  BRAK rekordow w tym oknie.")
        return
    print(f"  rekordy total={m['n_total_records']}  decyzje-z-best={m['n_decisions_with_best']}")
    print(f"  serializacja alternatives obcieta wzgledem pool_feasible_count: "
          f"{fmt_pct(m['pool_serialization_truncated_rate'])} "
          f"({m['pool_serialization_checked_n']} rekordow sprawdzonych) "
          f"-> pool-share ponizej jest PRZYBLIZENIEM z serializowanej czesci puli")

    print("\n-- WINNER-SHARE vs POOL-SHARE (grupa: pozycja NIEZNANA, kanon F-3"
          " POSITION_UNKNOWN_SOURCES) --")
    print(f"  pool-share (unknown w puli kandydatow): {fmt_pct(m['pool_share_unknown'])} "
          f"({m['unknown_pool_slots']}/{m['total_pool_slots']})")
    print(f"  winner-share (unknown jako #1):         {fmt_pct(m['winner_share_unknown'])} "
          f"({m['unknown_winners']}/{m['n_decisions_with_best']})")
    gap = None
    if m["pool_share_unknown"] is not None and m["winner_share_unknown"] is not None:
        gap = m["winner_share_unknown"] - m["pool_share_unknown"]
    print(f"  GAP (winner-share - pool-share): {'n/a' if gap is None else f'{100*gap:+.1f}pp'} "
          f"(>0 = grupa unknown wygrywa NIEPROPORCJONALNIE do udzialu w puli)")

    print("\n-- waski widok 'no_gps only' (dla porownania z liczbami z memory bug-notu:"
          " 24.8% puli / 50.5% zwyciezcow) --")
    print(f"  pool-share no_gps:   {fmt_pct(m['pool_share_nogps_only'])}")
    print(f"  winner-share no_gps: {fmt_pct(m['winner_share_nogps_only'])}")

    print("\n-- MIXED-POOL head-to-head (pula ma jednoczesnie known i unknown) --")
    print(f"  mixed pools: {m['mixed_pools_n']}")
    print(f"  unknown wygrywa w mixed pool: {fmt_pct(m['mixed_pool_unknown_win_rate'])} "
          f"(50% = neutralnie, wiecej = przewaga)")

    print("\n-- KONCENTRACJA zwyciezcow --")
    print(f"  top-2 cid: {fmt_pct(m['top2_concentration'])}")
    print(f"  target cids {m['target_cids']}: winner-share={fmt_pct(m['target_cids_winner_share'])} "
          f"vs pool-share={fmt_pct(m['target_cids_pool_share'])}")
    for cid, d in m["per_cid_winner_pool"].items():
        print(f"    cid={cid:>6}: winners={d['winners']:>4} ({fmt_pct(d['winner_share'])})  "
              f"pool_slots={d['pool_slots']:>4} ({fmt_pct(d['pool_share'])})")
    print(f"  top-12 wszystkich zwyciezcow: {m['winner_counter_top12']}")

    print("\n-- POST_WAVE residual (znany wyjatek F2.1c, NIE neutralizowany przez"
          " ten fix - dziala poza POSITION_UNKNOWN_SOURCES z konstrukcji; wymaga"
          " NIEPUSTEGO bagu, wiec strukturalnie nie moze wchlonac glownego wzorca"
          " no_gps+pusty-worek. Obserwacja NIEZALEZNA od telemetrii kandydata -"
          " dziala na dzisiejszych danych, bo pos_source='post_wave' juz istnieje) --")
    print(f"  post_wave zwyciezcy (wszyscy): {m['post_wave_winners_all']} = {fmt_pct(m['post_wave_winners_all_share'])} wszystkich decyzji")
    print(f"  post_wave zwyciezcy w target cids {m['target_cids']}: {m['post_wave_winners_target_cids']} "
          f"= {fmt_pct(m['post_wave_share_of_target_wins'])} ich {m['target_cids_winners_n']} zwyciestw")
    print(f"  post_wave / (post_wave+no_gps) w target cids: {fmt_pct(m['post_wave_share_of_target_plus_nogps_wins'])} "
          f"(alternatywny mianownik - tylko sciezki potencjalnie syntetyczne)")

    print("\n-- km_to_pickup DISPLAY (UWAGA: dla no_gps to fleet_avg PO nadpisaniu"
          " F1.7, NIE km ktore realnie zasililo score przed fixem - dlatego"
          " wyglada 'niewinnie'; prawdziwe road_km z centrum widoczne dopiero"
          " w bonus_nogps_neutral_raw_km po deployu kandydata) --")
    print(f"  unknown-pool: {stat_str(m['km_display_unknown_pool'])}")
    print(f"  known-pool:   {stat_str(m['km_display_known_pool'])}")
    print(f"  winner (all): {stat_str(m['km_display_winner_all'])}")

    print("\n-- SCORE MARGIN best vs #2 (proxy sily przewagi; near-ceiling ="
          " landslide zgodny z dowodem 112-vs-4.1 w kodzie) --")
    print(f"  {stat_str(m['score_margin_best_vs_alt0'])}"
          f"  (pominieto {m['score_margin_sentinel_skipped']} par z hard-cap sentinel score"
          f" |score|>={SENTINEL_SCORE_ABS_THRESHOLD:.0f})")

    print("\n-- REGRESJA-GUARDY (do porownania PRZED/PO w oknie B) --")
    print(f"  KOORD-rate:          {fmt_pct(m['koord_rate'])} ({m['koord_n']}/{m['n_total_records']})")
    print(f"  'cisza' rate (best_effort_low_score i pokrewne prefiksy z "
          f"_AP_KOORD_SILENCE_PREFIXES): {fmt_pct(m['silence_rate'])} ({m['silence_n']}/{m['n_total_records']})")
    print(f"  best_effort rate (zwyciezca best_effort=True): {fmt_pct(m['best_effort_rate'])}")

    print("\n-- TELEMETRIA KANDYDATA bonus_nogps_neutral_* --")
    if not m["telemetry_present"]:
        print(f"  {m['telemetry_note']}")
    else:
        print(f"  kandydaci z telemetria: {m['telemetry_candidate_n']}")
        print(f"  bonus_nogps_neutral_raw_km (prawdziwy km z centrum, zasila score): {stat_str(m['bonus_raw_km_stat'])}")
        print(f"  bonus_nogps_neutral_km (mediana neutralna):                        {stat_str(m['bonus_neutral_km_stat'])}")
        print(f"  bonus_nogps_neutral_dist_delta (zmiana score po neutralizacji):    {stat_str(m['bonus_dist_delta_stat'])}")
        print(f"  apply=True rate (flaga byla ON dla tych rekordow): {fmt_pct(m['bonus_applied_true_rate'])}")
        print(f"\n  KONTRFAKTYCZNY re-sort ON vs OFF (score +/- delta wg apply flag):")
        print(f"    decyzje z telemetria w puli: {m['counterfactual_decisions_n']}")
        print(f"    would_flip_winner: {m['counterfactual_would_flip_n']} = {fmt_pct(m['counterfactual_would_flip_rate'])}")
        print(f"    winner-share UNKNOWN gdyby ON: {fmt_pct(m['counterfactual_winner_share_unknown_on'])} "
              f"(cel: -> ~pool-share {fmt_pct(m['pool_share_unknown'])})")
        print(f"    target cids winner-share gdyby ON: {fmt_pct(m['counterfactual_target_cids_winner_share_on'])}")

        print(f"\n  DONOR-FILTER VALIDATION (Sol REJECT 19.07 pkt 2 - mediana MUSI liczyc sie"
              f" wg silnika: NOT road_km_from_synthetic_pos AND feasibility=='MAYBE'."
              f" Formula skorygowana 19.07 wieczor po korekcie CTO/nogps-v2 -"
              f" pierwsza wersja tego narzedzia mylnie uzywala is_position_known(pos_source)):")
        print(f"    decyzje sprawdzone: {m['donor_filter_checked_n']}")
        print(f"    z tego NIEMIERZALNE bezposrednio (pula ma donora klasy 'kotwica przy"
              f" no_gps/pre_shift' - km nadpisany w logu, nieodtwarzalny): {m['donor_filter_unmeasurable_n']}")
        print(f"    coverage (mierzalne / sprawdzone): {fmt_pct(m['donor_filter_coverage_rate'])}"
              f"  [{m['donor_filter_measurable_n']}/{m['donor_filter_checked_n']}]")
        print(f"    zgodne z formula silnika (na mierzalnej podpuli): {m['donor_filter_match_n']} "
              f"= {fmt_pct(m['donor_filter_match_rate'])}")
        print(f"    NIEZGODNE (donor filter nadal zly / inny wariant): {m['donor_filter_mismatch_n']}")
        if m["donor_filter_mismatch_examples"]:
            print(f"    przyklady rozjazdu (max 5): {m['donor_filter_mismatch_examples']}")
        if m["donor_filter_synth_none_seen"]:
            print(f"    UWAGA: {m['donor_filter_synth_none_seen']} kandydatow mialo synth=None"
                  f" (road_km_from_synthetic_pos brakujace) - nieoczekiwane, sprawdz serializacje.")
        print(f"    -> match_rate ~100% NA MIERZALNEJ PODPULI = Sol pkt 2 POTWIERDZONY naprawiony;"
              f" <100% = donor filter nadal wciaga zlych kandydatow, STOP przed ACK flipa."
              f" Niska coverage = gate ma malo sygnalu, raportuj obok match_rate, nie zamiast niego.")


def print_variance(v, label):
    print(f"\n{'-' * 78}")
    print(f"WARIANCJA HISTORYCZNA ({label}, bucket={v['bucket_hours']}h, "
          f"min_bucket_n={v['min_bucket_n']})")
    print("-" * 78)
    if not v["rows"]:
        print("  brak bucketow z wystarczajaca liczba decyzji (za krotkie/za rzadkie okno)")
        return
    for r in v["rows"]:
        print(f"  {r['bucket_start']}  n={r['n']:>4}  winner_share_unknown={fmt_pct(r['winner_share_unknown']):>7}  "
              f"target_winner_share={fmt_pct(r['target_cids_winner_share']):>7}  "
              f"koord={fmt_pct(r['koord_rate']):>6}  best_effort={fmt_pct(r['best_effort_rate']):>6}  "
              f"km_winner_p50={r['km_winner_p50']}")

    def series_line(name, key):
        s = v[key]
        stdev_s = fmt_pp(s.get("stdev")) if key != "km_winner_p50_variance" else (
            "n/a" if s.get("stdev") is None else f"{s['stdev']:.3f}km")
        mean_s = fmt_pct(s.get("mean")) if key != "km_winner_p50_variance" else (
            "n/a" if s.get("mean") is None else f"{s['mean']:.2f}km")
        rng = "n/a" if s.get("min") is None else (
            f"[{fmt_pct(s.get('min'))}, {fmt_pct(s.get('max'))}]" if key != "km_winner_p50_variance"
            else f"[{s.get('min'):.2f}, {s.get('max'):.2f}]km")
        print(f"  {name:26} n_bucket={s['n_buckets']} mean={mean_s:>8} stdev={stdev_s:>10} range={rng}")

    print()
    series_line("winner_share_unknown", "winner_share_unknown_variance")
    series_line("target_cids_winner_share", "target_cids_winner_share_variance")
    series_line("koord_rate", "koord_rate_variance")
    series_line("silence_rate", "silence_rate_variance")
    series_line("best_effort_rate", "best_effort_rate_variance")
    series_line("km_winner_p50", "km_winner_p50_variance")
    series_line("mixed_pool_unknown_win_rate", "mixed_pool_unknown_win_rate_variance")
    series_line("gap_unknown (winner-pool share)", "gap_unknown_variance")


def print_delta_table(ma, mb, label_a, label_b):
    print(f"\n{'=' * 78}")
    print(f"DELTA TABELA: [{label_b}] minus [{label_a}]")
    print("=" * 78)
    if ma.get("empty") or mb.get("empty"):
        print("  jedno z okien jest puste - brak sensownej delty")
        return
    rows = [
        ("winner_share_unknown", "winner-share (unknown, kanon F-3)"),
        ("pool_share_unknown", "pool-share (unknown, kanon F-3)"),
        ("winner_share_nogps_only", "winner-share (no_gps only)"),
        ("pool_share_nogps_only", "pool-share (no_gps only)"),
        ("mixed_pool_unknown_win_rate", "mixed-pool unknown win-rate"),
        ("top2_concentration", "top-2 concentration"),
        ("target_cids_winner_share", "target cids winner-share"),
        ("koord_rate", "KOORD-rate"),
        ("silence_rate", "'cisza' rate"),
        ("best_effort_rate", "best_effort rate"),
    ]
    print(f"  {'metryka':38} {'A':>10} {'B':>10} {'delta(pp)':>12}")
    for key, label in rows:
        va, vb = ma.get(key), mb.get(key)
        if va is None or vb is None:
            print(f"  {label:38} {'n/a':>10} {'n/a':>10} {'n/a':>12}")
            continue
        delta_pp = 100 * (vb - va)
        print(f"  {label:38} {fmt_pct(va):>10} {fmt_pct(vb):>10} {delta_pp:>+11.2f}pp")

    km_a, km_b = ma.get("km_display_winner_all", {}), mb.get("km_display_winner_all", {})
    if km_a.get("n") and km_b.get("n"):
        pct_change = 100 * (km_b["p50"] - km_a["p50"]) / km_a["p50"] if km_a["p50"] else None
        print(f"\n  mediana km_to_pickup zwyciezcow: A={km_a['p50']}  B={km_b['p50']}  "
              f"zmiana={'n/a' if pct_change is None else f'{pct_change:+.1f}%'} "
              f"(guard candidate-planu: nie powinno rosnac >10%)")

    if mb.get("telemetry_present"):
        print(f"\n  [B] ma telemetrie kandydata - kontrfaktyczny ON winner-share (unknown): "
              f"{fmt_pct(mb.get('counterfactual_winner_share_unknown_on'))} "
              f"vs OFF (rzeczywisty, w B) {fmt_pct(mb.get('winner_share_unknown'))} "
              f"vs pool-share w B {fmt_pct(mb.get('pool_share_unknown'))}")
    else:
        print(f"\n  [B] telemetria kandydata nieobecna - jesli B ma byc 'PO deployu kodu "
              f"kandydata (flaga OFF)', sprawdz czy --input obejmuje pliki logow PO "
              f"tym deployu (kandydat jeszcze nie zmergowany na czas tego przebiegu).")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def build_argparser():
    p = argparse.ArgumentParser(
        description="READ-ONLY pomiar shadow no-GPS center-score bug + kandydat ENABLE_NO_GPS_NEUTRAL_SCORE_DIST.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--input", nargs="+", default=[DEFAULT_INPUT],
                    help=f"Sciezka(i) do shadow_decisions.jsonl (domyslnie: {DEFAULT_INPUT}). "
                         f"Podaj wiele plikow (np. rotowany .1) zeby rozszerzyc pokrycie okna.")
    p.add_argument("--since", type=parse_single_ts_arg, default=None, help="ISO8601, dolna granica (wlacznie).")
    p.add_argument("--until", type=parse_single_ts_arg, default=None, help="ISO8601, gorna granica (wylacznie).")
    p.add_argument("--window-a", type=lambda s: parse_window_arg(s, "--window-a"), default=None,
                    help="'ISO_SINCE,ISO_UNTIL' - okno PRZED (uruchamia tryb porownania).")
    p.add_argument("--window-b", type=lambda s: parse_window_arg(s, "--window-b"), default=None,
                    help="'ISO_SINCE,ISO_UNTIL' - okno PO (uruchamia tryb porownania).")
    p.add_argument("--target-cids", default=",".join(DEFAULT_TARGET_CIDS),
                    help=f"CSV cid do sledzenia (domyslnie {','.join(DEFAULT_TARGET_CIDS)} = Ostapczukowie).")
    p.add_argument("--bucket-hours", type=float, default=6.0,
                    help="Wielkosc bucketa czasowego (h) do liczenia wariancji historycznej winner-share (domyslnie 6h).")
    p.add_argument("--bucket-hours-coarse", type=float, default=24.0,
                    help="Drugi, grubszy bucket (h) do krzyzowej weryfikacji wariancji (domyslnie 24h).")
    p.add_argument("--min-bucket-n", type=int, default=MIN_BUCKET_N,
                    help=f"Min. liczba decyzji w buckecie zeby wliczyc go do wariancji (domyslnie {MIN_BUCKET_N}).")
    p.add_argument("--json-out", default=None, help="Zapisz pelny raport JSON do tej sciezki (opcjonalne).")
    return p


def main(argv=None):
    args = build_argparser().parse_args(argv)
    target_cids = [c.strip() for c in args.target_cids.split(",") if c.strip()]

    json_report = {"input": args.input, "target_cids": target_cids}

    if args.window_a or args.window_b:
        if not (args.window_a and args.window_b):
            print("BLAD: --window-a i --window-b musza byc podane razem.", file=sys.stderr)
            return 2
        (sa, ua), (sb, ub) = args.window_a, args.window_b
        sum_a, cnt_a = load_summaries(args.input, sa, ua)
        sum_b, cnt_b = load_summaries(args.input, sb, ub)
        label_a = f"A [{sa.isoformat() if sa else '-inf'} .. {ua.isoformat() if ua else '+inf'})"
        label_b = f"B [{sb.isoformat() if sb else '-inf'} .. {ub.isoformat() if ub else '+inf'})"
        ma = compute_window_metrics(sum_a, target_cids)
        mb = compute_window_metrics(sum_b, target_cids)
        print_report(ma, label_a, cnt_a)
        print_report(mb, label_b, cnt_b)
        print_delta_table(ma, mb, label_a, label_b)
        va = compute_bucket_variance(sum_a, args.bucket_hours, target_cids, args.min_bucket_n)
        vb = compute_bucket_variance(sum_b, args.bucket_hours, target_cids, args.min_bucket_n)
        print_variance(va, label_a)
        print_variance(vb, label_b)
        json_report.update({
            "mode": "compare",
            "window_a": {"label": label_a, "counters": cnt_a, "metrics": ma, "variance": va},
            "window_b": {"label": label_b, "counters": cnt_b, "metrics": mb, "variance": vb},
        })
    else:
        summaries, counters = load_summaries(args.input, args.since, args.until)
        label = f"[{args.since.isoformat() if args.since else '-inf'} .. {args.until.isoformat() if args.until else '+inf'})"
        m = compute_window_metrics(summaries, target_cids)
        print_report(m, label, counters)
        v6 = compute_bucket_variance(summaries, args.bucket_hours, target_cids, args.min_bucket_n)
        print_variance(v6, label)
        v24 = compute_bucket_variance(summaries, args.bucket_hours_coarse, target_cids, args.min_bucket_n)
        print_variance(v24, label)
        json_report.update({
            "mode": "single",
            "window": {"label": label, "counters": counters, "metrics": m,
                       "variance_fine": v6, "variance_coarse": v24},
        })

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(json_report, fh, indent=2, ensure_ascii=False, default=str)
        print(f"\n[JSON zapisany: {args.json_out}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
