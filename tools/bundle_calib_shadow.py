"""bundle_calib_shadow — READ-ONLY shadow z OBIEKTYWEM SKALIBROWANYM NA GOTOWOŚĆ JEDZENIA.

Modelowany na `tools/b_route_shadow.py` (ta sama izolacja: PM.PLANS_FILE→temp, flaga
`os.environ[FLAG]`, dedup po bag_sig, `_start_anchor` dla pozycji, MAX_PER_RUN, `_append_jsonl`).

RÓŻNICA vs b_route_shadow = objektyw. Ziomek (route_simulator_v2._count_sla_violations)
liczy świeżość R6 od SYMULOWANEGO `pickup_at` planu → premiuje OPÓŹNIANIE odbioru
(odbierz później = jedzenie „świeższe" w aucie wg modelu) → wybiera out-and-back zamiast
bundla. Tu liczymy R6 od GOTOWOŚCI jedzenia w restauracji:
  ready = pickup_ready_at = czas_kuriera_warsaw   (dla niesionych: picked_up_at)
i karzemy spóźnioną DOSTAWĘ czasówki (deadline z pola `uwagi`, dotąd nieparsowany).

CALIB route = leksykograficznie najlepsza wg (r6_ready, czas_late, finish_in_min):
  - worek ≤5 zleceń (≤10 stopów): BRUTE FORCE wszystkie poprawne przeploty
    (pickup-before-delivery per oid; niesione = sam dropoff).
  - inaczej: kandydaci {served, b_full_retsp, b_lite} (jak b_route_shadow).
Macierz OSRM liczona RAZ na worek, walki reużywają (engine-spójne z served).

ZERO mutacji żywego stanu (plan_manager.PLANS_FILE → temp; nie pisze orders_state,
nie emituje eventów, nie woła Telegrama). Log per worek do `bundle_calib_shadow.jsonl`
zapisuje TEŻ coords+czas_kuriera+uwagi per oid → korpus RE-SCOROWALNY później
(b_route_shadow tego NIE robił — to była luka).

Flaga `ENABLE_BUNDLE_CALIB_SHADOW` (default OFF). Uruch: `python -m dispatch_v2.tools.bundle_calib_shadow`.
"""
import os
import re
import sys
import json
import pathlib
import tempfile
import logging
import itertools
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] bundle_calib_shadow: %(message)s")
_log = logging.getLogger("bundle_calib_shadow")

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
ORDERS_STATE = f"{STATE_DIR}/orders_state.json"
PLANS_LIVE = f"{STATE_DIR}/courier_plans.json"
GPS_PATH = f"{STATE_DIR}/gps_positions_pwa.json"
# Env-override ścieżek (re-collect λ=0, checklist bug4-logger_raport §4): osobny plik
# outputu + osobny state trzymają korpusy λ=1.5 i λ=0 ROZŁĄCZNE (zero skażenia).
OUT_JSONL = os.environ.get(
    "BUNDLE_CALIB_OUT_JSONL", f"{STATE_DIR}/bundle_calib_shadow.jsonl")
STATE_PATH = os.environ.get(
    "BUNDLE_CALIB_STATE_PATH", f"{STATE_DIR}/bundle_calib_shadow_state.json")
FLAG = "ENABLE_BUNDLE_CALIB_SHADOW"
MAX_PER_RUN = int(os.environ.get("BUNDLE_CALIB_SHADOW_MAX_PER_RUN", "25"))
# Limit brute-force: worek >5 zleceń → kandydaci heurystyczni (jak b_route_shadow).
BRUTE_MAX_ORDERS = int(os.environ.get("BUNDLE_CALIB_SHADOW_BRUTE_MAX_ORDERS", "5"))
ACTIVE = {"assigned", "picked_up"}

# R6: delivered - ready > cap = naruszenie świeżości. Cap = TEN SAM dial co dźwignia
# flipu O2 (common.O2_OVERAGE_CAP_MIN; konsumenci: route_simulator_v2.o2_score,
# plan_recheck._o2_key) — parytet instrument↔dźwignia jest CELOWY. Termiczna R6 jest
# PŁASKA (doktryna Adriana 2026-05-10, feasibility_v2: „35 min jedyną twardą regułą");
# „40" = BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN, cap SELEKCJI kuriera w eskalacji-3
# (ratunek przy 0 feasible) — INNY mechanizm, nie termika worka (pomiar 01.07:
# werdykt bramki O2 nieczuły na 35↔40, głębokie breachy med ~49 min dominują).
# ⚠ dial jest env-frozen per-proces (wzorzec #9): strojenie = O2_OVERAGE_CAP_MIN
# w env OBU serwisów (dispatch-shadow + dispatch-bundle-calib-shadow), inaczej dryf.
# BUNDLE_CALIB_R6_MAX_MIN = lokalny knob do przeliczeń post-hoc (nie strojenie live).
# Strażnik parytetu: tests/test_bundle_calib_shadow.py::test_overage_cap_equals_engine_dial
from dispatch_v2 import common as _common  # noqa: E402
R6_MAX_MIN = float(os.environ.get(
    "BUNDLE_CALIB_R6_MAX_MIN", getattr(_common, "O2_OVERAGE_CAP_MIN", 35.0)))
BUNDLE_IMPROVED_CZAS_MIN = 2.0   # min poprawa czas_late by liczyć improvement
# O2 objektyw CIĄGŁY (kalibracja 2026-06-25, replay 42 worków): minimalizuj
# overage (Σ minut świeżości ponad 35) + LAMBDA_CZAS * czas_late. Objektyw progowy
# (liczba R6) był ZŁY — poświęcał zlecenie bez deadline (Pruszynka 84 min vs 38).
# LAMBDA_CZAS=1.5: historyczny sweet spot — zeruje spóźnienia czasówek przy +5 min
# overage/42 worki, R6 bez zmian. Wyżej nic nie zyskuje. (ETAP 2: waga = decyzja Adriana.)
LAMBDA_CZAS = float(os.environ.get("BUNDLE_CALIB_LAMBDA_CZAS", "1.5"))
BUNDLE_IMPROVED_FINISH_TOL = 2.0  # finish nie gorszy o > tyle minut

# Cap świeżości CARRIED (Adrian, Opcja 3, 2026-06-25 — kurier niesie A + stoi pod
# restauracją odbioru B): best-under-Z = najlepszy O2-przeplot, w którym ŻADNE
# NIESIONE (picked_up) zlecenie nie przekracza Z min świeżości (delivered-ready).
# Objektyw O2 sam jest ŚLEPY na pasmo 20→35 (overage=max(0,age-35)) → CALIB wywozi
# carried >R6; under_z mierzy ile przeplotów wygrywa POD twardym capem Z i jakiego
# detouru wymaga → kalibracja X/Y/Z na review 02.07. Kotwice: SOFT 20 / danger 32 / R6 35.
Z_CAPS = [float(x) for x in os.environ.get("BUNDLE_CALIB_Z_CAPS", "20,32,35").split(",") if x.strip()]

# --- IZOLACJA: zapisy plan_manager (z _gen_one_bag_plan) idą na temp ---
from dispatch_v2 import plan_manager as PM          # noqa: E402
_SHADOW_PLANS = pathlib.Path(tempfile.gettempdir()) / "bundle_calib_shadow_plans.json"
PM.PLANS_FILE = _SHADOW_PLANS
PM.LOCK_FILE = pathlib.Path(str(_SHADOW_PLANS) + ".lock")
if not _SHADOW_PLANS.exists():
    _SHADOW_PLANS.write_text("{}")

from dispatch_v2 import plan_recheck as P            # noqa: E402
from dispatch_v2 import route_simulator_v2 as R      # noqa: E402
from dispatch_v2 import osrm_client                  # noqa: E402

DWELL_P = 1.0
DWELL_D = 3.5

# Deadline czasówki z `uwagi`. Łapie: "Czasówka na 14", "czasowka na 14:30",
# "na 14.00", "Czasówka 14:00", "CZASOWKA NA 16.30". Godzina 0-23, minuty opcjonalne.
_DEADLINE_RE = re.compile(
    r"czas[oó]wk[a-zą]*\s*(?:na\s*)?(\d{1,2})(?:[:.](\d{2}))?",
    re.IGNORECASE)


def _parse_deadline(uwagi, day_warsaw):
    """Z pola uwagi → aware UTC deadline dostawy tego dnia (Warsaw), albo None.

    day_warsaw = data (Warsaw) do której przypiąć godzinę z uwag.
    """
    if not uwagi:
        return None
    m = _DEADLINE_RE.search(str(uwagi))
    if not m:
        return None
    try:
        hh = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) is not None else 0
    except Exception:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    dt = datetime(day_warsaw.year, day_warsaw.month, day_warsaw.day, hh, mm,
                  tzinfo=WARSAW)
    return dt.astimezone(timezone.utc)


def _load(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {} if default is None else default


def _cok(c):
    return (isinstance(c, (list, tuple)) and len(c) == 2
            and all(isinstance(x, (int, float)) for x in c)
            and abs(c[0]) > 1.0 and abs(c[1]) > 1.0)


def _parse(ts):
    """ISO → aware UTC. Naive (np. picked_up_at) traktujemy jak Warsaw (konwencja stanu)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WARSAW)
    return dt.astimezone(timezone.utc)


def _bag_sig(oids, orders_state):
    parts = []
    for oid in sorted(oids):
        st = (orders_state.get(oid) or {}).get("status")
        parts.append(f"{oid}:{1 if st == 'picked_up' else 0}")
    return "|".join(parts)


def _mine_from_bag(oids, orders_state):
    mine = {}
    for oid in oids:
        o = orders_state.get(oid) or {}
        dc = o.get("delivery_coords")
        pc = o.get("pickup_coords")
        if not _cok(dc):
            return None
        if o.get("status") != "picked_up" and not _cok(pc):
            return None
        mine[oid] = {"status": o.get("status") or "assigned",
                     "courier_id": o.get("courier_id"),
                     "czas_kuriera_warsaw": o.get("czas_kuriera_warsaw"),
                     "picked_up_at": o.get("picked_up_at"),
                     "assigned_at": o.get("assigned_at"),
                     "uwagi": o.get("uwagi"),
                     "pickup_coords": pc, "delivery_coords": dc}
    return mine


def _stops_from_mine(mine):
    """Panel-format stopy: niesione = tylko dropoff; reszta pickup+dropoff."""
    st = []
    for oid, o in mine.items():
        if o.get("status") != "picked_up":
            st.append({"order_id": oid, "type": "pickup"})
        st.append({"order_id": oid, "type": "dropoff"})
    return st


def _coords_of(s, mine):
    o = mine[str(s["order_id"])]
    c = o["pickup_coords"] if s["type"] == "pickup" else o["delivery_coords"]
    return (float(c[0]), float(c[1]))


def _osrm_matrix(seqs, mine, pos):
    """Policz JEDNĄ macierz OSRM dla wszystkich unikalnych punktów worka + pos.
    Zwraca (idx, M) gdzie idx mapuje (round lat,lng)→indeks, M=macierz osrm_client.table."""
    pts = [(float(pos[0]), float(pos[1]))]
    seen = {(round(pts[0][0], 6), round(pts[0][1], 6))}
    for oid, o in mine.items():
        for c in (o["pickup_coords"], o["delivery_coords"]):
            key = (round(float(c[0]), 6), round(float(c[1]), 6))
            if key not in seen:
                seen.add(key)
                pts.append((float(c[0]), float(c[1])))
    M = osrm_client.table(pts, pts)
    if not M:
        return None, None
    idx = {(round(a, 6), round(b, 6)): i for i, (a, b) in enumerate(pts)}
    return idx, M


def _leg(a, b, idx, M):
    ia = idx.get((round(a[0], 6), round(a[1], 6)))
    ib = idx.get((round(b[0], 6), round(b[1], 6)))
    if ia is None or ib is None:
        return None
    v = (M[ia][ib] or {}).get("duration_s")
    if v is None or v >= 9e8:
        return None
    return v / 60.0


def _walk_calib(seq, mine, pos, now, idx, M, deadlines):
    """PER-ORDER czasy łańcuchem OSRM (reuse macierzy idx/M). Skalibrowany objektyw.

    Zwraca dict:
      r6_ready    = #zleceń gdzie delivered - ready > 35min,
                    ready = czas_kuriera_warsaw; niesione: min(czas_kuriera, picked_up_at).
                    ⚠ #11 audyt 28.06: to NIE 1:1 parytet z silnikiem — r6_thermal_anchor (silnik)
                    dla niesionych = picked_up_at-ONLY. Tu ŚWIADOMY rozjazd: min() łapie wadliwy
                    czas_kuriera (np. deklar. 14:07 a odbiór 13:38). Skutek: instrument bywa
                    bardziej konserwatywny (dłuższy carried_age) — 214/603 differs w ±7min od capa
                    Z → może przesunąć bucket Z. align-to-engine = kandydat sprintu O2 (02.07). NIE pickup_at (proj.).
      czas_late   = Σ max(0, delivered - deadline) [min] po zleceniach z deadlinem.
      finish_in_min, drive_min,
      carry_ready = {oid: minuty od ready/picked_up do dostawy} (do logu).
    Zwraca None gdy któryś leg OSRM niepoliczalny.
    """
    t = now
    drive = 0.0
    prev = (float(pos[0]), float(pos[1]))
    pick_at, deliv_at = {}, {}
    for s in seq:
        cur = _coords_of(s, mine)
        leg = _leg(prev, cur, idx, M)
        if leg is None:
            return None
        drive += leg
        t = t + timedelta(minutes=leg)
        prev = cur
        oid = str(s["order_id"])
        if s["type"] == "pickup":
            # committed czas_kuriera = floor wyjazdu (jak silnik: kurier nie odbierze przed)
            ck = _parse(mine[oid].get("czas_kuriera_warsaw"))
            if ck is not None and ck > t:
                t = ck
            pick_at[oid] = t
            t = t + timedelta(minutes=DWELL_P)
        else:
            deliv_at[oid] = t
            t = t + timedelta(minutes=DWELL_D)

    r6_ready = 0
    overage = 0.0           # CIĄGŁY term: Σ minut świeżości ponad 35 (O2)
    czas_late = 0.0
    carry_ready = {}
    for oid, o in mine.items():
        d = deliv_at.get(oid)
        if d is None:
            continue
        # READY = gotowość jedzenia (kalibracja), NIE symulowany pickup_at.
        # picked_up: min(czas_kuriera, picked_up_at) — jedzenie gotowe najpóźniej przy odbiorze
        # (łapie wadliwy czas_kuriera, np. pizza deklarowana 14:07 a odebrana 13:38).
        ck = _parse(o.get("czas_kuriera_warsaw"))
        if o.get("status") == "picked_up":
            pu = _parse(o.get("picked_up_at"))
            ready = min([x for x in (ck, pu) if x is not None], default=None)
        else:
            ready = ck
        if ready is not None:
            age = (d - ready).total_seconds() / 60.0
            carry_ready[oid] = round(age, 1)
            if age > R6_MAX_MIN:
                r6_ready += 1
            overage += max(0.0, age - R6_MAX_MIN)
        dl = deadlines.get(oid)
        if dl is not None:
            czas_late += max(0.0, (d - dl).total_seconds() / 60.0)

    last_deliv = max(deliv_at.values()) if deliv_at else now
    return {
        "r6_ready": r6_ready,
        "overage": round(overage, 1),
        "czas_late": round(czas_late, 1),
        "finish_in_min": round((last_deliv - now).total_seconds() / 60.0, 1),
        "drive_min": round(drive, 2),
        "carry_ready": carry_ready,
    }


def _oid_seq(seq):
    return [(s["type"], str(s["order_id"])) for s in seq]


def _all_valid_perms(mine):
    """Wszystkie poprawne sekwencje stopów (pickup-before-delivery per oid;
    niesione = sam dropoff). Zwraca listę list-of-stop-dict. Tylko gdy worek mały."""
    topick = [o for o in mine if mine[o].get("status") != "picked_up"]
    carried = [o for o in mine if mine[o].get("status") == "picked_up"]
    nodes = []
    for oid in topick:
        nodes.append((oid, "pickup"))
        nodes.append((oid, "dropoff"))
    for oid in carried:
        nodes.append((oid, "dropoff"))
    out = []
    for perm in itertools.permutations(nodes):
        seen_pick = set()
        ok = True
        for (oid, typ) in perm:
            if typ == "pickup":
                seen_pick.add(oid)
            else:  # dropoff
                if oid in topick and oid not in seen_pick:
                    ok = False
                    break
        if ok:
            out.append([{"order_id": oid, "type": typ} for (oid, typ) in perm])
    return out


def _served_order(plan_doc, mine):
    """Trasa SERWOWANA = kolejność stopów z żywego courier_plans (kanon po recanon),
    odsiana do aktywnego worka; niesione = sam dropoff. Fallback gdy plan nie pokrywa: carried-first."""
    if isinstance(plan_doc, dict) and plan_doc.get("stops"):
        out, seen = [], set()
        for s in plan_doc["stops"]:
            oid = str(s.get("order_id"))
            if oid not in mine:
                continue
            typ = "pickup" if s.get("type") == "pickup" else "dropoff"
            if typ == "pickup" and mine[oid].get("status") == "picked_up":
                continue
            key = (typ, oid)
            if key in seen:
                continue
            seen.add(key)
            out.append({"order_id": oid, "type": typ})
        cov_d = {o for (t, o) in [(x["type"], x["order_id"]) for x in out] if t == "dropoff"}
        if cov_d >= set(mine.keys()):
            return out
    carried = [o for o in mine if mine[o].get("status") == "picked_up"]
    topick = [o for o in mine if mine[o].get("status") != "picked_up"]
    out = [{"order_id": o, "type": "dropoff"} for o in carried]
    topick.sort(key=lambda o: (mine[o].get("czas_kuriera_warsaw") or "~"))
    for o in topick:
        out.append({"order_id": o, "type": "pickup"})
    for o in topick:
        out.append({"order_id": o, "type": "dropoff"})
    return out


def _b_full_retsp(cid, oids, mine, pos, now):
    """B = pełne re-TSP _gen_one_bag_plan (zapis na shadow-temp). Zwraca stopy lub None."""
    osd = {oid: dict(o) for oid, o in mine.items()}
    for o in osd.values():
        o["courier_id"] = cid
    gps = {str(cid): {"lat": float(pos[0]), "lon": float(pos[1]), "timestamp": now.isoformat()}}
    try:
        _SHADOW_PLANS.write_text("{}")
        ok = P._gen_one_bag_plan(str(cid), list(oids), osd, gps, now, R)
        if not ok:
            return None
        plan = PM.load_plan(str(cid))
        if not plan or not plan.get("stops"):
            return None
        return [{"order_id": str(s["order_id"]),
                 "type": "pickup" if s["type"] == "pickup" else "dropoff"}
                for s in plan["stops"]]
    except Exception as e:
        _log.warning(f"B re-TSP fail cid={cid}: {type(e).__name__}: {e}")
        return None


def _b_lite(served_seq, mine, pos, now, idx, M):
    """B-lite = wstaw NAJNOWSZE zlecenie (max assigned_at) do trasy bez niego + canon."""
    newest = None
    newest_ts = ""
    for oid, o in mine.items():
        ts = str(o.get("assigned_at") or "")
        if ts > newest_ts:
            newest_ts, newest = ts, oid
    if newest is None or len(mine) < 2:
        return None
    try:
        pre_stops = [dict(s) for s in served_seq if str(s["order_id"]) != newest]
        for s in pre_stops:
            o = mine[str(s["order_id"])]
            c = o["pickup_coords"] if s["type"] == "pickup" else o["delivery_coords"]
            s["coords"] = {"lat": c[0], "lng": c[1]}
            s["dwell_min"] = DWELL_P if s["type"] == "pickup" else DWELL_D
            s["status_at_plan_time"] = o.get("status") or "assigned"
        no = mine[newest]
        nstops = []
        if no.get("status") != "picked_up":
            nstops.append({"order_id": newest, "type": "pickup",
                           "coords": {"lat": no["pickup_coords"][0], "lng": no["pickup_coords"][1]},
                           "dwell_min": DWELL_P, "status_at_plan_time": "assigned"})
        nstops.append({"order_id": newest, "type": "dropoff",
                       "coords": {"lat": no["delivery_coords"][0], "lng": no["delivery_coords"][1]},
                       "dwell_min": DWELL_D, "status_at_plan_time": no.get("status") or "assigned"})
        plan = {"start_pos": {"lat": float(pos[0]), "lng": float(pos[1])},
                "start_ts": now.isoformat(), "stops": pre_stops, "optimization_method": "incremental"}

        def lf(a, b):
            v = _leg(a, b, idx, M)
            return v if v is not None else 9e3
        merged = PM.insert_stop_optimal(plan, nstops, now, lf)
        canon = P._apply_canon_order_invariants([dict(s) for s in merged["stops"]], mine,
                                                 (float(pos[0]), float(pos[1])), now)
        return [{"order_id": str(s["order_id"]),
                 "type": "pickup" if s["type"] == "pickup" else "dropoff"} for s in canon]
    except Exception as e:
        _log.warning(f"B-lite fail: {type(e).__name__}: {e}")
        return None


def _zkey(z):
    """Klucz Z jako czysty string ('20','32','35' gdy całkowite)."""
    return str(int(z)) if float(z).is_integer() else str(z)


def _max_carried_age(m, mine):
    """Max wieku (delivered-ready) po NIESIONYCH (picked_up) zleceniach worka.
    0.0 gdy worek nie ma niesionego — wtedy cap Z nie wiąże (przeplot bez carried).
    To jest wielkość bramkowana Opcją 3 Adriana: świeżość JUŻ wiezionego jedzenia."""
    if not m:
        return 0.0
    cr = m.get("carry_ready") or {}
    ages = [cr[o] for o in mine
            if mine[o].get("status") == "picked_up" and cr.get(o) is not None]
    return max(ages) if ages else 0.0


def _calib_route(mine, pos, now, idx, M, deadlines, served, b, blite):
    """CALIB = najlepsza wg O2 CIĄGŁEGO: min(overage + LAMBDA_CZAS*czas_late), finish jako tie-break.
    (objektyw progowy r6_ready był zły — poświęcał zlecenie bez deadline; replay 42 worki.)
    Worek ≤BRUTE_MAX_ORDERS → brute force; inaczej {served,b,blite}.

    Zwraca (best_seq, best_metrics, n_candidates, mode, under_z).
    under_z[_zkey(Z)] = best-O2-przeplot POD twardym capem świeżości carried (max wieku
    niesionego ≤ Z) dla Z∈Z_CAPS, albo None gdy ŻADEN feasible przeplot nie mieści się
    pod capem (Opcja 3 Adriana). Liczone w TEJ SAMEJ pętli — selekcja CALIB NIEZMIENIONA."""
    if len(mine) <= BRUTE_MAX_ORDERS:
        cands = _all_valid_perms(mine)
        mode = "brute"
    else:
        cands = [c for c in (served, b, blite) if c]
        mode = "heuristic"
    best_seq = None
    best_m = None
    best_key = None
    n_eval = 0
    uz = {z: None for z in Z_CAPS}   # z -> (key, seq, m, max_carried_age)
    for seq in cands:
        m = _walk_calib(seq, mine, pos, now, idx, M, deadlines)
        if m is None:
            continue
        n_eval += 1
        key = (round(m["overage"] + LAMBDA_CZAS * m["czas_late"], 2), m["finish_in_min"])
        if best_key is None or key < best_key:
            best_key = key
            best_seq = seq
            best_m = m
        # best-under-Z (Opcja 3, additive — NIE dotyka selekcji CALIB powyżej)
        mage = _max_carried_age(m, mine)
        for z in Z_CAPS:
            if mage <= z and (uz[z] is None or key < uz[z][0]):
                uz[z] = (key, seq, m, mage)
    under_z = {}
    for z in Z_CAPS:
        v = uz[z]
        if v is None:
            under_z[_zkey(z)] = None
            continue
        _k, _seq, _m, _mage = v
        under_z[_zkey(z)] = {
            "seq": _oid_seq(_seq),
            "max_carried_age": round(_mage, 1),
            "o2": round(_m["overage"] + LAMBDA_CZAS * _m["czas_late"], 2),
            "overage": _m["overage"],
            "czas_late": _m["czas_late"],
            "finish_in_min": _m["finish_in_min"],
            "drive_min": _m["drive_min"],
        }
    return best_seq, best_m, n_eval, mode, under_z


def _bundle_improved(m_served, m_calib):
    """CALIB lepszy od SERVED.

    Warunek (udokumentowany):
      (1) r6_ready(calib) <= r6_ready(served)            — świeżość NIE gorsza, ORAZ
      (2) r6_ready(calib) < r6_ready(served)  LUB
          czas_late(calib) <= czas_late(served) - 2min   — realna poprawa (świeżość albo punktualność), ORAZ
      (3) finish_in_min(calib) <= finish_in_min(served) + 2min  — nie kończy istotnie później.
    Czyli: CALIB nie pogarsza świeżości, daje konkretny zysk (mniej R6 LUB ≥2min mniej
    spóźnień czasówki) i nie wydłuża istotnie zamknięcia worka.
    """
    if not m_served or not m_calib:
        return False
    c1 = m_calib["r6_ready"] <= m_served["r6_ready"]
    c2 = (m_calib["r6_ready"] < m_served["r6_ready"]
          or m_calib["czas_late"] <= m_served["czas_late"] - BUNDLE_IMPROVED_CZAS_MIN)
    c3 = m_calib["finish_in_min"] <= m_served["finish_in_min"] + BUNDLE_IMPROVED_FINISH_TOL
    return bool(c1 and c2 and c3)


def _append_jsonl(rows, path=OUT_JSONL):
    if not rows:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as e:
        _log.warning(f"append_jsonl fail: {e}")


def _build_row(cid, oids, sig, mine, pos, now, served, calib_seq, m_served,
               m_calib, deadlines, mode, n_cands, under_z):
    bi = _bundle_improved(m_served, m_calib)
    coords = {oid: {"pickup": o["pickup_coords"], "delivery": o["delivery_coords"]}
              for oid, o in mine.items()}
    czk = {oid: o.get("czas_kuriera_warsaw") for oid, o in mine.items()}
    uwagi = {oid: o.get("uwagi") for oid, o in mine.items()}
    dlz = {oid: (dt.isoformat() if dt else None) for oid, dt in deadlines.items()}
    carried = [o for o in mine if mine[o].get("status") == "picked_up"]
    return {
        "ts": now.isoformat(),
        "lambda_czas": LAMBDA_CZAS,
        "cid": cid,
        "bag_sig": sig,
        "order_ids": sorted(oids),
        "n_orders": len(oids),
        "n_carried": len(carried),
        "calib_mode": mode,
        "n_candidates": n_cands,
        "served_seq": _oid_seq(served),
        "calib_seq": _oid_seq(calib_seq) if calib_seq else None,
        "m_served": m_served,
        "m_calib": m_calib,
        "deadlines": dlz,
        "bundle_improved": bi,
        "delta_r6": (round(m_served["r6_ready"] - m_calib["r6_ready"], 2)
                     if (m_served and m_calib) else None),
        "delta_czas": (round(m_served["czas_late"] - m_calib["czas_late"], 2)
                       if (m_served and m_calib) else None),
        "delta_finish": (round(m_served["finish_in_min"] - m_calib["finish_in_min"], 2)
                         if (m_served and m_calib) else None),
        # best-under-Z (Opcja 3 Adriana 2026-06-25): najlepszy O2-przeplot pod twardym
        # capem świeżości carried Z∈{20,32,35}; max wiek niesionego w served/CALIB
        # (CALIB jest ślepy na pasmo 20→35 → calib_max_carried_age pokazuje rozjazd).
        "under_z": under_z,
        "served_max_carried_age": round(_max_carried_age(m_served, mine), 1),
        "calib_max_carried_age": round(_max_carried_age(m_calib, mine), 1),
        # RE-SCORE corpus: coords + czas_kuriera + uwagi per oid (luka b_route_shadow)
        "coords": coords,
        "czas_kuriera": czk,
        "uwagi": uwagi,
    }


def assess_bag(cid, oids, orders_state, plans, gps, now):
    """Pełna ocena jednego worka. Zwraca row dict albo None (skip)."""
    mine = _mine_from_bag(oids, orders_state)
    if mine is None:
        return None
    try:
        anchor = P._start_anchor(cid, oids, orders_state, gps, now)
    except Exception:
        anchor = None
    if anchor is None or not _cok(anchor[0]):
        return None
    pos = anchor[0]
    sig = _bag_sig(oids, orders_state)
    day_warsaw = now.astimezone(WARSAW)
    deadlines = {oid: _parse_deadline(o.get("uwagi"), day_warsaw)
                 for oid, o in mine.items()}

    seqs_for_matrix = [_stops_from_mine(mine)]
    idx, M = _osrm_matrix(seqs_for_matrix, mine, pos)
    if idx is None:
        return None

    served = _served_order(plans.get(cid), mine)
    b = _b_full_retsp(cid, oids, mine, pos, now)
    blite = _b_lite(served, mine, pos, now, idx, M)
    m_served = _walk_calib(served, mine, pos, now, idx, M, deadlines)
    calib_seq, m_calib, n_cands, mode, under_z = _calib_route(
        mine, pos, now, idx, M, deadlines, served, b, blite)
    if m_served is None or m_calib is None:
        return None
    return _build_row(cid, oids, sig, mine, pos, now, served, calib_seq,
                      m_served, m_calib, deadlines, mode, n_cands, under_z)


def run_once(now=None, dry_run=False):
    if os.environ.get(FLAG, "0") != "1":
        _log.info(f"{FLAG} != 1 → no-op")
        return []
    now = now or datetime.now(timezone.utc)
    orders_state = _load(ORDERS_STATE)
    plans = _load(PLANS_LIVE)
    gps = _load(GPS_PATH)
    state = _load(STATE_PATH)
    if not isinstance(state, dict):
        state = {}

    by_cid = {}
    for oid, o in orders_state.items():
        if isinstance(o, dict) and o.get("status") in ACTIVE and o.get("courier_id") is not None:
            by_cid.setdefault(str(o["courier_id"]), []).append(str(oid))

    rows = []
    new_state = dict(state)
    processed = 0
    for cid, oids in by_cid.items():
        if len(oids) < 2:
            new_state[cid] = ""
            continue
        sig = _bag_sig(oids, orders_state)
        if state.get(cid) == sig:
            continue
        new_state[cid] = sig
        if processed >= MAX_PER_RUN:
            continue
        try:
            row = assess_bag(cid, oids, orders_state, plans, gps, now)
        except Exception as e:
            _log.warning(f"assess_bag fail cid={cid}: {type(e).__name__}: {e}")
            row = None
        if row is not None:
            rows.append(row)
            processed += 1

    if dry_run:
        n_imp = sum(1 for r in rows if r["bundle_improved"])
        _log.info(f"DRY-RUN: {len(rows)} rekordów, bundle_improved={n_imp} "
                  f"(processed={processed}, couriers={len(by_cid)})")
        return rows
    _append_jsonl(rows)
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(new_state, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        _log.warning(f"state save fail: {e}")
    n_imp = sum(1 for r in rows if r["bundle_improved"])
    _log.info(f"logged={len(rows)} bundle_improved={n_imp} "
              f"(couriers={len(by_cid)}, processed={processed})")
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
