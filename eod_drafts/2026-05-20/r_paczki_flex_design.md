# R-PACZKI-FLEX — design spec (2026-05-20)

**Goal:** zlecenia z 6 kont firmowych = paczki (nie jedzenie); luźne okno czasowe (soft cap 2h pickup / 3h delivery), gradient penalty zamiast hard reject R-35MIN-MAX. Wyjątek: czasówki trzymają konkretną porę.

**Adrian source:** message 2026-05-20 + AskUserQuestion clarifications (start clock = pojawienie w panelu gastro, soft cap gradient, firmowe Nadajesz.pl WŁĄCZONE jako paczka).

**Sprint scope:** 5 firm bridge (Dr Tusz/Dentomax/3Giga/Orthdruk/Interpap) + 1 firmowe Nadajesz.pl (`address_id=161`). Po flipie suppress firmowych — Ziomek będzie też proponował kuriera dla firmowych nadajesz.pl (dziś manual przez Adriana).

---

## 1. Klasyfikator

**Identyfikator paczki:** `address_id ∈ PACZKA_ADDRESS_IDS = frozenset({161, 232, 233, 234, 235, 236})`.

Empirycznie zweryfikowane (events.db, 2026-05-20): orders z drtusz_bridge mają `address_id` równy `restaurant_id` z bridge config, firmowe Nadajesz.pl ma `address_id=161` (52 ordery w ostatnich 1000 NEW_ORDER events).

**Helper (common.py):**
```python
PACZKA_ADDRESS_IDS = frozenset({161, 232, 233, 234, 235, 236})

def is_paczka_order(order_dict) -> bool:
    aid = order_dict.get("address_id")
    try:
        return int(aid) in PACZKA_ADDRESS_IDS
    except (TypeError, ValueError):
        return False
```

Fail-safe: corrupt/None aid → False (jedzeniówka, surowy R-35MIN-MAX apply — bezpieczne).

**Czasówka exception:** jeśli `order_dict.get("order_type") == "czasowka"` (prep_minutes ≥ 60) → paczka NIE dostaje flex (czasówka trzyma konkretną porę, R-DECLARED-TIME nadrzędne). Helper `is_paczka_flex_eligible(order)`:
```python
def is_paczka_flex_eligible(order_dict) -> bool:
    return is_paczka_order(order_dict) and order_dict.get("order_type") != "czasowka"
```

---

## 2. Konstanty (common.py)

```python
# R-PACZKI-FLEX — 2026-05-20 sprint
PACZKA_ADDRESS_IDS = frozenset({161, 232, 233, 234, 235, 236})
PACZKA_PICKUP_SOFT_CAP_MIN = 120.0   # 2h od pojawienia w gastro
PACZKA_DELIVERY_SOFT_CAP_MIN = 180.0 # 3h od pojawienia w gastro
PACZKA_FLEX_PENALTY_PER_MIN = 1.0    # liniowy; -60 punktów przy +60min nad cap

ENABLE_R_PACZKI_FLEX = _os.environ.get("ENABLE_R_PACZKI_FLEX", "0") == "1"
```

**Flag default OFF** (env+`flags.json`), shadow mode 24h obs przed flip True.

**Start clock = pojawienie w panelu gastro** = `created_at_utc` z `normalize_order` (panel zwraca UTC). Konwersja Warsaw lokalnie do liczenia overrun.

---

## 3. Gates — 4 zmiany

### 3a. feasibility_v2.py:642 — R-35MIN-MAX bypass

Obecnie:
```python
if bag_time_min > C.BAG_TIME_HARD_MAX_MIN:
    # hard reject
```

Po fixie:
```python
_bypass = (
    C.ENABLE_R_PACZKI_FLEX
    and C.is_paczka_flex_eligible(order_dict)
    and not _bag_has_food_mate(bag)
)
if bag_time_min > C.BAG_TIME_HARD_MAX_MIN and not _bypass:
    # hard reject (jedzeniówka rządzi nawet z paczką w bagu)
```

**Bag-mate rule:** paczka + jedzeniówka w bagu → 35min hard apply (jedzeniówka się psuje). Helper `_bag_has_food_mate(bag) -> bool` iteruje po bag entries i sprawdza czy któraś nie jest paczką.

### 3b. feasibility_v2.py:654 — soft 30-35min zone bypass

Identyczny mechanizm: jeśli `_bypass`, skip soft penalty zone.

### 3c. sla_tracker.py — BAG_TIME alert suppress

`ENABLE_BAG_TIME_ALERTS` default False w prod (sprint 07.05), ale gdy ktoś flipnie True → suppress dla paczek. Defense w `_check_bag_time_alerts` (lub funkcji analogicznej).

### 3d. dispatch_pipeline.py — nowy bonus_r_paczki_flex w scoring

Nowa funkcja:
```python
def _r_paczki_flex_penalty(order, now_warsaw, eta_pickup_warsaw, eta_delivery_warsaw):
    """Liniowa kara powyżej soft caps; -1 punkt/min."""
    if not (C.ENABLE_R_PACZKI_FLEX and C.is_paczka_flex_eligible(order)):
        return 0.0
    created_warsaw = _gastro_created_at_warsaw(order)
    if created_warsaw is None:
        return 0.0
    pickup_overrun = max(0.0, (eta_pickup_warsaw - created_warsaw).total_seconds()/60 - C.PACZKA_PICKUP_SOFT_CAP_MIN)
    deliv_overrun = max(0.0, (eta_delivery_warsaw - created_warsaw).total_seconds()/60 - C.PACZKA_DELIVERY_SOFT_CAP_MIN)
    return -(pickup_overrun + deliv_overrun) * C.PACZKA_FLEX_PENALTY_PER_MIN
```

Wynik dodany do `bonus_penalty_sum` (linia 2307) jako nowa kolumna `bonus_r_paczki_flex`. Try/except wrapper z fallback 0.0 + warning log (Lekcja #32 fail-loud, Lekcja #83 defense-in-depth).

---

## 4. Suppress flip — firmowe Nadajesz.pl (address_id=161)

Sprint 07.05 dodał suppress dla firmowych w 4 miejscach:
- `shadow_dispatcher.py:740-763` (verdict SUPPRESSED_FIRMOWE_KONTO przed shadow log)
- `telegram_approver.py:1838-1851` (proposal_sender skip)
- `czasowka_scheduler.py:266-281` + `:523-531` (KOORD alert suppress)
- `dispatch_pipeline.py:1204` (komentarz, no-op)

**Zmiana wzorca:** w każdym z tych miejsc dodać `and not C.is_paczka_flex_eligible(order)` do warunku suppress. Czyli: firmowe NIE-paczka NIE-flex (corner case np. czasówka 161) nadal suppress; firmowe = paczka = flex → ENABLE propose.

**Defense:** gdy `ENABLE_R_PACZKI_FLEX=False` (flag default) → semantyka 100% stara, zero zmian observowalnych.

---

## 5. Encoding checklist (Lekcja #54 obligatory)

| Warstwa | Plik | Zmiana |
|---|---|---|
| Kod | common.py | konstanty + 2 helpery |
| Kod | feasibility_v2.py:642,654 | bypass R6 hard+soft z bag-mate guard |
| Kod | sla_tracker.py | BAG_TIME alert suppress for paczki |
| Kod | dispatch_pipeline.py | _r_paczki_flex_penalty + dodanie do bonus_penalty_sum |
| Kod | shadow_dispatcher.py | flip suppress dla firmowych |
| Kod | telegram_approver.py | flip suppress dla firmowych |
| Kod | czasowka_scheduler.py | flip KOORD suppress dla firmowych |
| Tests | tests/test_r_paczki_flex.py | 25+ testów (klasyfikator + 3 czasówka + 6 bypass + 3 bag-mate + 5 gradient + 3 suppress flip) |
| Shadow serializer A | shadow_dispatcher.py _serialize_candidate | `is_paczka`, `paczka_pickup_overrun_min`, `paczka_delivery_overrun_min`, `bonus_r_paczki_flex`, `paczka_flex_bypass_reason` |
| Shadow serializer B | shadow_dispatcher.py inline best | te same 5 pól |
| Auto prop prefixes | shadow_dispatcher.py `_AUTO_PROP_PREFIXES` | dodaj `paczka_`, `r_paczki_` |
| Learning analyzer | learning_analyzer.py readers | (deferred — pole emit'ne, reader auto-pick przez `_AUTO_PROP_PREFIXES`) |
| Dashboard | (deferred do Etap 2) | nowa kolumna w view; tymczasowo pole w raw log |

---

## 6. Flags & deploy plan

### Stan flag (default po sprintu)

```json
{
  "ENABLE_R_PACZKI_FLEX": false
}
```

`common.py` env-overridable `ENABLE_R_PACZKI_FLEX=0/1`.

### Deploy etapy

1. **Etap 0** (this sprint) — kod LIVE z flag OFF (default). Tests PASS. Restart dispatch-shadow.
2. **Etap 1** — flip `ENABLE_R_PACZKI_FLEX=true` w `flags.json` (hot-reload, no restart). 24h shadow obs. Sprawdź: paczki bridge dostają propozycje (poprzednio R6 reject), firmowe Nadajesz.pl mają propozycje (poprzednio suppress), gradient action właściwy (over-cap penalty widoczny w log).
3. **Etap 2** (opcjonalnie, post-obs) — calibracja `PACZKA_FLEX_PENALTY_PER_MIN` jeśli za miękki/twardy.

### Rollback procedury

**Per-flag (5s, hot-reload):**
```bash
python3 -c "import json,os,tempfile; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['ENABLE_R_PACZKI_FLEX']=False; fd,t=tempfile.mkstemp(dir=os.path.dirname(p)); open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False)); os.replace(t,p)"
```

**Hard (git revert):**
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
git revert <sprint-commit> --no-edit
sudo systemctl restart dispatch-shadow
# dispatch-telegram WYMAGA Adrian explicit ACK
```

### Backups (24h retention)

- `common.py.bak-pre-r-paczki-flex-2026-05-20`
- `feasibility_v2.py.bak-pre-r-paczki-flex-2026-05-20`
- `sla_tracker.py.bak-pre-r-paczki-flex-2026-05-20`
- `dispatch_pipeline.py.bak-pre-r-paczki-flex-2026-05-20`
- `shadow_dispatcher.py.bak-pre-r-paczki-flex-2026-05-20`
- `telegram_approver.py.bak-pre-r-paczki-flex-2026-05-20`
- `czasowka_scheduler.py.bak-pre-r-paczki-flex-2026-05-20`
- `flags.json.bak-pre-r-paczki-flex-2026-05-20`

---

## 7. Test plan (test_r_paczki_flex.py)

| # | Group | Test |
|---|---|---|
| 1-6 | klasyfikator | 6× happy path per aid (161/232/233/234/235/236) |
| 7-9 | klasyfikator negative | food rid (=190), None aid, corrupt aid "abc" |
| 10-13 | czasowka exception | paczka+czasówka NIE flex (per aid) |
| 14-19 | bypass R-35MIN-MAX | 5 firm + firmowe nadajesz, bag_time=45min → feasible (flag ON) |
| 20-22 | bag-mate | paczka+food → 35min hard apply; paczka+paczka → flex; food only → unchanged |
| 23-27 | gradient | under cap=0, exact cap=0, over cap=neg liniowy, both pickup+delivery, 4h+ extreme |
| 28-30 | suppress flip | flag OFF → firmowe suppress (stary code path); flag ON+paczka → propose; flag ON+not_paczka → nieaplikowalne (only aid=161) |
| 31-32 | defense edge | flag ON + corrupt order → fallback safe; missing created_at → penalty=0 |

Cel: **30+ testów PASS**, regresja istniejących test_v327*, test_feasibility*, test_shadow_serializer* — 0 nowych FAIL.

---

## 8. ACK gate Adrian

Przed Fazą 2 (AIDER):
- Spec design (ten plik) ✓
- Lista 6 address_id zweryfikowana empirycznie ✓
- Klasyfikator/gradient/bypass/suppress sprawdzone w kontekście istniejącego kodu ✓
- Bag-mate rule decyzja: paczka+jedzeniówka → 35min hard (jedzeniówka rządzi)
- Czasówka exception: czasówka-paczka NIE flex (R-DECLARED-TIME rządzi)
- Flag default OFF, shadow mode

**Pending Adrian ACK na cały spec — czy ruszamy implementacją (Faza 2 AIDER ~60min)?**
