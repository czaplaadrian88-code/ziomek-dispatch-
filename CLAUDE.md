# CLAUDE.md — Ziomek V3.1 (dispatch_v2)

> **PIERWSZA INSTRUKCJA:** Zanim odpowiesz na JAKIEKOLWIEK pytanie albo napiszesz JAKIKOLWIEK kod, przeczytaj w tej kolejności wszystkie 4 pliki:
> 1. `docs/SKILL.md` (master V3.1 — architektura, 29 reguł, 12 faz)
> 2. `docs/TECH_DEBT.md` (bieżący stan, bugs, odłożone rzeczy)
> 3. `docs/FAZA_0_SPRINT.md` (plan dzisiejszy)
> 4. `docs/SYSTEM_FLOW.md` (flow end-to-end zlecenia)
>
> Następnie wykonaj rytuał startowy (sanity check produkcji). Dopiero potem proś o zgodę na działanie.

---

## Kontekst w 3 akapitach

**Adrian** jest ownerem **NadajeSz Białystok** — firma kurierska gastro-delivery. 30 kurierów, 55 restauracji, 1500-2000 orderów/tydz, revenue ~35-45k PLN/tydz transport + 70-90k GMV. Plan: ekspansja Warszawa + API dla aggregatorów (Restimo → Wolt Drive clone).

**Ziomek** to autonomiczny AI dispatcher który zastępuje ręczną pracę koordynatora. Decyzja per order: który kurier, jakim bagiem, w jakiej kolejności dostaw. Shadow mode (propozycje w Telegramie, Adrian akceptuje → Ziomek imituje koordynatora = sam loguje się do panelu i przypisuje) do uzyskania API Rutcom, potem autonomia.

**Ty (Claude Code)** pomagasz Adrianowi pisać, patchować i testować kod **na żywym serwerze produkcyjnym**. Pracujemy iteracyjnie: diagnoza → patch → test → restart. Zawsze backupy, zawsze testy przed restartem produkcji. Celem jest system **najsolidniejszy jakościowo, pancerny, skalowalny na wielomiastowość** — nie półśrodki, nie hacki, nie TODO bez kontekstu.

---

## Zasady współpracy (HARD)

- **Polski, konkretnie, bez lania wody.** Adrian nie znosi ogólnych zdań typu "powinniśmy rozważyć".
- **"Pytaj nie zgaduj"** — każde zgadywanie to 10-30 min debugu. Sprawdzaj realny kod (`grep`, `cat`, `sed -n`) przed patchem. Nie pisz funkcji z pamięci.
- **Weryfikuj empirycznie** — zanim napiszesz kod, sprawdź sygnatury, strukturę danych, zawartość plików.
- **Patchowanie małymi krokami:**
  1. `cp plik.py plik.py.bak-$(date +%Y%m%d-%H%M%S)`
  2. Python `str.replace` z `assert old in s` (nie `sed`, nie heredoki nad 50 linii)
  3. `python3 -m py_compile plik.py`
  4. Import check + test funkcjonalny
  5. Diff check (`diff bak current`)
  6. Restart serwisu **TYLKO** jeśli wymaga — i **TYLKO po zgodzie Adriana**
- **Dry-run z mockami przed restartem produkcji** — zawsze.
- **Tryb komunikacji:** krótko. Update "✅ X done, ⏳ Y w trakcie, szacunek N min". Długie analizy TYLKO przy realnych wątpliwościach architektonicznych.
- **Traktuj Adriana jak przedsiębiorcę** — patrz przez pryzmat przychodu, marży, wykonalności, prostoty wdrożenia, przewagi rynkowej. 2-5 najmocniejszych opcji zamiast 20 luźnych pomysłów.
- **Filozofia jakości:** najsolidniejsza opcja, pancerna, skalowalna na wielomiastowość. Nigdy hacki ani TODO bez kontekstu. Jeśli coś brzmi jak "na teraz" — zapisz w TECH_DEBT zamiast komentarza w kodzie.

## Hard NEVER

- Nie ruszaj produkcji bez `cp .bak-*` + testu
- Nie restartuj `systemd` bez py_compile + import check + **zgody Adriana**
- Nie reintroducuj `chromedp` — Python HTTP zostaje (D15 V3.1)
- Nie każ kurierowi czekać (D8) — R27 pickup window ±5 min
- Nie licz SLA od `now` dla assigned (D4) — od `picked_up_at`
- Nie używaj `jq` (brak w systemie) — użyj Python do JSON
- Nie dodawaj `tools.telegram`/`tools.exec.approval` do `openclaw.json` (crash gateway)
- Nie zapominaj Warsaw TZ dla panel timestamps
- Nie hardcoduj MAX_BAG_SIZE — zostało ODRZUCONE (zobacz stan P0.1 niżej)
- Nie zawieszaj orderów — zawsze best-effort + alert (R29)
- Nie implementuj premium SLA dzisiaj (D13 — odroczone do października 2026, jednolite SLA=35 min)
- Nie zaniżaj ETA kuriera dla restauracji "bo się spóźniają" — D16: mierzymy spóźnienia dla alertów, ale możemy wysłać kuriera +prep_variance min później (ochrona D8)

## Hard ALWAYS

- Czytaj SKILL.md + TECH_DEBT + FAZA_0_SPRINT + SYSTEM_FLOW na start
- Weryfikuj realny kod przed patchem
- Atomic writes: temp → fsync → rename
- Warsaw TZ: `from zoneinfo import ZoneInfo; WARSAW = ZoneInfo("Europe/Warsaw")`
- Update TECH_DEBT na koniec sesji
- Każda akceptacja Adriana w Telegramie = Ziomek loguje się i przypisuje sam (D15)

---

## STAN FAZY 0 (12.04.2026)

### ✅ P0.1 DONE — Dynamic MAX_BAG_SIZE (przeformułowane)

**Co zrobione:**
- `common.py` → `MAX_BAG_TSP_BRUTEFORCE = 5` + `MAX_BAG_SANITY_CAP = 8` (techniczne guardy, NIE biznesowe reguły)
- `scoring.py` i `feasibility.py` importują z `common` (jedno źródło prawdy)
- Stare `MAX_BAG_SIZE = 4` (scoring) i `= 6` (feasibility) USUNIĘTE
- `s_obciazenie` przepisane na linear decay: bag 0=100, 1=80, 2=60, 3=40, 4=20, 5+=0 (zamiast hard cutoff na 4)

**Filozofia (zaktualizowana podczas P0.1):**
> Wave size NIE jest biznesową regułą per tier. Wave size wynika z:
> - SLA 35 min per order (feasibility + TSP simulation)
> - Traffic multiplier (traffic.py)
> - Kurier + aktualna mapa
>
> Tier wpływa tylko na **scoring** (Faza 2 tier multiplier), NIE na hard limits.

**R3 V3.1 "hard cap 4 per tier" został ODRZUCONY** jako sprzeczny z D8 ("kurier zawsze w ruchu") i z obserwacji Adriana że Mateusz O w niedzielny peak (puste ulice) może mieć 5 orderów jeśli SLA się zmieści. **SKILL.md sekcja R3 wymaga aktualizacji na końcu dnia** → TECH_DEBT.

**Backup:** `*.bak-20260412-1020*`

### ✅ P0.2a DONE — time_penalty D4 (przez state_machine, nie scoring)

**Co zrobione:**
- `state_machine.py` → nowa funkcja `compute_oldest_picked_up_age_min(bag, now_utc)`
- Filtruje TYLKO `status == "picked_up"` (ordery `assigned` nie liczą się do SLA kuriera)
- Explicit `now_utc` parametr (caller musi podać, zero defaults) → deterministyczna, replay-able
- Parsuje 3 formaty timestampów (datetime/ISO/naive Warsaw panel)
- Raises ValueError dla `None` i naive datetime (fail fast)

**Decyzja architektoniczna:** scoring pozostaje **pure math** bez znajomości statusów/dat. Separacja odpowiedzialności pod wielomiastowość. Shadow dispatcher (Faza 1) woła `state_machine.compute_oldest_picked_up_age_min` PRZED `scoring.score_candidate`.

**Testy:** 12/12 PASS (pusty bag, tylko assigned, mixed, panel Warsaw, datetime obj, broken data, garbage input, determinism, Warsaw-ready caller)

**Backup:** `state_machine.py.bak-20260412-*`

### ⏸ P0.2b (R27 window) — ODROCZONE do Fazy 1

R27 wymaga `simulate_bag_route` zwracającego `predicted_arrival_at_pickup`. Obecny `route_simulator.py` v1 idzie do kosza (→ `route_simulator_v2` PDP-TSP w Fazie 1). Pisanie R27 dla v1 = martwy kod.

### ⏳ P0.3-P0.8 DO ZROBIENIA (kolejność)

- **P0.3** `courier_resolver.py` priority bug (PILNE, ~25 min)
- **P0.4** `panel_watcher.py` pickup_coords null → inline geocoding + centroid fallback (~25 min)
- **P0.5** OSRM haversine fallback × 1.4 / 25 km/h (~40 min)
- **P0.6** Recon: co panel zwraca w `fetch_order_details` dla `prep_ready_at`? (~30 min)
- **P0.7** `gap_fill_restaurant_meta.py` — filozofia D16 (alerty biznesowe, NIE bufory) (~5h)
- **P0.8** Meta integration w `route_simulator_v2` (inline do Fazy 1)

---

## Rytuał startowy (odpal to PIERWSZE)

```bash
# 1. Sanity check serwisów
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker

# 2. Tail logów (ostatnie 3 linie każdy)
tail -3 /root/.openclaw/workspace/scripts/logs/watcher.log
tail -3 /root/.openclaw/workspace/scripts/logs/sla_tracker.log

# 3. State summary
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import state_machine
from collections import Counter
all_o = state_machine.get_all()
c = Counter(o.get('status','?') for o in all_o.values())
pu = sum(1 for o in all_o.values() if o.get('picked_up_at'))
print(f'State: {dict(c)}, with picked_up_at: {pu}')
"

# 4. Fleet snapshot
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import courier_resolver
fleet = courier_resolver.build_fleet_snapshot()
disp = courier_resolver.dispatchable_fleet(fleet)
print(f'Fleet: {len(fleet)}, dispatchable: {len(disp)}')
"

# 5. Recent shadow/learning (jeśli istnieją, po Fazie 1)
tail -5 /root/.openclaw/workspace/dispatch_state/shadow_decisions.jsonl 2>/dev/null
tail -5 /root/.openclaw/workspace/dispatch_state/learning_log.jsonl 2>/dev/null
```

Dopiero po tym pytaj: **"Adrian, lecimy P0.3?"**

---

## Runtime i dostęp

| Element | Wartość |
|---------|---------|
| Serwer | Hetzner CPX22, Ubuntu 24.04 |
| IP | 178.104.104.138 (UTC) |
| Runtime | OpenClaw 2026.3.27 w Docker |
| Kontener główny | `openclaw-openclaw-gateway-1` |
| Model AI | `openai/gpt-5.4-mini` primary, DeepSeek fallback |
| Telegram admin Adrian ID | 8765130486 |
| Bot dispatch | `@NadajeszBot` |
| Bot sterowanie | `@GastroBot` |
| Panel | `gastro.nadajesz.pl` (PHP/Laravel, agencja Rutcom) |
| Login sekrety | `/root/.openclaw/workspace/.secrets/panel.env` |

## Timezone (KRYTYCZNE)

- **Serwer = UTC**. Logi, `events.db`, `created_at` — wszystko UTC.
- **Panel używa Warsaw TZ** dla pól widocznych (`czas_odbioru_timestamp`, `dzien_odbioru`, `czas_doreczenia`). Format naive: `"2026-04-11 18:01:47"` (bez tzinfo, interpretować jako Warsaw).
- W kodzie: `from zoneinfo import ZoneInfo; WARSAW = ZoneInfo("Europe/Warsaw")`.
- Wielomiastowość (Warszawa) = ta sama TZ, zero refactoru. Ekspansja EU = refactor per city TZ (TECH_DEBT P2.3).

## Mapping statusów (`id_status_zamowienia`)

| ID | Status | Opis |
|----|--------|------|
| 2 | new | nowe, nieprzypisane |
| 3 | assigned | przypisane do kuriera |
| 5 | picked_up | kurier odebrał z restauracji |
| 7 | delivered | dostarczone |
| 8 | undelivered | cancelled by kurier |
| 9 | cancelled | anulowane |

`id_kurier = 26` = **Koordynator** (wirtualny bucket dla czasówek).

---

## Struktura katalogu
dispatch_v2/
├── CLAUDE.md                 ← JESTEŚ TU
├── docs/
│   ├── SKILL.md              ← master V3.1 (CZYTAJ PIERWSZY)
│   ├── TECH_DEBT.md
│   ├── FAZA_0_SPRINT.md
│   └── SYSTEM_FLOW.md
├── common.py                 ← ✅ P0.1
├── scoring.py                ← ✅ P0.1 (pure math)
├── feasibility.py            ← ✅ P0.1 (v1 do kosza w Fazie 1)
├── state_machine.py          ← ✅ P0.2a
├── courier_resolver.py       ← ⏳ P0.3
├── panel_watcher.py          ← ⏳ P0.4
├── osrm_client.py            ← ⏳ P0.5
├── route_simulator.py        ← 🗑️ v1, do zastąpienia w Fazie 1
├── geocoding.py
├── geometry.py
├── traffic.py
├── event_bus.py
├── .bak-                   ← backupy (ignoruj w searchu)
└── pycache/

## Sekrety i logi

- Sekrety: `/root/.openclaw/workspace/.secrets/` (panel.env, gmaps.env, traccar.env)
- Logi: `/root/.openclaw/workspace/scripts/logs/` (watcher.log, sla_tracker.log, sla_log.jsonl)
- State: `/root/.openclaw/workspace/dispatch_state/` (orders_state.json, events.db, geocode_cache.json, restaurant_coords.json, restaurant_meta.json, kurier_piny.json)

## Systemd units aktywne

- `dispatch-panel-watcher.service` — 20s cycle ✅
- `dispatch-sla-tracker.service` — 10s cycle ✅

---

## Przy każdej zmianie aktualizuj

1. **TECH_DEBT.md** — co odłożone, dlaczego, priorytet P0/P1/P2
2. **CLAUDE.md sekcja STAN FAZY 0** — dopisz kolejny ✅ Pn.X DONE z szczegółami
3. **Git (jeszcze nie zainicjowany, TECH_DEBT P2)** — na razie backupy `.bak-TIMESTAMP`

## Eskalacja do Adriana (Telegram: 8765130486)

- Production down
- Ziomek zaproponował absurd (guaranteed SLA violation)
- Data quality alarm (>10 orderów >90 min w dniu)
- Critical partner SLA spadł >10pp w 3 dni
- Fleet utilization <60% przez >4h
- Agreement rate <60% przez 3 dni (Ziomek przegrywa z intuicją = problem scoringu)
- **Każda prośba o restart systemd** — Adrian akceptuje LUB odrzuca
