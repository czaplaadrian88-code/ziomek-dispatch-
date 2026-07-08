# AUDYT 2.0 — Lane A: BLOKERY AUTONOMII (P1 przed 1. flipem) — RAPORT

**Data:** 2026-07-02 · **Branch:** `fix/auton-blockers` · **Commit:** `2998a29` (HEAD)
**Worktree:** `/root/.openclaw/workspace/wt-auton-blockers`
**Tryb:** BUDOWA (kod+testy, staged). ZERO flipów/restartów/push. Podmiana żywych plików = koordynator za ACK.
**Źródło findingu:** `eod_drafts/2026-07-02/AUDYT2/L05-bramki-wykluczen-autonomia.md` (P1 ×3).

---

## WERDYKT
Oba blokery domknięte u źródła + za istniejącą flagą `ENABLE_AUTO_ASSIGN` (OFF live → executor inert).
Blocker-1 dotyka WSPÓŁDZIELONEGO `gastro_assign.py` (LIVE: telegram + auto_koord) — fix **zachowuje
kontrakt „puste ciało = sukces"** (auto_koord 1057 parkowań, telegram realne przypisania), a wycina
tylko fałszywy sukces (HTML/logowanie/błąd). Regresja worktree: **4009 passed / 23 failed** (te same
23 znane artefakty path-layout `a2_selection`+`courier_reliability`; +39 = moje nowe testy). Zero nowych
awarii (passed 3970→4009 = dokładnie +39).

---

## BLOCKER 1 — fałszywy sukces `gastro_assign.py` (exit 0 mimo nieudanego przypisania)

### Mechanizm (plik:symbol + cytat audytu)
- `gastro_assign.py:206-209` (żywy) — warunek sukcesu:
  `if isinstance(result, dict) and (result.get('success') or result.get('status')=='ok' or 'error' not in str(result).lower())`.
  Człon **`'error' not in str(result).lower()`** przepuszcza KAŻDE ciało bez słowa „error": `{'raw':''}`
  (pusto), a także `{'raw':'<html>Sesja wygasła…</html>'}` (odbicie na logowanie po wygaśnięciu sesji) →
  drukuje `ASSIGN_OK`. Gałąź `else` „nieoczekiwana odpowiedź" (`:208-209`) **NIE robi `sys.exit(1)`** →
  proces kończy **exit 0**.
- `auto_assign_executor.py:160-163` (`_default_assign_runner`) + `auto_koord.py:147-154` +
  `telegram_approver.py:1987` — wszyscy trzej konsumenci ufają **wyłącznie `returncode==0`** i czytają stdout.
- Cytat L05 (dowód NA ŻYWO): *„`auto_koord_log.jsonl` … `Odpowiedź panelu: {'raw': ''}` (PUSTE ciało) →
  gastro drukuje `ASSIGN_OK`. … `{'raw':'<html>Sesja wygasła…</html>'}` (brak słowa „error") → ASSIGN_OK
  exit 0 = FAŁSZYWY SUKCES."* Skutek po flipie autonomii: `ok=True` → rate-cap zapisany, `AUTO_ASSIGN_EXECUTED`
  do learning_log, Telegram „✅ wykonane" — **przy niewykonanym przypisaniu = cichy drop zlecenia bez człowieka**.

### ⚠ Ograniczenie kompletności (twin-path — dlaczego NIE „usunąć empty=OK")
`gastro_assign.py` woła **3 konsumentów, 2 LIVE**: telegram (realne przypisania od miesięcy) i **auto_koord
(LIVE, `flags.json:23=true`, 1057 parkowań u Koordynatora, gdzie PUSTE ciało JEST sygnałem sukcesu panelu)**.
Naiwne „usuń `'error' not in`, wymagaj `success:true`" ZŁAMAŁOBY empty-body-success → regres LIVE.
Fix zachowuje empty=OK i wycina tylko realne strony błędu.

### Fix (STAGED `deploy_staging/scripts/gastro_assign.py`)
Mirror zsynchronizowany z żywym (ZoneInfo, md5 identyczny PRZED fixem), potem nałożony fix. Diff = **36 usuniętych /
136 dodanych linii**, WYŁĄCZNIE: docstring (banner STAGING), restrukturyzacja `main()`, klasyfikacja sukcesu.
Funkcje `_first_existing`/`login`/`get_kurier_id`/`assign` = **bajt-identyczne** (AST-parytet w teście).
1. `_classify_assign_response(result) -> (ok, detail)` — PUSTE ciało + JSON `success/status:ok` = SUKCES;
   `success:false`/`status:error/fail`/słowo error/exception/HTML/`<!doctype`/`name="_token"`/`zaloguj`/
   `/admin2017/login` = PORAŻKA. Zachowawczo: niepuste-nie-błędne-nie-HTML ciało zostaje OK (bez regresji
   nieznanych kształtów sukcesu). Kierunek **fail-closed** (fałszywe „nieudane" > fałszywe „udane").
2. Wszystkie porażki → `_err(...)` = `print(stderr) + return 1`; `main()` zwraca kod, `sys.exit(main())`.
3. `--verify` (opcjonalny) → `verify_assignment` = read-back `edit-zamowienie`, potwierdza `id_kurier==expected`.
   **auto_koord/telegram GO NIE przekazują → ich zachowanie NIEZMIENIONE.**
4. `ASSIGN_OK_SENTINEL = "ASSIGN_OK:"` drukowany tylko po potwierdzeniu (konsumowany przez executor).

### Strona executora (Blocker-1)
`_default_assign_runner`: sukces TYLKO gdy `returncode==0 ORAZ ASSIGN_OK: w stdout` (nie sam exit-code) +
przekazuje `--verify` + timeout 30→45 s (read-back round-trip).

---

## BLOCKER 2 — 1. flip nie jest no-op (TOCTOU / brak idempotencji / „stary sen")

### Mechanizm (plik:symbol + cytat audytu)
- L05 P1: *„pierwszy flip … odpali realny `gastro_assign` na ~1-2 zleceniach/dzień … strict `would_auto_assign=true`
  istnieje w ledgerze (10× w oknie 27.06→01.07)"* — po flipie ON decyzje policzone PRZED gotowością operatora
  odpalają się na 1. ticku.
- L05 P1 (idempotencja): *„bezpieczniki tylko GLOBALNE (rate-cap 6/h) + per-kurier cooldown. ZERO guardu per-order.
  … reconcile-lag panelu 15-90 s → to samo, wciąż-nieprzypisane zlecenie może dostać 2. event (inny event_id →
  dedup event_bus nie chroni) → drugi assign."*
- Okno TOCTOU: `maybe_execute` czyta flagę na wejściu (`:219`), potem I/O rate-cap/cooldown (odczyt plików,
  tail-scan) — flip→OFF w tym oknie NIE był re-sprawdzany przed `runner(...)`.

### Fix (`auto_assign_executor.py`, wszystko za `ENABLE_AUTO_ASSIGN` OFF)
1. **Atomowy re-check** `C.decision_flag("ENABLE_AUTO_ASSIGN")` TUŻ przed `runner(...)` (po całym I/O) →
   flip→OFF w oknie = `{"blocked":"flag_off_at_execution"}`, zero wykonania.
2. **Dry-first**: `_flags_recently_changed(now_ts, arm_delay)` — jeśli `flags.json` zmieniony w ostatnich
   `AUTO_ASSIGN_ARM_DELAY_SEC` (default 45 s) → `{"blocked":"dry_first_handshake"}` (log-only). Pierwszy tick po
   flipie OFF→ON (i po każdej zmianie configu) NIE wykonuje — decyzja „ze starego snu" sprzed flipu nie odpala się
   natychmiast; nadzorujący operator ma beat. **Wariant minimalny (UDOKUMENTOWANY):** semantyka time-based (okno po
   zmianie flags.json), NIE „dokładnie tick-2" — bo shadow tickuje per-event, nie stałym interwałem; okno 45 s
   gwarantuje ≥1 tick handshake przed 1. wykonaniem, jest stateless (zero nowej hydrauliki stanu) i pauzuje też przy
   każdej zmianie configu (bezpieczny efekt uboczny). `_flags_recently_changed` odmawia pod pytest bez allow-env
   (testy sterują deterministycznie, nie mtime współdzielonego pliku).
3. **Idempotencja per-order**: `assigned_orders {oid: ts}` w state (TTL `AUTO_ASSIGN_IDEMPOTENCY_TTL_SEC`=900 s).
   `_recent_auto_assign` blokuje 2. assign tego samego oid; `_record_auto_assign` zapisuje po sukcesie + przycina wygasłe.
4. Nowe pokrętła (env/flags-overridable **W MODULE executora** — `common.py` poza tym pasem): `_exec_numeric`.

---

## DOWODY (39 nowych testów, 100% green; regresja bez nowych awarii)

| Plik | Co dowodzi |
|---|---|
| `tests/test_gastro_assign_exitcode.py` (26) | klasyfikacja (empty=OK chroni twin / HTML=FAIL = THE BUG / json-error=FAIL); verify read-back (match/mismatch/none/exc); **behawioralne kody wyjścia main()** (session-bounce→exit1, empty→exit0+sentinel, verify-mismatch→exit1, verify-ok→exit0, kurier-not-found→exit1); **mutation ×2** (klasyfikator→always-True reintrodukuje fałszywy sukces; empty-polarity→flip łamie kontrakt twina); **parytet mirrora** (AST-identyczność funkcji nietkniętych + fix-present + konwergencja post-deploy) |
| `tests/test_auto_assign_toctou_2026_07_02.py` (13) | **TOCTOU** (flip→OFF w oknie I/O → block, runner NIE wołany); **dry-first** (recently-changed→block / po oknie→exec / mtime-unit / suppress-under-pytest); **idempotencja** (recent oid→block / expired→exec / record-on-success / prune); **sentinel** (exit0-bez-sentinela→fail / exit0+sentinel→ok / exit≠0→fail / cmd ma `--verify`); **mutation ×2** (usuń idempotencję→double-assign; sentinel=""→fałszywy sukces wraca); + 3 kontrakty ZACHOWANE (flag-off→None+zero-I/O, happy-path, rate-cap) na WORKTREE module |

**⚠ C12(e):** `auto_assign_executor.py` żyje W `dispatch_v2`, a konftest pinuje pakiet na KANON → testy blocker-2
ładują **worktree** kopię PO ŚCIEŻCE (inaczej testowałyby nieedytowany kanon). `gastro_assign` (staged, poza pakietem)
ładowany po ścieżce jak wzorzec grafik. sys.modules sprzątane try/finally.

**Regresja (worktree, `pytest tests/ -q`):** baseline `3970 passed / 23 failed` → po zmianie `4009 passed / 23 failed`
(26 skipped, 9 xfailed, 2 xpassed). Delta passed = +39 (dokładnie moje testy); failed BEZ ZMIAN (23 = te same znane
artefakty path-layout `a2_selection`(11)+`courier_reliability`(rest), NIE moje pliki). Zero nowych awarii.

---

## SEKWENCJA DEPLOYU (ZA ACK — koordynator, seryjnie)
Kolejność KRYTYCZNA (gastro PIERWSZE — bo executor `--verify` wymaga nowego gastro; a gastro to LIVE-współdzielony):

1. **KROK A — `gastro_assign.py` (LIVE-affecting: telegram + auto_koord).** To NAJRYZYKOWNIEJSZY krok (nie executor).
   - `cp /root/.openclaw/workspace/scripts/gastro_assign.py{,.bak-pre-auton-blockers-2026-07-02}`
   - `cp /root/.openclaw/workspace/wt-auton-blockers/deploy_staging/scripts/gastro_assign.py /root/.openclaw/workspace/scripts/gastro_assign.py`
   - `py_compile` + smoke: uruchom `gastro_assign.py --help` (arg-parse OK). **ZERO restartu** (subprocess per call).
   - Weryfikacja LIVE (obserwacja, nie akcja): 1. auto_koord parkowanie po deployu → `auto_koord_log.jsonl` dalej
     `success` (empty-body=OK zachowane); 1. realne przypisanie z Telegrama → dalej `ASSIGN_OK`. Jeśli pojawi się
     nowy `ASSIGN_ERROR: NIE potwierdzone` → to REALNA porażka wcześniej ukryta (dobrze), NIE regres — sprawdzić detail.
   - Rollback: `cp .bak-pre-auton-blockers-2026-07-02` → gastro_assign.py (natychmiast, następny subprocess czysty).
2. **KROK B — `auto_assign_executor.py` (INERT, `ENABLE_AUTO_ASSIGN` OFF).** Restart `dispatch-shadow` żeby wczytać
   (`.bak` → cp → py_compile → import → restart 1 serwis off-peak). Bez ryzyka: flaga OFF → `maybe_execute` return None.
   Rollback: `.bak` → restart.
3. **Testy kanoniczne po deployu:** `pytest tests/test_auto_assign_executor.py tests/test_auto_assign_gate.py
   tests/test_auto_koord.py tests/test_gastro_assign_exitcode.py tests/test_auto_assign_toctou_2026_07_02.py`
   (po merge do kanonu `dispatch_v2 == mój kod` → package-import testuje mój executor; parytet mirrora → diff==∅).
4. **NIE flipować `ENABLE_AUTO_ASSIGN`** tym deployem — to osobny, nadzorowany krok (patrz niżej).

## CO TO ODBLOKOWUJE (checklist przed 1. ON autonomii)
- [x] Blocker-1: executor/auto_koord/telegram NIE widzą już fałszywego sukcesu (sentinel + honest exit + read-back).
- [x] Blocker-2: 1. flip = dry-first handshake (nie odpala natychmiast) + idempotencja + atomowy re-check.
- [ ] **NADAL WYMAGANE przed 1. ON (poza tym pasem):** E2E kontrolowane przypisanie na żywym panelu (name→cid + read-back
  potwierdza `id_kurier`); monitor+stop-loss auto-assign (odpowiednik `carried_first_guard` — NIE istnieje); walidacja
  name→cid round-trip dla slice; 1. ON off-peak z Adrianem, `AUTO_ASSIGN_MAX_PER_HOUR=1`, profil STRICT.

## RYZYKA
- **KROK A = jedyny LIVE-ryzykowny.** Ryzyko teoretyczne: panel zwraca na realny sukces NIEPUSTE, nie-JSON ciało →
  klasyfikator dałby fałszywe „nieudane". Mitygacja: fail-closed jest bezpieczny (człowiek widzi+powtarza), a auto_koord
  ma 3× retry + always-propose + czasowka_scheduler redundancję. Nieznane kształty niepustego ciała zostają OK (zachowawczo).
- Executor `--verify` działa dopiero gdy nowy gastro jest w kanonie → kolejność A→B obowiązkowa (do B flaga i tak OFF).
- **N-D (inny pas, ZAKAZ):** `telegram_approver.run_gastro_assign` dalej ufa samemu returncode (bez sentinela) —
  gastro-fix czyni returncode UCZCIWYM (HTML→exit1), więc telegram też poprawnie failuje; docelowo tamten pas powinien
  dodać sentinel. `auto_koord.py` docstring „default False" ≠ LIVE — kosmetyka, nie tknięta (empty-path zachowany).

## ROLLBACK
- gastro: `cp .bak-pre-auton-blockers-2026-07-02` (natychmiast). executor: `.bak` + restart shadow. Kod: `git revert 2998a29`.
- Pokrętła: `AUTO_ASSIGN_ARM_DELAY_SEC=0` (wyłącza dry-first), `AUTO_ASSIGN_IDEMPOTENCY_TTL_SEC=0` (wyłącza idempotencję)
  w flags.json — hot, bez restartu.
