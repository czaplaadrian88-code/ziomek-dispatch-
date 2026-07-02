# FALA 1 — cod-weekly-diag: diagnoza + fix U ŹRÓDŁA (finding B audytu 2.0)

**Lane:** cod-weekly-diag | **Branch:** `fix/cod-weekly-diag` | **Data:** 2026-07-02
**Commity (na branchu, do scalenia przez koordynatora):**
- `0346a9a` fix(cod-weekly): actionable brak-bloku + auto-create tygodnia za flagą OFF (cod_weekly/config.py, sheet_writer.py, run_weekly.py)
- `7afc431` fix(cod-weekly): testy brak-bloku + mutation-check (tests/test_cod_weekly_missing_block.py)
- (ten raport) — osobny commit

Baza HEAD przed pracą: `c6e2c13`.

---

## 1. DIAGNOZA GROUND-TRUTH (READ-ONLY)

Źródło: `journalctl -u dispatch-cod-weekly` + `logs/cod_weekly.log` (żywy system, tylko odczyt).

### Werdykt vs hipoteza audytu
Hipoteza audytu 2.0 ("pada na braku bloku tygodnia w arkuszu") — **POTWIERDZONA** jako dominujący
wyzwalacz. **Doprecyzowanie (2 korekty):**

1. **NIE ma surowego tracebacka.** Moduł już teraz kończy się czysto: `log.error("TARGET COLUMN
   FAIL: ...")` + `return 1`. Objaw "SILENT" był na poziomie systemd/notyfikacji, nie stacktrace.
   Dodatkowo ścieżka `find_target`-fail w `cmd_write` wysyłała tylko jednolinijkowy alert
   `[COD WEEKLY ALERT] Target column fail: ...` (mało aktionable), a przy czystym braku bloku
   część poniedziałków nie zostawiała nawet czytelnego telegramu — stąd wrażenie "cichego" faila.

2. **Są DWIE ścieżki błędu** (log to jednoznacznie pokazuje):
   - **(A) brak bloku** — `find_target: candidates=[]` → `NoTargetColumnError` → exit 1. Dominująca.
   - **(B) blok istnieje ale kolumna JUŻ WYPEŁNIONA** — `empty_check FAIL: ratio=0%` → exit 1
     (dotyczy 1 tygodnia, 22.06 kol. CV; to NIE utrata — dane tam były).

### Dokładnie który krok pada
`run_weekly.cmd_write` → `find_target_cod_columns_resilient` → `sheet_writer.find_target_cod_columns`
(payday-match) i fallback `find_target_column_auto` (range-match). Gdy OBA zwracają 0 kandydatów
(bo w arkuszu 'Wynagrodzenia Gastro' nie ma bloku danego tygodnia = ręcznie nie dodano payday+zakres)
→ `NoTargetColumnError` → `cmd_write` łapie → exit 1. Zapis COD NIE rusza.

### Które tygodnie przepadły (z logu — co zrobił SYSTEM)
Serwis w poniedziałek liczy POPRZEDNI zamknięty tydzień (pon-niedz).

| Poniedziałek (run) | Tydzień | Payday | Wynik w logu | Status |
|---|---|---|---|---|
| 2026-05-25 | 2026-05-18..05-24 | 27-05-2026 | candidates=[] → TARGET COLUMN FAIL | **PRZEPADŁ** |
| 2026-06-01 | 2026-05-25..05-31 | 03-06-2026 | candidates=['CI'] → Written 69/69 | OK (wpisany) |
| 2026-06-08 | 2026-06-01..06-07 | 10-06-2026 | candidates=[] → TARGET COLUMN FAIL | **PRZEPADŁ** |
| 2026-06-15 | 2026-06-08..06-14 | 17-06-2026 | candidates=[] → TARGET COLUMN FAIL | **PRZEPADŁ** |
| 2026-06-22 | 2026-06-15..06-21 | 24-06-2026 | candidates=['CV'] ale empty_check FAIL 0% | już WYPEŁNIONY (weryfikować, prawdop. NIE utrata) |
| 2026-06-29 | 2026-06-22..06-28 | 01-07-2026 | candidates=[] + auto-detect=[] → FAIL | **PRZEPADŁ** |

**Cztery tygodnie DEFINITYWNIE bez auto-zapisu** (kandydaci do backfillu): 18-24.05, 01-07.06,
08-14.06, 22-28.06. Piąty (15-21.06, kol. CV) był już wypełniony — do WERYFIKACJI, nie liczę jako
utrata. (Audyt mówił "≥2"; log pokazuje 4 czyste utraty + 1 do weryfikacji.)

**Uwaga:** log dowodzi tylko że SYSTEM nie wpisał tych tygodni. Czy ktoś dopisał je ręcznie później
(jak zrobiono dla CV) — musi potwierdzić odczyt arkusza (krok 1 backfillu, patrz §4). Świadomie NIE
czytałem żywego arkusza (unik jakiegokolwiek efektu ubocznego na arkuszu-pieniądzach); i tak
koordynator/Adrian weryfikują go przed backfillem (operacja pieniężna za ACK).

---

## 2. ZMIANY (U ŹRÓDŁA, partycja cod_weekly/**)

**`cod_weekly/config.py`** — flagi ENV czytane per-wywołanie (wzorzec drop-inów systemd, nie flags.json):
- `autocreate_block_enabled()` ← `COD_WEEKLY_AUTOCREATE_BLOCK` (default **OFF**)
- `autocreate_block_dry_run()` ← `COD_WEEKLY_AUTOCREATE_DRY_RUN` (default OFF)
- `BLOCK_ROW2_HEADERS` — kanoniczne nagłówki bloku.

**`cod_weekly/sheet_writer.py`** — mechaniczne tworzenie bloku (blok = deterministyczne 4 kolumny):
- `build_week_block_plan()` — liczy (BEZ zapisu) strukturę bloku/bloków do dopisania NA KOŃCU
  (za całą treścią, content-based detekcja go znajdzie). Zwraca też `new_row1/new_row2` do retry.
- `ensure_week_block(dry_run)` — dopisuje blok (row1 payday+zakres, row2 nagłówki) 1× `batch_update`.
  **NIE dotyka danych COD** — kolumny wartości zostają PUSTE → empty_check przejdzie, normalny zapis
  wypełni je świeżo. Split-month → 2 bloki (wspólny payday, różne zakresy).

**`cod_weekly/run_weekly.py`** — `cmd_write` ścieżka błędu przebudowana:
- Rozdzielone `NoTargetColumnError` (brak bloku) od `AmbiguousTargetError/ValueError` (struktura
  niejednoznaczna). **Ambiguous NIGDY nie auto-tworzy** (dodałby kolejny blok → wymaga człowieka).
- **(2a)** przy braku bloku: pełna, AKTIONABLE instrukcja (arkusz, komórki payday+zakres, nagłówki
  row2 — reużyta z preflight `_build_preflight_instruction`) + komenda backfillu, zamiast gołego
  "Target column fail". `exit 1` ZOSTAJE (OnFailure/staleness ma się na nim oprzeć).
- **(2b)** auto-create ZA FLAGĄ: `COD_WEEKLY_AUTOCREATE_BLOCK=1` → tworzy blok, retry find_target na
  zaktualizowanych wierszach (bez 2. round-tripu API), kontynuuje zapis. `..._DRY_RUN=1` → pokazuje
  CO by utworzył (log + alert), NIC nie zapisuje, exit 1.
- **(2c)** brak cichych częściowych sukcesów — zachowane z istniejącego designu (per-segment status;
  exit 1 gdy wszystkie segmenty failed; PARTIAL alert gdy część; write exception → głośno + exit 1).
  Auto-create też fail-loud: błąd tworzenia / retry-fail → alert + exit 1.

**Domyślnie (flaga OFF) produkcja zmienia się TYLKO tym, że alert braku-bloku jest teraz aktionable.**
Zero auto-zapisu do arkusza dopóki koordynator+Adrian nie flipną flagi.

---

## 3. DOWODY

- **py_compile** 3 plików: OK w obu venvach (dispatch + sheets).
- **Nowe testy** `tests/test_cod_weekly_missing_block.py` — **8/8 PASS** w dispatch-venv
  (`/root/.openclaw/venvs/dispatch/bin/python -m pytest`). Test ładuje 3 zmienione pliki WPROST z
  worktree (harness importuje `dispatch_v2` z KANONU) + wstrzykuje fake gspread/google.oauth2 tylko
  na czas importu i przywraca sys.modules → zero wycieku, `importorskip` innych testów dalej skipuje
  (slice cod_weekly: 18 passed / 5 skipped — bez kontaminacji).
  - flag OFF → exit 1 + alert zawiera "Akcja Rafał", payday, zakres, nagłówki, komendę `--week ... --write`
  - flag ON → `ws.batch_update` 1×, COD dopisany do nowej kolumny AU, exit 0
  - **flag ON≠OFF udowodnione** (ten sam scenariusz: OFF nie tworzy/nie zapisuje, ON tworzy+zapisuje)
  - dry-run → `batch_update` NIE wołany, alert "DRY-RUN", exit 1
  - Ambiguous + flag ON → `batch_update` NIE wołany (nigdy auto-create)
  - split-month → 2 bloki (wspólny payday 06-05-2026, różne zakresy)
  - **MUTATION-CHECK (C13):** zmutowana detekcja braku bloku (`find_target` zwraca [] zamiast
    `raise NoTargetColumnError`) → ścieżka aktionable "Akcja Rafał" ZNIKA. Test to wychwytuje
    (`assert real_actionable and not mutated_actionable`) → PASS = dowód, że test faktycznie testuje
    detekcję, nie przechodzi pusto.
- **Pełna regresja** `cd wt-cod && pytest tests/`:
  - Z moim testem: **23 failed, 3694 passed**, 23 skipped, 11 xfailed
  - BEZ mojego testu (czysty aktualny kanon): **23 failed, 3686 passed**
  - **Delta mojej pracy = +8 passed, +0 failed.** ZERO nowych FAILi.
  - **23 faile są PRE-EXISTING dryfem współdzielonego kanonu** (`scripts/dispatch_v2` ruszył od
    nocnego baseline 3709/0): `test_a2_selection_shadow.py` (15) + `test_courier_reliability.py` (8)
    — oba szukają modułów `tools/*.py` pod nieistniejącą ścieżką `/root/.openclaw/workspace/dispatch_v2/tools/`
    (WIP innych lane'ów jeszcze nie w kanonie). **Poza moją partycją, nie ode mnie** (padają też solo,
    bez mojego kodu). Pre-existing gspread-guarded testy dalej SKIPUJĄ (nie faile).

---

## 4. DEPLOY ZA ACK (wykonuje koordynator/Adrian — pieniądze!)

**A) Sam fix (bez auto-create) — bezpieczny, zalecany od razu po merge.**
Merge brancha `fix/cod-weekly-diag` do kanonu → następny poniedziałek (2026-07-06 08:00 Warsaw)
łapie kod. Efekt przy dalszym braku bloku: exit 1 (jak dziś, OnFailure działa) + aktionable alert.
Zero zmiany zachowania zapisu. Restart serwisu niepotrzebny (oneshot, świeży proces per tick).

**B) Auto-create (flip flagi) — osobny ACK, off-peak.**
Drop-in `/etc/systemd/system/dispatch-cod-weekly.service.d/autocreate.conf`:
```
[Service]
Environment=COD_WEEKLY_AUTOCREATE_BLOCK=1
```
Zalecany 1. krok — PODGLĄD (nic nie pisze): dodać też `Environment=COD_WEEKLY_AUTOCREATE_DRY_RUN=1`,
`systemctl daemon-reload`, uruchomić raz ręcznie, sprawdzić w logu blok jaki BY utworzył; potem
zdjąć DRY_RUN. ⚠ Limitacja: gdy w arkuszu istnieje blok NIEPEŁNY (same nagłówki row2 bez payday/
zakres), auto-create dopisze NOWY kompletny blok obok (osierocony nagłówek zostaje, nieszkodliwy) —
stąd DRY-RUN + rzut oka Rafała przed flipem.

**C) BACKFILL przepadłych tygodni (dokument — WYKONANIE za ACK Adriana, pieniądze!).**
Moduł UMIE liczyć zaległe tygodnie — parametr `--week YYYY-MM-DD:YYYY-MM-DD` (pon:niedz; waliduje 7
dni i poniedziałek). NOWY parametr NIE jest potrzebny (byłby dublem). Entrypoint prod:
`/root/.openclaw/venvs/sheets/bin/python3 -m dispatch_v2.cod_weekly.run_weekly` z
`WorkingDirectory=/root/.openclaw/workspace/scripts`.

Procedura (chronologicznie, najstarszy pierwszy — żeby kolumny dopisywały się w kolejności):
1. **WERYFIKACJA arkusza (READ):** potwierdzić które z 4 tygodni są NADAL puste (część mogła być
   dopisana ręcznie, jak CV). Dla każdego: sprawdzić czy jest blok z danym payday i czy kolumna
   'COD - Transport' pusta.
2. Dla każdego POTWIERDZONEGO pustego tygodnia — jeśli używamy auto-create, najpierw PODGLĄD:
   ```
   COD_WEEKLY_AUTOCREATE_BLOCK=1 COD_WEEKLY_AUTOCREATE_DRY_RUN=1 \
     /root/.openclaw/venvs/sheets/bin/python3 -m dispatch_v2.cod_weekly.run_weekly \
     --week 2026-05-18:2026-05-24 --write
   ```
   potem realny zapis (zdjąć DRY_RUN):
   ```
   COD_WEEKLY_AUTOCREATE_BLOCK=1 \
     /root/.openclaw/venvs/sheets/bin/python3 -m dispatch_v2.cod_weekly.run_weekly \
     --week 2026-05-18:2026-05-24 --write
   ```
   Kolejno: `2026-05-18:2026-05-24`, `2026-06-01:2026-06-07`, `2026-06-08:2026-06-14`,
   `2026-06-22:2026-06-28`.
   (Alternatywa bez auto-create: Rafał dodaje blok ręcznie wg alertu, potem `--week ... --write`.)
3. **Weryfikacja po:** telegram raportu "Wpisano X/69" per tydzień + rzut oka na kolumnę w arkuszu.
   Ponowny run tego samego tygodnia = exit 1 empty_check (już wypełniony) = sygnał "gotowe", NIE błąd
   (skip-already-filled chroni przed dublem).
- **15-21.06 (CV):** NIE backfillować bez weryfikacji — było już wypełnione.

---

## 5. ROLLBACK

- **Auto-create:** zdjąć drop-in / `Environment=COD_WEEKLY_AUTOCREATE_BLOCK=0` + `daemon-reload`
  (hot — oneshot bierze świeży env per tick; bez restartu długo-żyjącego procesu). Default OFF =
  natychmiastowy powrót do zachowania "tylko aktionable alert".
- **Cały fix:** `git revert 7afc431 0346a9a` na branchu (lub nie-merge). Kod ensure_week_block jest
  martwy dopóki flaga OFF, więc revert bezpieczny w każdym momencie.
- Auto-create pisze WYŁĄCZNIE nagłówki bloku (row1/row2), nigdy danych COD → nawet po flipie żaden
  rollback nie dotyka pieniędzy.

---

## 6. POZA PARTYCJĄ / NIE ROBIONE (świadomie)

- **OnFailure / rejestracja cron_health dla dispatch-cod-weekly** — robi lane **watchdog-close**
  (nie dotykałem `observability/cron_health.py` ani `/etc`). Mój fix trzyma `exit != 0` przy
  braku/niejednoznaczności bloku, więc ich OnFailure ma się na czym oprzeć.
- **23 pre-existing faile kanonu** (test_a2_selection_shadow, test_courier_reliability) — dryf
  współdzielonego repo / WIP innych lane'ów; nie moja partycja.
- **Odczyt/zapis żywego arkusza** — świadomie 0 (diagnoza z logu wystarczyła; backfill = koordynator
  za ACK).
- Zero: systemctl/daemon-reload, flags.json, /etc, Telegram, git push, commit na master, pip install.
