# AUDYT 2.0 — PAS L06: Konsola API (akcje koordynatora) + courier_api ops-internals

**Data:** 2026-07-02 · **Tryb:** READ-ONLY (zero zmian produkcji; jedyny zapis = ten plik) · **Lane:** L06 (PION 0.E — powierzchnie ZAPISU/producenci danych, poza inwentarzem 1.0)
**Zakres:** (1) warstwa API konsoli przyjmująca KLIKNIĘCIA koordynatora (`nadajesz_clone/panel/backend/app/api/{coordinator,ziomek,dispatch,fleet,parcel_ops,notify_feed}.py` + moduły `integrations/ziomek/*` które realnie piszą); (2) `courier_api` ops-internals (`schedule_escalation_cron`, `delivery_town`, `cost_calculator/aggregator`, `payment_override`, `gate_audit_poller`, `fleet_aggregator`).
**Pytania pasa:** walidacja wejścia · autoryzacja · idempotencja · czy akcja koordynatora może rozjechać stan silnika · co robią i jakie ryzyko wnoszą wewnętrzne procesy apki.

---

## 0. TL;DR (najważniejsze)

- **Architektura write-path jest ZDROWA, nie znalazłem P0/żywego pożaru.** Wszystkie akcje konsoli są SHADOW-FIRST (osobne flagi `COORDINATOR_*_LIVE`), audytowane (`coordinator_assign_audit.jsonl`), a przydział/edycja/anulowanie idą przez **gastro (źródło prawdy)** przez subprocess → silnik re-czyta z panelu → **brak bezpośredniego desyncu stanu silnika** (Z3 utrzymane). Autoryzacja stoi na JWT HS256 z egzekwowanym silnym sekretem (env=`staging`, boot-guard w `config.get_settings()`), Fernet odseparowany, CORS prod-only, panel na `127.0.0.1`. Subprocess = zawsze forma listy (brak shell-injection). Tokeny apki = `secrets.token_urlsafe(32)` + rate-limit PIN.
- **Najmocniejszy trop (P2):** przełącznik **AUTONOMII** Ziomka (`/api/coordinator/auto-assign` → `ENABLE_AUTO_ASSIGN`) jest **słabiej bramkowany niż start/stop mózgu** — tylko rola, BEZ PIN — mimo że to najbardziej konsekwentny „flip" w całym systemie (memory: 1. włączenie = NIEPRZETESTOWANE E2E, „musi być nadzorowane").
- **Świeży ground-truth (P2/P3):** 3 oneshoty w MOIM pasie mają **puste `OnFailure=`** (cicha awaria — dokładnie klasa `cod-weekly` z designu 2.0 §0.2e): `schedule-escalation`, `fleet-aggregator`, `fleet-cost-aggregator`.
- Reszta: brak rate-limitu na write-endpointach + subprocess-per-write (skala/perf), rozjazd dual-write paczki, twin-path `delivery_town`, kosmetyka `notify_feed`.

---

## 1. AUTORYZACJA / IDEMPOTENCJA — mapa write-endpointów konsoli

| Endpoint (coordinator.py) | Gate | LIVE za flagą | Idempotencja | Uwaga |
|---|---|---|---|---|
| `POST /assign` :181 | `ziomek_admin` | `COORDINATOR_ASSIGN_LIVE` | po stronie gastro (nadpisanie) | brak PIN (decyzja 19.06) |
| `POST /cancel` :282 | `ziomek_admin` | `COORDINATOR_CANCEL_LIVE` | status 9 idempotentny | „najryzykowniejsza ścieżka" wg docstringu |
| `POST /route` :306 | `ziomek_admin` | `COORDINATOR_PLAN_LIVE` | CAS w plan_manager | plan nadpisywany przez silnik (§5) |
| `POST /edit` :438 | `ziomek_admin` + `require_pin_when_live` **= no-op** | `COORDINATOR_EDIT_LIVE` | merge w gastro_edit | edytuje adres/COD/status/kuriera KAŻDEGO zlecenia floty |
| `POST /create` :722 | `ziomek_admin` | `DISPATCH_PUSH_LIVE` | brak (patrz niżej) | tworzy zlecenie w gastro |
| `POST /courier-block` :342 | `ziomek_admin` | LIVE zawsze (wewn. stan) | dedup w manual_overrides | HARD wykluczenie z puli |
| `POST /refresh-time` :391 | `ziomek_admin` | LIVE (kolejka) | kolejka+TTL po stronie silnika | dotyka committed `czas_kuriera` (R27) |
| `POST /auto-assign` :795 | `ziomek_admin` **(tylko rola)** | pisze `ENABLE_AUTO_ASSIGN` | flags_admin atomic | **KILLSWITCH autonomii — bez PIN** |
| `POST /ziomek/control` :765 | `ziomek_admin` **+ `require_pin`** | systemctl start/stop mózgu | — | jedyna akcja z PIN |

**Dowód no-op PIN:** `app/core/deps.py:105-115` — `require_pin_when_live` zwraca usera bez sprawdzenia (Adrian 26.06 „usuń pin ze wszystkiego"). `require_pin` (`deps.py:95-102`) to jedyny realny PIN (`verify_pin` w `security.py:54` = `bool(hashed) and _pwd.verify(...)` → null-hash odrzuca, brak bypassu).
**Boundary cross-tenant:** `require_roles("ziomek_admin")` (`deps.py:68`) — restauracja (owner/manager/staff) dostaje 403 na konsolę/feed nawet z tokenem. Poprawnie (twardy serwerowy boundary; docstring w `ziomek.py:20` mówi „demo=bearer", ale KOD jest bardziej restrykcyjny — nie-finding).

---

## 2. FINDINGI

### P2 — [F1] Przełącznik AUTONOMII słabiej bramkowany niż start/stop mózgu (asymetria)
**Powierzchnia:** `nadajesz_clone/panel/backend/app/api/coordinator.py:795-808` (`auto_assign_toggle`) vs `:765-777` (`ziomek_control`).
**Dowód:** `/auto-assign` POST ma tylko `user: CurrentUser = Depends(_OperatorOnly)` (=`require_roles("ziomek_admin")`), **brak** `Depends(require_pin)`. `/ziomek/control` (start/stop/restart mózgu) MA `_pin: CurrentUser = Depends(require_pin)`. Toggle deleguje do `auto_assign_flag.set_state` → `dispatch_v2.flags_admin set ENABLE_AUTO_ASSIGN true` (`integrations/ziomek/auto_assign_flag.py:63-88`), co odblokowuje egzekutor faktycznie sam przypisujący kuriera. Design 2.0 §2.F sam wskazuje ten przycisk jako „nową powierzchnię"; memory (AUTON-02): „PIERWSZE wciśnięcie »Włącz« = NIEPRZETESTOWANE E2E gastro_assign → MUSI być nadzorowane".
**Materialność:** nie policzone (kwestia blast-radius, nie częstotliwości). Pojedynczy POST z ważną sesją `ziomek_admin` przełącza silnik w tryb autonomiczny; brak PIN / typed-confirmation / server-side rate-cap na samym toggle. NIE jest to dziura eksploatowalna zdalnie (wymaga ważnej sesji operatora, API bearer więc CSRF n/d), a egzekutor ma WŁASNE bezpieczniki (rate-cap, cooldown po PANEL_OVERRIDE, bramka `would_auto_assign`) — dlatego P2, nie P1. Rzecz w **asymetrii**: mniej konsekwentna akcja (stop mózgu) ma PIN, bardziej konsekwentna (autonomia ON) nie.
**Rekomendacja:** dołożyć `require_pin` (lub jednorazowy typed-confirm) na `/auto-assign` przy przejściu na `enabled=True` (OFF może zostać bez PIN — killswitch w dół ma być łatwy); ewentualnie server-side jednokrotność/rate-cap. Zrównać z `/ziomek/control`.

### P2 — [F2] `schedule-escalation.service` bez `OnFailure` → cicha awaria eskalacji dyspozycji
**Powierzchnia:** `courier_api/schedule_escalation_cron.py` (unit `schedule-escalation.service`).
**Dowód (ground-truth 02.07 `systemctl show`):** `OnFailure=` **puste**, `Result=success`, timer `schedule-escalation.timer` active/enabled, service static/oneshot. Cron zwraca `return 1` gdy `ss._send_telegram(msg)` się nie powiedzie (`schedule_escalation_cron.py:49-51`) lub gdy `schedule_service.escalation_report()` rzuci — a **żaden alert nie poleci** (dokładnie klasa `cod-weekly FAILED+SILENT` z designu 2.0 §0.2e / ledger §3, gdzie identyczny wzorzec oznaczono P2-live).
**Materialność:** nie policzone (istnienie klasy). Funkcja wysyła koordynatorowi zbiorczą listę kurierów bez dyspozycji na kolejny okres (T-3) — jej cicha śmierć = kurierzy nie dostają fallbackowego przypomnienia (banner w apce dociera tylko do zaktualizowanych — patrz HONESTY w docstringu :10-12) → luki w grafiku/podaży. Nie-krytyczne dla pojedynczej decyzji, ale trwale degraduje podaż floty bez sygnału.
**Rekomendacja:** dodać drop-in `OnFailure=dispatch-onfailure-alert@schedule-escalation.service.service` (wzór, który MA `gate-audit.service`). Docelowo standard OnFailure dla WSZYSTKICH oneshotów (2.0 lane 2.G).

### P3 — [F3] `fleet-aggregator` i `fleet-cost-aggregator` bez `OnFailure` → cicha stagnacja analityki koszt/km/marża
**Powierzchnia:** `courier_api/fleet_aggregator.py` + `cost_aggregator.py` (unity `fleet-aggregator.service`, `fleet-cost-aggregator.service`).
**Dowód (ground-truth 02.07):** oba `OnFailure=` **puste**, timery active/enabled, service oneshot. `aggregate_day` łapie wyjątek → `error_msg` → `return 1` (`cost_aggregator.py:238-251`, `fleet_aggregator.py` analogicznie) bez alertu.
**Materialność:** nie policzone; wpływ = REPORTING (marża/koszt/km per zlecenie do finansów/FLT-04), nie ścieżka decyzyjna dispatchu. Cicha awaria = zamrożone `order_metrics`/`courier_daily_km` bez sygnału (klasa RC4/2.B).
**Rekomendacja:** ten sam drop-in `OnFailure` co F2; ewentualnie lekki „freshness" check (ostatni udany run w `aggregator_runs`).

### P3 — [F4] Brak rate-limitu na write-endpointach konsoli + subprocess-per-write (skala/perf)
**Powierzchnia:** `coordinator.py` (wszystkie POST) + `integrations/ziomek/assign.py:84`, `cancel_order:136`, `route.py:104`, `courier_block.py:124`, `coordinator_time_recheck.py:50`, `auto_assign_flag.py:38`.
**Dowód:** slowapi/limiter jest na `api/auth.py` (login) i `public_geo.py`, ale grep `coordinator.py` = **0** dekoratorów rate-limit. Każdy write = świeży `subprocess.run([ziomek_dispatch_python, ...])` w venv Ziomka; `assign_courier` loguje się do gastro PER wywołanie (brak reuse sesji CSRF między klikami), timeout 30 s (`settings.ziomek_assign_timeout_s`). Zimny import `dispatch_v2` / login gastro ~4-5 s (por. CLAUDE.md „panel_client login refresh ~6-7s blocking").
**Materialność:** nie policzone. Szybkie klikanie / bug frontu / wiele kart operatora → N równoległych ciężkich subprocessów (login gastro ×N) → wyczerpanie CPU/uchwytów i kolejkowanie. Nie żywy pożar (konsola = zaufany operator, mały ruch), ale koszt rośnie z wolumenem — zbieżne z regresją perf z designu 2.0 §0.2f.
**Rekomendacja:** rate-limit per-user na write-endpointach (np. `10/min`), rozważyć pulę/reuse sesji gastro zamiast login-per-assign.

### P3 — [F5] Rozjazd dual-write przy przydziale PACZKI (orders_state vs panel Delivery)
**Powierzchnia:** `coordinator.py:227-253` (`_assign_parcel`) + `integrations/ziomek/assign.py:161-201` (`assign_parcel`).
**Dowód:** `assign_parcel` pisze do `orders_state` przez subprocess `dispatch_v2.parcel_assign` (tor silnika), a POTEM `_assign_parcel` best-effort odzwierciedla status w panelu (`deliveries_svc.transition_status` → `assigned`) w `try/except` z `db.rollback()` przy błędzie (`:247-249`). Jeśli subprocess=ok+live, a mirror panelowy padnie → `orders_state=assigned`, ale panel `Delivery.status` zostaje `new/queued`.
**Materialność:** nie policzone; wpływ = tracking klienta/spójność snapshotu paczki (nowy tor, mały wolumen dziś). Fail-soft, udokumentowane, nie psuje rdzenia.
**Rekomendacja:** reconcile paczki (watcher orders_state→panel) albo traktować orders_state jako jedyne źródło i pochodnie panelu liczyć z niego (nie dual-write).

### P3 — [F6] `delivery_town` twin-path (courier_api ↔ panel) + trujący 365-dniowy cache przy 1 błędnym reverse-geocode
**Powierzchnia:** `courier_api/delivery_town.py` (bliźniak `panel/.../integrations/ziomek/delivery_town.py`), `town_label` :163-180.
**Dowód:** docstring :19-20 „Identyczny moduł żyje w panelu … współdzielą TYLKO plik cache (dane), nie kod (repo niezależne)" — jawny twin (klasa K7 dryf cross-repo z audytu 1.0). Separator różny z założenia (`_SEP="\n"` w apce vs `" "` w panelu). Cache: `_TTL_SEC = 365*86400`; przy sukcesie reverse-geocode zapisuje `_store(k, town)` (:179) — jeśli Google zwróci BŁĘDNĄ `locality` (np. przy granicy gmin), zła miejscowość utrwala się na rok.
**Materialność:** nie policzone; wpływ = DISPLAY (miejscowość na konsoli/apce), NIE decyzja (moduł jawnie read-only wobec orders_state, :9-11). Rozjazd = kosmetyka, ale myląca dla kuriera.
**Rekomendacja:** wspólny moduł/kontrakt zamiast 2 kopii (albo golden-test parytetu jak L6.A); krótszy TTL dla „miękkich" trafień + walidacja wyniku vs bbox Białegostoku.

### P3 — [F7] `notify_feed` cap „dismissed" po `set→list` (brak stabilnej kolejności) → wskrzeszanie usuniętych alertów
**Powierzchnia:** `nadajesz_clone/panel/backend/app/api/notify_feed.py:135-140` (`_save_dismissed`).
**Dowód:** `seq = list(ids)` na **secie**, potem `seq[-_MAX_DISMISSED:]` „zachowaj najnowsze 4000". Set w Pythonie nie gwarantuje kolejności wstawiania → „najnowsze" jest arbitralne; komentarz sam przyznaje „kolejność wstawiania nieistotna". Po przekroczeniu 4000 id część niedawno usuniętych może wypaść → alert wraca jako niewidziany.
**Materialność:** nie policzone; czysta kosmetyka feedu (żaden wpływ na dispatch). Wyzwala się dopiero >4000 dismissów.
**Rekomendacja:** trzymać dismissed jako listę/`OrderedDict` z realnym czasem albo (id→ts) i ucinać po ts.

### INFO — [F8] `/route` LIVE: ręczna kolejność nadpisywana przez działający silnik (ephemeral)
**Powierzchnia:** `integrations/ziomek/route.py:8-11` (docstring) + `save_route:93`.
**Dowód:** docstring wprost: „Gdy Ziomek (dispatch-shadow) działa, może nadpisać plan — narzędzie sensowne głównie w trybie ręcznym (Ziomek off)". Zapis przez `plan_manager.save_plan` (CAS), ale kolejny tick silnika przelicza plan.
**Materialność:** nie policzone; wpływ = UX/spójność (koordynator ustawia trasę, apka pokaże ją krótko, potem silnik zmieni). To NIE korupcja danych — silnik wygrywa (poprawnie). Udokumentowane.
**Rekomendacja:** UI powinno pokazywać „plan zostanie nadpisany dopóki Ziomek ON" (albo blokować `/route` gdy `dispatch-shadow` active).

### INFO — [F9] `gate_audit_poller`: reset `last=MAX(id)` po restarcie pomija zdarzenia z okna niedostępności; log bez rotacji
**Powierzchnia:** `courier_api/gate_audit_poller.py:96-100` (`last = MAX(id)`), `emit` :90-92 (append do `scripts/logs/gate_audit.log`).
**Dowód:** przy starcie ustala `last = COALESCE(MAX(id),0)` → zdarzenia status=3, które przyszły gdy poller był down, nigdy nie zostaną zaudytowane. Unit `gate-audit.service` = `Restart=always` + `OnFailure=dispatch-onfailure-alert@…` (ma alert — dobrze), ale audyt to observability, więc luka jest „miękka". `emit` = append-only bez logrotate (klasa RC4 unbounded).
**Materialność:** nie policzone; wpływ = kompletność audytu bramki 5-min (nie decyzja). Restart=always odzyskuje proces, gubi tylko okno.
**Rekomendacja:** persystować `last` (np. plik/tabela) i wznawiać od niego; objąć `gate_audit.log` logrotate.

### INFO — [F10] `payment_override`: kurier samodzielnie zmienia gotówka↔karta na WŁASNych zleceniach dnia (brak dual-control)
**Powierzchnia:** `courier_api/payment_override.py` + `courier_orders.py:600-649` (`payment_change_allowed`/`change_payment_method`) + endpoint `main.py:652-677`.
**Dowód:** autoryzacja solidna — `_require_session` (token), `payment_change_allowed` wymaga `courier_id` == zalogowany, status `picked_up` lub `delivered` z DZIŚ (:600-618), metoda z whitelisty `{karta,gotowka}` (:629), kwota z panelu (`pay.get("amount")`, NIE z inputu). `payment_override` = jedyny writer, PK=`order_id` (idempotentne). ALE: to samo-raportowanie kuriera bez drugiej pary oczu — oznaczenie zlecenia COD jako „karta" zmniejsza gotówkę, za którą kurier się rozlicza w dziennym rozliczeniu.
**Materialność:** nie policzone; dotyka PIENIĘDZY (COD/rozliczenie), ale jest to świadoma funkcja („kurier pobrał kartą zamiast gotówki") z audytem (`created_at`, `courier_id`) i bramką własnego-zlecenia-z-dziś. Ryzyko = zaufanie/fraud, nie bug.
**Rekomendacja:** rozważyć widoczność korekt w rozliczeniu koordynatora (flaga „skorygowano płatność" per kurier/dzień) + ewentualny cap dzienny/alert przy wielu korektach.

---

## 3. NIE-FINDINGI (zweryfikowane, żeby nie ścigać duchów)

- **Desync stanu silnika przez akcję koordynatora — NIE dla toru gastro.** `/assign,/cancel,/edit,/create` piszą do **gastro (źródło prawdy)** przez subprocess; silnik re-czyta z panelu (panel_watcher). Rozjazd możliwy tylko w torze PACZKI (F5, dual-write) i w `/route` (F8, silnik wygrywa). Fail-closed: bogus `order_id` → gastro_assign nie zwraca `ASSIGN_OK` → `ok=False`.
- **`time_arg` „HH:MM" vs minuty — obsłużone.** `gastro_assign.py:151-170` auto-wykrywa `":" → HH:MM` (przelicza na minuty od teraz) lub `int(minuty)`; błąd → `ASSIGN_ERROR` (fail-closed). Brak rozjazdu czasu odbioru z tego tytułu.
- **Shell-injection — brak.** Wszystkie subprocess = forma listy (`subprocess.run([...])`), wartości jako argv. Nazwa kuriera w `--kurier <courier>` to co najwyżej argument-injection teoretyczny, ale argparse konsumuje ją jako wartość → w najgorszym razie assign FAIL (bezpiecznie). `courier_block` przekazuje NAZWĘ rozwiązaną z `cid` (lookup w kurier_ids/courier_names), nie surowy input.
- **JWT / sekrety — posture OK.** `PANEL_ENVIRONMENT=staging` (∉ `_DEV_ENVS`) → boot-guard `config.py:411-423` wymusza nie-domyślny `PANEL_JWT_SECRET` + `PANEL_FERNET_KEY` (panel wstał → sekrety ustawione). HS256 ze stałą listą algorytmów (`security.py:77`), Fernet odseparowany od JWT, CORS prod-only (`effective_cors_origins`), `PANEL_DEBUG=0`, uvicorn na `127.0.0.1:8000` za nginx. `reserve-internal` (`fleet.py:194`) fail-closed przy pustym `internal_token`.
- **`ziomek_control` przez root.** `nadajesz-panel.service` biegnie jako `User=root` (systemctl bez sudo) — świadome (DEFER przeniesienia usera w unicie); akcja PIN-gated + localhost. Odnotowane jako kontekst, nie osobny finding (należy do 2.F/2.G).

---

## 4. CO ZOSTAŁO POZA MOIM ZASIĘGIEM (uczciwe luki)

- **`coordinator_time_recheck` → committed `czas_kuriera` (R27) bez strażnika** — z poziomu API-konsoli powierzchnia jest cienka i bezpieczna (rola+audyt, tylko enqueue oid). RYZYKO leży DOWNSTREAM (panel_watcher `deliberate=True` omija anty-migotanie) — to znany trop designu 2.0 §0.2d.3 / rodzina L3, NIE mój pas. Odnotowuję atrybucję, nie dubluję.
- Nie odpalałem endpointów (read-only, prod) — autoryzacja/idempotencja oceniane z lektury kodu + ground-truth `systemctl show`. Materialność „ile/dzień" dla F1/F4-F10 = nie policzona (istnienie/klasa), bo brak metryki wolumenu klików w ledgerze; F2/F3 = ground-truth stanu jednostek 02.07 (dryfuje).
- `cost_calculator`/`cost_aggregator`/`fleet_aggregator` przejrzane pod kątem write/idempotencji (UPSERT per klucz, RO-connection do analityki, grace-cap 4h dla open-orderów) — to analityka, nie ścieżka decyzyjna; głębszej walidacji poprawności km_share/marży NIE robiłem (poza pasem).
