"""JEDNO ŹRÓDŁO kolejności trasy kuriera (PODJAZDY / kursy) — kanon zunifikowany.

PROMOCJA `route_podjazdy` (Sprint 30, 2026-07-07). Ten moduł jest DOMEM reguły
kolejności; `route_podjazdy` re-eksportuje z niego (alias wsteczny — apka, golden,
narzędzia parytetu importują `route_podjazdy` bez zmian). Logika przeniesiona
VERBATIM z `route_podjazdy` (2026-06-18/28) — bajt-identyczna projekcja
`[(typ, sorted(order_ids))]` dowiedziona na korpusie golden + żywych workach
(patrz `eod_drafts/2026-07-07/S30A_routeorder_0diff.md`).

Cel: zamknąć INV-SRC-ROUTE-ORDER konstrukcyjnie zamiast 4 kopii trzymanych flagami.
Konsumenci delegują do tego modułu (apka `courier_orders`, konsola
`fleet_state`; zmiany w osobnych repo są atomowym elementem ADR-010).
Panel importuje przez `sys.path`; apka Kotlin NIE dzieli kodu — kontrakt
cross-język = `stop_id` + `order_ids` + committed per zlecenie.

PURE — bez I/O, bez OSRM, bez datetime.now → deterministyczne. ETA / wrapping /
floory / monotonic zostają PER-POWIERZCHNIA (prezentacja, nie kolejność).

Kontrakt czasu odbioru (OWNER_CONFIRMED 2026-07-28): grupa fizycznego stopu
NIE ma jednego czasu prezentowanego. Każde zlecenie zachowuje bajt-w-bajt własne
``czas_kuriera_warsaw``. ``stop_id`` i ``order_ids`` opisują wyłącznie tożsamość
i membership stopu. Downstream może osobno liczyć wyjazd ze stopu od
``latest_ready``; nie wolno z tego syntetyzować committed pokazywanego zlecenia.

Reguła PODJAZDÓW: odbiory dzielone na kursy (kolejne zlecenia w oknie
≤PICKUP_MERGE_MIN min = jeden podjazd), w kursie odbiory grupowane po
restauracji, carried (picked_up) na początek; per kurs: WSZYSTKIE odbiory →
WSZYSTKIE dostawy (kolejność dostaw wg rangi planu Ziomka, inaczej wg czasu
odbioru). Minimalizuje powroty po jedzenie (R-NO-RETURN) i przeplot.

trust_canon (2026-06-28): gdy ON i plan Ziomka pokrywa CAŁY worek → renderuj
porządek kanonu przez `_canon_order_from_plan`, ale nigdy kosztem guardu
rozrzutu committed. Inaczej (flaga OFF / plan niepełny) → lokalne podjazdy
carried-first.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import math
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
PICKUP_MERGE_MIN = 10          # jedyny próg sklejania odbiorów w jeden podjazd
# WB3 / case 491870 (2026-08-02): JEDYNY kontrakt fizycznego punktu odbioru.
# Wcześniej plan_recheck miał jednocześnie klucz ~1 m (`_pickup_rest_key`) i
# osobny promień 180 m (`RELAX_COLOC_PICKUP_M`). To rozszczepiało tę samą prawdę:
# detektor powrotu nie widział punktu, który relax uznawał za współlokalny.
PICKUP_POINT_RADIUS_M = 180.0
PICKUP_POINT_CONTRACT_VERSION = "pickup-point-v1"
_SENTINEL = datetime.max.replace(tzinfo=WARSAW)
_BIG = 1 << 30


def _iso(s):
    """Parsuj ISO (z 'Z' lub offsetem) → aware datetime; None gdy się nie da."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def _attr(o, name):
    """Duck-typing: obsłuż zarówno obiekt (BagOrder) jak i dict."""
    if isinstance(o, dict):
        return o.get(name)
    return getattr(o, name, None)


def _pickup_dt(o):
    return _iso(_attr(o, "czas_kuriera_warsaw"))


def _pickup_coords(order):
    """Znormalizowane ``(lat, lon)`` albo ``None``; bez sentineli i I/O."""
    coords = _attr(order, "pickup_coords")
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    try:
        lat, lon = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if (lat, lon) == (0.0, 0.0) or not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _pickup_name(order) -> str:
    """Fallback tożsamości tylko gdy co najmniej jeden rekord nie ma geometrii."""
    raw = (
        _attr(order, "restaurant")
        or _attr(order, "restaurant_name")
        or _attr(order, "restaurant_address")
        or _attr(order, "pickup_address")
        or ""
    )
    return " ".join(str(raw).strip().casefold().split())


def pickup_point_distance_m(first, second):
    """Odległość pickup↔pickup w metrach albo ``None`` przy braku geometrii."""
    a, b = _pickup_coords(first), _pickup_coords(second)
    if a is None or b is None:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    hav = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * 6_371_000.0 * math.asin(math.sqrt(hav))


def same_pickup_point(first, second) -> bool:
    """Kanoniczna odpowiedź „czy to ta sama fizyczna wizyta po odbiór?”.

    Gdy oba rekordy mają poprawne koordynaty, geometria zawsze wygrywa nad nazwą.
    Przy niepełnej geometrii fail-soft używa znormalizowanej nazwy restauracji.
    Promień jest jednym dialem właściciela kontraktu, nigdy parametrem callera.
    """
    distance = pickup_point_distance_m(first, second)
    if distance is not None:
        return distance <= PICKUP_POINT_RADIUS_M
    first_name, second_name = _pickup_name(first), _pickup_name(second)
    return bool(first_name and second_name and first_name == second_name)


def position_at_pickup_point(position, order) -> bool:
    """Czy pozycja kuriera leży w kanonicznym punkcie odbioru ``order``."""
    coords = _pickup_coords(order)
    if coords is None or not isinstance(position, (list, tuple)) or len(position) < 2:
        return False
    probe = {"pickup_coords": position}
    return same_pickup_point(probe, {"pickup_coords": coords})


def group_same_pickup_points(orders):
    """Stabilne grupy complete-link według kanonicznego kontraktu.

    Relacja promienia nie jest przechodnia (A~B i B~C nie implikuje A~C), więc
    kandydat trafia do grupy tylko gdy pasuje do KAŻDEGO jej członka. Eliminuje
    łańcuchowe scalenie dwóch faktycznie odległych lokali.
    """
    groups = []
    for order in orders:
        target = next((group for group in groups
                       if all(same_pickup_point(order, member) for member in group)), None)
        if target is None:
            groups.append([order])
        else:
            target.append(order)
    return groups


def _plan_pickup_clusters(plan_doc) -> dict:
    """{oid: (cluster_idx, pickup_rank)} dla ODBIORÓW z planu Ziomka. KOLEJNE odbiory
    (bez dostawy między nimi) = ten sam podjazd (cluster_idx). pickup_rank = pozycja
    odbioru w planie (do wiernej kolejności wewnątrz podjazdu). Pusty gdy brak planu.

    Membership planu jest pierwszym warunkiem grupowania. Drugim, zawsze
    egzekwowanym warunkiem jest rozrzut committed <= PICKUP_MERGE_MIN; sam klaster
    planu nigdy nie omija guardu czasu."""
    out = {}
    if not isinstance(plan_doc, dict):
        return out
    cidx = -1
    rnk = 0
    prev_pickup = False
    for s in (plan_doc.get("stops") or []):
        if not isinstance(s, dict):
            continue
        is_pickup = s.get("type") == "pickup"
        if is_pickup:
            oid = str(s.get("order_id"))
            if not prev_pickup:
                cidx += 1          # nowy podjazd zaczyna się po dostawie
            if oid not in out:
                out[oid] = (cidx, rnk)
                rnk += 1
        prev_pickup = is_pickup
    return out


def stop_id_for(kind, order_ids) -> str:
    """Deterministyczna tożsamość stopu z typu i membershipu, nigdy z koordynatów."""
    normalized = sorted(dict.fromkeys(str(oid) for oid in order_ids))
    return f"{kind}:{','.join(normalized)}"


def _pickup_spread_ok(orders) -> bool:
    """Czy wszystkie committed w grupie istnieją i mieszczą się w oknie 10 min."""
    values = [_pickup_dt(order) for order in orders]
    if not values or any(value is None for value in values):
        return len(values) <= 1
    return max(values) - min(values) <= timedelta(minutes=PICKUP_MERGE_MIN)


def _split_by_pickup_spread(ordered):
    """Podziel odbiory tak, by wewnętrzny spread każdej grupy był <= próg."""
    runs = []
    for order in ordered:
        if runs and _pickup_spread_ok([*runs[-1], order]):
            runs[-1].append(order)
        else:
            runs.append([order])
    return runs


def pickup_runs(to_pick, plan_doc=None, plan_aware=False):
    """Podziel odbiory na PODJAZDY (kursy) + grupuj po restauracji wewnątrz kursu.
    Wejście/wyjście: listy zleceń (obiekty BagOrder-podobne albo dict-y).

    plan_aware + plan Ziomka pokrywa WSZYSTKIE odbiory worka → zachowaj membership
    i kolejność klastra planu, a następnie ZAWSZE podziel go tak, by wewnętrzny
    rozrzut committed był <=PICKUP_MERGE_MIN. Inaczej (brak/niepełny plan lub
    flaga OFF) → podział wg tego samego okna."""
    clusters = _plan_pickup_clusters(plan_doc) if plan_aware else {}
    use_plan = bool(clusters) and all(str(_attr(o, "order_id")) in clusters for o in to_pick)
    if use_plan:
        groups: dict = {}
        for o in to_pick:
            cidx = clusters[str(_attr(o, "order_id"))][0]
            groups.setdefault(cidx, []).append(o)
        # podjazdy wg kolejności planu; guard rozrzutu obowiązuje także tutaj
        return [
            split
            for c in sorted(groups)
            for split in _split_by_pickup_spread(
                sorted(
                    groups[c],
                    key=lambda o: clusters[str(_attr(o, "order_id"))][1],
                )
            )
        ]
    ordered = sorted(to_pick, key=lambda o: (_pickup_dt(o) or _SENTINEL, str(_attr(o, "order_id"))))
    runs = _split_by_pickup_spread(ordered)
    out = []
    for run in runs:
        groups = group_same_pickup_points(run)
        out.append([
            order
            for group in groups
            for order in sorted(group, key=lambda o: (_pickup_dt(o) or _SENTINEL,
                                                       str(_attr(o, "order_id"))))
        ])
    return out


def plan_drop_rank(plan_doc) -> dict:
    """Względna kolejność DOSTAW z planu Ziomka (courier_plans.json stops)."""
    rank = {}
    di = 0
    if isinstance(plan_doc, dict):
        for s in (plan_doc.get("stops") or []):
            if not isinstance(s, dict):
                continue
            oid = str(s.get("order_id"))
            typ = "pickup" if s.get("type") == "pickup" else "dropoff"
            if typ == "dropoff" and oid not in rank:
                rank[oid] = di
                di += 1
    return rank


def _canon_order_from_plan(bag, plan_doc):
    """Kolejność stopów WPROST z kanonu Ziomka (courier_plans) — LUSTRO konsoli
    `fleet_state` (który deleguje do tego modułu). Renderuje porządek planu:
    niesione (picked_up) = tylko dostawa (pomiń węzeł odbioru), kolejne odbiory tej
    samej restauracji scalone w JEDEN stop (jedna liczba), dostawy dedup.
    Zawiera carried-first relax silnika („odbierz po drodze zanim dowieziesz niesione").

    Zwraca [(typ,[order_ids])] TYLKO gdy plan pokrywa CAŁY worek (cov_drop>=need_drop
    ORAZ cov_pick>=need_pick — identyczna bramka jak konsola); inaczej None (→ caller
    spada do lokalnych podjazdów carried-first). PURE, deterministyczne."""
    if not isinstance(plan_doc, dict):
        return None
    by_oid = {str(_attr(o, "order_id")): o for o in bag}
    out: list[tuple[str, list[str]]] = []
    seen_drop: set[str] = set()
    saw_seq = False
    for s in (plan_doc.get("stops") or []):
        if not isinstance(s, dict):
            continue
        saw_seq = True
        oid = str(s.get("order_id"))
        typ = "pickup" if s.get("type") == "pickup" else "dropoff"
        o = by_oid.get(oid)
        if o is None:
            continue
        if typ == "pickup":
            if _attr(o, "status") == "picked_up":      # carried = brak odbioru
                continue
            if out and out[-1][0] == "pickup" and \
                    same_pickup_point(by_oid[out[-1][1][-1]], o) and \
                    _pickup_spread_ok([by_oid[item] for item in out[-1][1]] + [o]):
                out[-1][1].append(oid)                  # scal odbiory tej samej restauracji
            else:
                out.append(("pickup", [oid]))
        else:
            if oid in seen_drop:
                continue
            seen_drop.add(oid)
            out.append(("dropoff", [oid]))
    if not saw_seq:
        return None
    need_drop = {str(_attr(o, "order_id")) for o in bag}
    need_pick = {str(_attr(o, "order_id")) for o in bag if _attr(o, "status") != "picked_up"}
    cov_drop = {o for (t, oids) in out for o in oids if t == "dropoff"}
    cov_pick = {o for (t, oids) in out for o in oids if t == "pickup"}
    if cov_drop >= need_drop and cov_pick >= need_pick:
        return out
    return None


def order_podjazdy(bag, plan_doc=None, plan_aware=False,
                   trust_canon=False) -> list[tuple[str, list[str]]]:
    """JEDYNE źródło kolejności. Zwraca listę stopów [(typ, [order_ids]), ...]
    gdzie typ ∈ {'pickup','dropoff'} a order_ids to zgrupowane zlecenia
    (odbiory tej samej restauracji w jednym podjeździe = jeden stop).

    bag: lista obiektów/dict-ów z polami: order_id, status, restaurant,
         czas_kuriera_warsaw. plan_doc: dict planu Ziomka (opcjonalny).
    plan_aware: gdy True i plan pokrywa worek, podjazdy idą wg klastrów planu
         (patrz pickup_runs) — koordynator/kurier widzą bundle Ziomka, nie podział czasowy.
    trust_canon: gdy True i plan Ziomka pokrywa CAŁY worek → zachowaj porządek
         courier_plans z carried-first relaxem silnika, lecz zawsze zastosuj
         time-spread guard. Inaczej → lokalne podjazdy carried-first.
    """
    if not bag:
        return []
    if trust_canon:
        canon = _canon_order_from_plan(bag, plan_doc)
        if canon is not None:
            return canon
    rank = plan_drop_rank(plan_doc)

    def _drop_key(o):
        oid = str(_attr(o, "order_id"))
        return (rank.get(oid, _BIG), _attr(o, "czas_kuriera_warsaw") or "~")

    carried = sorted((o for o in bag if _attr(o, "status") == "picked_up"), key=_drop_key)
    to_pick = [o for o in bag if _attr(o, "status") != "picked_up"]

    order: list[tuple[str, list[str]]] = [("dropoff", [str(_attr(o, "order_id"))]) for o in carried]
    for run in pickup_runs(to_pick, plan_doc, plan_aware):
        i = 0
        while i < len(run):
            anchor = run[i]
            grp = [str(_attr(run[i], "order_id"))]
            i += 1
            while i < len(run) and same_pickup_point(run[i], anchor):
                grp.append(str(_attr(run[i], "order_id")))
                i += 1
            order.append(("pickup", grp))
        drops = sorted(run, key=_drop_key)
        order += [("dropoff", [str(_attr(o, "order_id"))]) for o in drops]
    return order


# Alias nazwy kanonicznej modułu zunifikowanego. `order_podjazdy` zostaje głównym
# symbolem (apka/golden/narzędzia importują go przez route_podjazdy) — `order_route`
# to czytelniejsza nazwa dla nowych konsumentów. Ta sama funkcja, zero kopii.
order_route = order_podjazdy


def build_route_stops(bag, plan_doc=None, *, plan_aware=False,
                      trust_canon=False) -> list[dict]:
    """Kanoniczne stopy z tożsamością, membershipem i committed per zlecenie.

    Nie istnieje ``committed_at`` stopu. Dla pickupów jedynym kontraktem
    prezentacyjnym jest ``committed_by_order`` skopiowane bez transformacji
    z każdego zlecenia. Dropoff nie niesie czasu odbioru.
    """
    by_oid = {str(_attr(order, "order_id")): order for order in bag}
    stops = []
    for kind, raw_order_ids in order_podjazdy(
        bag,
        plan_doc,
        plan_aware=plan_aware,
        trust_canon=trust_canon,
    ):
        order_ids = [str(order_id) for order_id in raw_order_ids]
        stop = {
            "stop_id": stop_id_for(kind, order_ids),
            "kind": kind,
            "order_ids": order_ids,
        }
        if kind == "pickup":
            stop["committed_by_order"] = {
                order_id: _attr(by_oid[order_id], "czas_kuriera_warsaw")
                for order_id in order_ids
            }
        stops.append(stop)
    return stops


def repair_dropoffs_after_pickups(seq, *, kind_key="kind", id_key="order_id"):
    """Dostawy wyprzedzone przez sortowanie odbiorów → przenieś tuż ZA ich odbiór.

    JEDNO ŹRÓDŁO bliźniaka rozbitego dotąd na 2 kopie:
      - `courier_api/courier_orders._repair_dropoffs_after_pickups` (klucz 'kind'),
      - `dispatch_v2/plan_recheck._repair_dropoffs_after_pickups` (klucz 'type').
    Różnica kopii = wyłącznie nazwa klucza typu (+ plan_recheck robił str(order_id)).
    Tu klucz typu jest parametrem (`kind_key`), a order_id zawsze str-castowany po OBU
    stronach porównania (pickup↔dropoff tego samego zlecenia mają ten sam typ w danym
    wywołaniu → str-cast zachowuje relację równości = bajt-identyczny wynik dla obu
    dotychczasowych kopii; dowód fuzz w `eod_drafts/2026-07-07/S30A_routeorder_0diff.md`).

    Worek PRZEPLATANY (odbiór→dostawa→odbiór): sortowanie odbiorów wg committed
    potrafi wepchnąć dostawę przed jej własny odbiór. Zamiast rezygnować z CAŁEGO
    sortowania (dawny fail-safe → inwersja zostawała), każdą taką dostawę wstawiamy
    bezpośrednio za jej odbiór. Przeniesienie dostawy W PRAWO nie tworzy nowych
    naruszeń → pętla domyka się w ≤ liczbie naruszeń; twardy limit iteracji =
    defense-in-depth. Zwraca naprawioną listę albo None gdy się nie domknęła
    (caller traktuje jak dawny fail-safe i zostawia sekwencję bez zmian)."""
    out = list(seq)
    for _ in range(len(out) * len(out) + 1):
        pidx = {str(s.get(id_key)): i for i, s in enumerate(out)
                if s.get(kind_key) == "pickup"}
        viol = next((i for i, s in enumerate(out)
                     if s.get(kind_key) == "dropoff"
                     and pidx.get(str(s.get(id_key)), -1) > i), None)
        if viol is None:
            return out
        pi = pidx[str(out[viol].get(id_key))]
        s = out.pop(viol)
        out.insert(pi, s)   # po pop odbiór zjechał na pi-1 → insert(pi) = tuż za nim
    return None


def build_stop_sequence(bag, plan_doc=None, *, plan_aware=False,
                        trust_canon=False) -> list[dict]:
    """Zunifikowana kolejność jako lista kroków `[{"order_id": str, "kind": typ}, ...]`
    — forma konsumowana wprost przez apkę (`courier_orders.build_view`) i konsolę.
    Rozwija zgrupowane odbiory (jeden stop = kilka order_ids) na kroki per-zlecenie,
    dokładnie jak dziś gałąź `console_podjazdy` w apce (courier_orders:1145). ETA /
    dwell / coords dokleja caller osobno (prezentacja per-powierzchnia)."""
    sequence = []
    for stop in build_route_stops(
        bag,
        plan_doc,
        plan_aware=plan_aware,
        trust_canon=trust_canon,
    ):
        for order_id in stop["order_ids"]:
            step = {
                "order_id": order_id,
                "kind": stop["kind"],
                "stop_id": stop["stop_id"],
                "order_ids": list(stop["order_ids"]),
            }
            if stop["kind"] == "pickup":
                step["committed_at"] = stop["committed_by_order"][order_id]
            sequence.append(step)
    return sequence
