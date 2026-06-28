"""Źródło kolejności trasy kuriera — PODJAZDY (kursy) — dla APKI kuriera.

Importowane przez apkę (`courier_api/courier_orders.build_view`).
⚠ Konsola koordynatora ma WŁASNĄ kopię-lustro (panel
`fleet_state._order_from_plan_seq`/`_build_route`) i NIE importuje tego modułu
(osobne repo/venv) — parytet apka↔konsola utrzymywany TESTEM (golden fixture),
NIE wspólnym importem. Każda zmiana reguły kolejności = zmień OBA bliźniaki.

trust_canon (2026-06-28): gdy ON i plan Ziomka pokrywa CAŁY worek → renderuj
kanon (courier_plans) VERBATIM przez `_canon_order_from_plan` = dokładnie to co
konsola (zawiera carried-first relax 22.06 „odbierz po drodze zanim dowieziesz
niesione"). Inaczej (flaga OFF / plan niepełny) → lokalne podjazdy carried-first.

Wierna ekstrakcja logiki z panelu `fleet_state` (2026-06-18). PURE — bez I/O,
bez OSRM, bez datetime.now → deterministyczne, łatwe do testów i identyczne
na obu powierzchniach. ETA / wrapping zostają per-powierzchnia (to prezentacja,
nie kolejność).

Reguła PODJAZDÓW: odbiory dzielone na kursy (kolejne zlecenia w oknie
≤PICKUP_MERGE_MIN min = jeden podjazd), w kursie odbiory grupowane po
restauracji, carried (picked_up) na początek; per kurs: WSZYSTKIE odbiory →
WSZYSTKIE dostawy (kolejność dostaw wg rangi planu Ziomka, inaczej wg czasu
odbioru). Minimalizuje powroty po jedzenie (R-NO-RETURN) i przeplot.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
PICKUP_MERGE_MIN = 10          # próg sklejania odbiorów w jeden podjazd (= fleet_state)
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


def _plan_pickup_clusters(plan_doc) -> dict:
    """{oid: (cluster_idx, pickup_rank)} dla ODBIORÓW z planu Ziomka. KOLEJNE odbiory
    (bez dostawy między nimi) = ten sam podjazd (cluster_idx). pickup_rank = pozycja
    odbioru w planie (do wiernej kolejności wewnątrz podjazdu). Pusty gdy brak planu.

    To jest sedno „podjazdów wg planu": Ziomek może świadomie zbundlować dwa odbiory
    (odbierz A, odbierz B, dowieź A, dowieź B) mimo że ich umówione czasy są >PICKUP_MERGE_MIN
    od siebie. Czysto-czasowe sklejanie rozbiłoby ten bundle na dwa kursy i wymusiło
    powrót po jedzenie. Tu czytamy intencję planu zamiast zgadywać z czasu."""
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


def pickup_runs(to_pick, plan_doc=None, plan_aware=False):
    """Podziel odbiory na PODJAZDY (kursy) + grupuj po restauracji wewnątrz kursu.
    Wejście/wyjście: listy zleceń (obiekty BagOrder-podobne albo dict-y).

    plan_aware + plan Ziomka pokrywa WSZYSTKIE odbiory worka → grupuj wg klastrów planu
    (odbiory które Ziomek skleja = jeden podjazd, niezależnie od progu czasowego), a w
    podjeździe kolejność = kolejność odbiorów w planie. Inaczej (brak/niepełny plan lub
    flaga OFF) → stary podział wg okna ≤PICKUP_MERGE_MIN."""
    clusters = _plan_pickup_clusters(plan_doc) if plan_aware else {}
    use_plan = bool(clusters) and all(str(_attr(o, "order_id")) in clusters for o in to_pick)
    if use_plan:
        groups: dict = {}
        for o in to_pick:
            cidx = clusters[str(_attr(o, "order_id"))][0]
            groups.setdefault(cidx, []).append(o)
        # podjazdy wg kolejności planu; w podjeździe odbiory wg pozycji odbioru w planie
        return [sorted(groups[c], key=lambda o: clusters[str(_attr(o, "order_id"))][1])
                for c in sorted(groups)]
    ordered = sorted(to_pick, key=lambda o: (_pickup_dt(o) or _SENTINEL, str(_attr(o, "order_id"))))
    runs = []
    prev = None
    for o in ordered:
        dt = _pickup_dt(o)
        if runs and prev is not None and dt is not None and (dt - prev) <= timedelta(minutes=PICKUP_MERGE_MIN):
            runs[-1].append(o)
        else:
            runs.append([o])
        if dt is not None:
            prev = dt
    out = []
    for run in runs:
        first_seen = {}
        for i, o in enumerate(run):
            first_seen.setdefault(_attr(o, "restaurant") or "", i)
        out.append(sorted(run, key=lambda o: (first_seen[_attr(o, "restaurant") or ""], _pickup_dt(o) or _SENTINEL)))
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
    `fleet_state._order_from_plan_seq`. Renderuje sekwencję planu verbatim:
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
                    _attr(by_oid[out[-1][1][-1]], "restaurant") == _attr(o, "restaurant"):
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
    trust_canon: gdy True i plan Ziomka pokrywa CAŁY worek → renderuj kanon
         (courier_plans) VERBATIM (lustro konsoli `_order_from_plan_seq`), z carried-first
         relaxem silnika. Inaczej → lokalne podjazdy carried-first (niżej). Flaga = rollback.
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
            rest = _attr(run[i], "restaurant")
            grp = [str(_attr(run[i], "order_id"))]
            i += 1
            while i < len(run) and _attr(run[i], "restaurant") == rest:
                grp.append(str(_attr(run[i], "order_id")))
                i += 1
            order.append(("pickup", grp))
        drops = sorted(run, key=_drop_key)
        order += [("dropoff", [str(_attr(o, "order_id"))]) for o in drops]
    return order
