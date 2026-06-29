# ZOMBIE-01 — diagnoza KOMPLETNA + patch gotowy. IMPLEMENTACJA ODŁOŻONA (kolizja multi-session 06-08)

## ⚠ DLACZEGO ODŁOŻONE
courier_resolver.py edytowany RÓWNOLEGLE przez inną sesję CC (147 niezacommitowanych
wstawień: feature "Persistent last-known-position store FIX 2026-06-08" — `_load/_save/_rescue
_last_known_pos`, COURIER_LAST_POS_PATH). Edycja pliku z żywym cudzym WIP = ryzyko zacommitowania
ich półproduktu / konflikt ([[feedback-multisession-shared-deploy]]). APLIKOWAĆ DOPIERO gdy ich WIP
zacommitowany (sprawdź `git status courier_resolver.py` = clean lub ich commit w logu).

## DIAGNOZA (zweryfikowana w kodzie — gotowa)
Audyt ZOMBIE-01 [P2, conf=low]: oid=476621 carry 1463min (24h) w c2_shadow_log 29-05.
- **Realny dispatch bag CHRONIONY** przez `_bag_not_stale@90min` (STRICT_BAG_RECONCILIATION=1, live)
  — agresywniej niż proponowane przez audyt 3h. picked_up>90min → filtrowany.
- **LUKA STRUKTURALNA (potwierdzona)**: `_bag_not_stale` dla `status==assigned` używa `updated_at`
  (świeży → KEEP), NIE konsultuje `picked_up_at`. Ale route_simulator (`_compute_per_order_delivery
  _minutes`:658) ORAZ feasibility R6 (`feasibility_v2.py`:832) liczą `is_picked = picked_up_at is not
  None OR status=='picked_up'` → anchor=picked_up_at. Order z status=assigned + ZACHOWANYM starym
  picked_up_at: filtr KEEP, ale anchor=stary picked_up_at → elapsed 1463min.
- **REALNY IMPACT (zweryfikowany)**: zombie klasyfikowany jako `is_picked` → trafia do
  `r6_picked_up_violations` (TRACKED, nie hard-reject — `feasibility_v2.py`:869-870). NIE wywala
  bezwarunkowego `r6_per_order_violations` rejecta. ALE ustawia `r6_max_bag_time_min=1463`
  (`feasibility_v2.py`:862-863) → **zatruwa metrykę carry używaną w scoringu** (carry penalty) +
  C2 shadow stats + ryzyko P3-D4 delta-reject. Zgodne z audytem „zatruwa statystyki, mógł zatruć propozycje".
- conf realnego skutku: rzadkie (Path B V3.27.5 preservuje picked_up status → zwykle nie ma flapu
  do assigned; zombie wymaga anomalii: status flap bez czyszczenia picked_up_at, manual panel edit,
  albo saved-plan path). oid=476621 już sprzątnięty z orders_state.

## FIX (root-cause, przy źródle = bag): rozszerz `_bag_not_stale`
Order z `picked_up_at` starszym niż próg = ghost NIEZALEŻNIE od statusu → STALE. Wstaw PO bloku
parse-warn `pickup_at_warsaw` (po `_bag_not_stale._warned_pu = seen`), PRZED `# Timestamp wyboru per status`:

```python
    # ZOMBIE-01 (audyt 2026-06-03): order z `picked_up_at` starszym niż próg = ghost
    # NIEZALEŻNIE od statusu. Luka: status=assigned z zachowanym (starym) picked_up_at
    # przechodził filtr (gałąź assigned używa updated_at), ale route_simulator/feasibility
    # anchorują elapsed na picked_up_at (is_picked) → absurd carry (476621: 1463min)
    # zatruwa r6_max_bag_time (scoring) + C2 + per-order. Filtr przy źródle; ten sam próg.
    if flag("ENABLE_ZOMBIE_PICKUP_AT_GUARD", default=True):
        _pu_ghost = order.get("picked_up_at")
        if _pu_ghost:
            try:
                _pu_dt = datetime.fromisoformat(str(_pu_ghost).replace("Z", "+00:00"))
                if _pu_dt.tzinfo is None:
                    _pu_dt = _pu_dt.replace(tzinfo=timezone.utc)
                if (now_utc - _pu_dt).total_seconds() / 60.0 > _threshold:
                    _zseen = getattr(_bag_not_stale, "_warned_zombie", set())
                    _zoid = str(order.get("order_id") or "?")
                    if _zoid not in _zseen and len(_zseen) < 50:
                        _log.warning(
                            f"ZOMBIE_PICKUP_GUARD oid={_zoid} status={status} picked_up_at "
                            f">{_threshold}min → STALE (ghost: odebrane dawno, nie domknięte)")
                        _zseen.add(_zoid); _bag_not_stale._warned_zombie = _zseen
                    return False
            except Exception:
                pass
```
+ flaga `ENABLE_ZOMBIE_PICKUP_AT_GUARD: true` w flags.json (+comment). Test: test_zombie01_pickup_guard
(assigned+stary picked_up_at→stale / picked_up świeży→keep / assigned bez picked_up_at→keep /
flaga OFF→keep). Regresja: konsumenci _bag_not_stale (bag_integrity/fail02/fail05). Zmiana KODU →
restart dispatch-shadow/panel-watcher. Backup `.bak-pre-zombie01-pickup-guard-2026-06-08` JUŻ zrobiony.

## Alternatywa (jeśli wolisz NIE filtrować z bagu)
Cap absurd elapsed w `_compute_per_order_delivery_minutes` + R6 (anchor>threshold → traktuj jak
brak picked_up_at / pomiń z carry). Gorzej: stop zostaje w geometrii TSP. Fix przy bagu czystszy.
