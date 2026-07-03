# Agent F — Inne projekty na serwerze (inwentaryzacja powierzchowna)

Data: 2026-07-03. Zakres: `/root/.openclaw/workspace/*` (poza `dispatch_v2` i `wt-*`), `/var/www/html`, oraz git-repa znalezione pod `/root` i `/var/www`. Audyt READ-ONLY, bez wchodzenia w głąb kodu — tylko README/CLAUDE.md/nazwy katalogów/`git log -1`/branch. To materiał wstępny, nie pełny audyt tych projektów.

**Uwaga metodologiczna:** kilka jednostek systemd (np. `dispatch-papu-bridge.service`) to SYMLINKI wskazujące na pliki wewnątrz repo projektu, nie samodzielne pliki w `/etc/systemd/system` — zwykłe `grep -r` je pomija (nie podąża za symlinkami przy rekursji), więc liczby niżej liczone `grep -R` (z dereferencją).

## Tabela projektów

| Ścieżka | Co to (1 zdanie) | Git (branch, remote) | Zależność od Ziomka | Jednostek systemd |
|---|---|---|---|---|
| `workspace/mailek` | Agent cold-mailingu B2B (leady dla Nadajesz.pl) | brak własnego `.git` — śledzony w repo-korzeniu workspace (branch `master`, remote `mailek.git`) | brak funkcjonalnej (parę wzmianek w docs/audytach, zero importów) | 2 |
| `workspace/scripts/` (luźne pliki + `flags.json` + `tools/` + `analysis/`) | Skrypty operacyjne Ziomka + **współdzielony `flags.json`** (źródło prawdy o flagach silnika) + stare jednorazowe narzędzia audytowe | ten sam repo co mailek | `flags.json` czytany/pisany też przez panel koordynatora (patrz sekcja relacji); `tools/`+`analysis/` = martwe, kwiecień 2026 | n/d (per plik) |
| `workspace/scripts/courier_api` | REST backend apki Android kuriera Nadajesz.pl | **WŁASNY** `.git` (branch `master`, remote `courier_api.git`) — osobne repo, nie mailek; pominięty przez pierwsze `find` (głębokość 5) | CIĘŻKA: czyta `dispatch_state` (plany/ETA/committed pickup), `gps_writer.py` pisze pozycję kuriera | 9 |
| `workspace/scripts/courier_api_panelsync` | Drugi wdrożony egzemplarz tego samego kodu (rola „panel sync") | **NIE osobne repo** — `.git` to plik-wskaźnik na git worktree `courier_api/.git/worktrees/courier_api_panelsync` | jak `courier_api` (ten sam kod/historia) | 1 |
| `workspace/scripts/papu_dispatch_bridge` | Most Lokalka(Papu) ↔ panel dyspozytorski Ziomka (`gastro.nadajesz.pl`), timer co 5 min | brak `.git` | WRITE: wstrzykuje zamówienia Papu jako nowe zlecenia do Ziomka; `bridge.py`/`config.py` odwołują się do `dispatch_v2` | 1 |
| `workspace/scripts/drtusz_bridge` | Most: nowe zlecenia 11 firm partnerskich (panel `nadajesz.pl/admin2017`) → panel dyspozytorski Ziomka | brak `.git` | jw., referencje `dispatch_v2` w `config.py` | 1 |
| `workspace/scripts/ml_data_prep` | Offline pipeline przygotowujący dane pod model LGBM Ranker (Fazy 2-5) | brak `.git` | README wprost: „Zero contact with live dispatch_v2 services" | 0 |
| `workspace/scripts/tools` + `workspace/scripts/analysis` | Stare jednorazowe skrypty audytowe/kalibracyjne (kwiecień 2026: `audit_dispatch_v2.py`, `canonicalize_restaurants.py`, `wave_chains.py` itd.) | brak `.git` | historyczne, brak śladu live użycia | 0 |
| `workspace/ordering_app` | Lokalka/Papu — backend B2C dostaw jedzenia (FastAPI + Postgres + Alembic) | **WŁASNY** `.git` (branch `master`, remote `podlaskie-papu-backend.git`); ostatni commit dziś | lekka: `slot_service.py`/`models/orders.py`/`api/internal/v1/dispatch.py` + skrypt telegram wspominają `dispatch_v2` — realny zapis robi most `papu_dispatch_bridge` | 24 |
| `workspace/nadajesz_clone` | Własny klon panelu dyspozytora/restauracji/kuriera — zamiennik zewnętrznego SaaS nadajesz.pl | **WŁASNY** `.git` (branch `coordinator-console`, remote `nadajesz.git`) | **NAJCIĘŻSZA**: `panel/backend/app/integrations/ziomek/*` (assign/adapter/fleet_state/route/committed_time/auto_assign_flag/coordinator_time_recheck/shadow_quote/courier_provision*/delivery_town/address_pin/parcel_dispatch_shadow/schedule_grid/courier_block) — czyta i PISZE `flags.json` oraz `dispatch_state/` | 27 |
| `/var/www/html` | Statyczny prototyp SPA B2C+B2B Lokalka (serwowany nginx) + katalog wdrożeniowy APK kuriera i buildu panelu admina | **WŁASNY** `.git` (branch `main`, remote `podlaskie-papu-prototype.git`) | brak bezpośrednich odniesień w kodzie | 0 |
| `/root/courier-app` | Natywna apka Android kuriera (Kotlin/Compose, pakiet `pl.nadajesz.courier`) | **WŁASNY** `.git` (branch `master`, remote `courier-app.git`) | koncepcyjna („trasa sterowana przez Ziomka") — brak importu kodu (inny język/runtime), kontrakt w `GRAFIK_API_CONTRACT_v1.md` | 0 |
| `/root/openclaw` | Framework/CLI agenta OpenClaw — narzędzie, nie projekt biznesowy | **WŁASNY** `.git`, **HEAD ODCZEPIONY** (detached @ `41cf93efff`, marzec 2026), remote `openclaw/openclaw.git` | brak | 2 |
| `workspace/{docs,memory,q_and_a,schemas,skills}` (korzeń workspace) | Relikty sprzed obecnego systemu pamięci: stare handovery F2.2/Fleet MVP (`docs/`, kwi-maj), dzienne logi sesji marzec-kwiecień (`memory/`), szablony multi-bota deepseek/nemo/scout/hub (`schemas/`, `skills/`) | część repo mailka (korzeń) | brak / czysto historyczne | n/d |

## Relacje: kto czyta/pisze dispatch_v2 lub dispatch_state

- **`nadajesz_clone/panel/backend`** — READ+WRITE najgłębsze: cały moduł `app/integrations/ziomek/` + bezpośredni dostęp do `flags.json` (`auto_assign_flag.py`, `api/coordinator.py`, `jobs/watcher.py`) i plików w `dispatch_state/`. To jest konsola koordynatora opisana w memory Ziomka.
- **`scripts/courier_api`** (+ worktree `courier_api_panelsync`) — READ `dispatch_state` (plany/ETA/committed pickup) do wyświetlenia trasy kurierowi; WRITE pozycji GPS (`gps_writer.py`), z której korzysta `courier_resolver`/last-known-pos w Ziomku.
- **`scripts/papu_dispatch_bridge`** — WRITE: wstrzykuje zamówienia z Lokalki jako nowe zlecenia do panelu gastro Ziomka (timer 5 min, oneshot).
- **`scripts/drtusz_bridge`** — WRITE: to samo dla 11 firm partnerskich przez panel `nadajesz.pl/admin2017`.
- **`ordering_app`** — pośrednio, przez most `papu_dispatch_bridge`; własny kod ma tylko wzmianki/dokumentację mostu, nie robi zapisu bezpośrednio.
- **`mailek`** — brak realnej relacji; „dispatch_v2"/„dispatch_state" pojawia się tylko w kilku starych plikach handover jako wzmianka historyczna, nie import.

## ⚠ DO WYJAŚNIENIA

- `scripts/tools` i `scripts/analysis` — czy ktoś jeszcze tego używa, czy do archiwizacji (brak commitów od kwietnia).
- `docs/`, `memory/`, `q_and_a/`, `schemas/`, `skills/` w korzeniu workspace — wyglądają jak relikty sprzed obecnego systemu pamięci (`/root/.claude/.../memory/`); bezpieczne do archiwizacji?
- `/root/openclaw` ma HEAD odczepiony na starym commicie z marca — celowe zamrożenie wersji narzędzia czy zapomniana gałąź?
- `courier_api_panelsync` jako git worktree tego samego repo co `courier_api` (własny `courier-panel-sync.service`) — czy nadal aktywnie potrzebny jako osobny wdrożony egzemplarz?
- Repo-korzeń workspace jest nazwane/zdalnie powiązane jako „mailek", ale fizycznie jest katalogiem-rodzicem dla `scripts/dispatch_v2` (osobne zagnieżdżone repo) i wielu mostów Ziomka — ryzyko pomyłki przy operacjach `git` wykonanych z korzenia workspace zamiast z właściwego repo.
