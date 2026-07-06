# ADR-R02: Czysty rdzeń `decide(world) → Decision` + powłoka efektów (strangler, nie rewrite)

Status: proponowany (implementacja filaru F-2 kanonu zatwierdzonego 01.07)

## Kontekst
Decyzja zapada w `_assess_order_impl` (~3785 l., `dispatch_pipeline.py:3629`) splecionej ze środowiskiem: OSRM na żywo (4 punkty), flagi z dysku, HTTP fetch panelu w ocenie, shadow-writy i load-governor (plik+Telegram) w środku. Jednocześnie: stan wchodzi argumentami, `scoring`/`route_simulator_v2`/`objm_lexr6`/`tsp_solver` są JUŻ czyste, a efekty idą przez nazwane helpery (`_emit_*`, `_append_*`, `_loadgov_*`) — szwy do cięcia istnieją (raw/01b §5).

## Decyzja
Rdzeń wydzielany metodą stranglera, NIGDY big-bang:
1. Wejścia wstrzykiwane: `TravelTimeProvider` (macierz/route OSRM liczone w powłoce), FlagSnapshot (ADR-R01), `now` — docelowo jeden obiekt `WorldState` budowany raz na tick (F-1: flota z hierarchią źródeł pozycji, zlecenia, kalibracje ze świeżością).
2. Efekty NIE wykonywane w rdzeniu: decyzja zwraca `effects` (rekordy shadow, alerty, event-emity), powłoka wykonuje je PO rdzeniu.
3. Każdy krok wycinania za flagą z parytetem bajt-w-bajt starej i nowej ścieżki na golden corpusie (ADR-R04) + pełną regresją vs baseline; flaga OFF = stara ścieżka (rollback natychmiastowy).
4. Żywy HTTP fetch panelu w ocenie (`ENABLE_V327_PRE_PROPOSAL_RECHECK`) przenoszony do budowy WorldState (świeżość danych przed decyzją, nie w jej trakcie).
5. `Scorer` jako strategia wymienna (ADR-R06) — heurystyka domyślna, fallback zawsze heurystyczny.

## Konsekwencje
- Rdzeń testowalny deterministycznie (testy charakteryzujące realne, nie kontrfaktyczne); koszt przyszłych zmian silnika spada.
- Przez czas migracji żyją 2 ścieżki za flagą — koszt poznawczy; mitygacja: dziennik kroków `05-dziennik.md` + wpisy CODEMAP + zakaz >1 kroku „w locie" naraz.
- Zakaz nowych I/O w modułach rdzenia (ratchet lint/CI).
- Rdzeń NIE zmienia reguł biznesowych — bajt-parytet jest definicją ukończenia kroku (protokół #0: refaktor bez zmiany zachowania → dowód bajt-identyczności).

## Źródła
`ZIOMEK_ARCHITECTURE.md` §2 F-1/F-2; `02-diagnoza.md` D2; `raw/01b-rdzen.md` TOP-5; ADR-008 (rdzeń nie przenoszony między lane'ami — dyscyplina jednego właściciela obowiązuje też tu).
