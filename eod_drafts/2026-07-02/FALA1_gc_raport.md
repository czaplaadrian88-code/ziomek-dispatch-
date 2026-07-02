# FALA 1 / lane gc-observability — RAPORT (audyt 2.0 L13)

**Branch:** `fix/gc-observability` (worktree `wt-gc`, HEAD wyjściowy c6e2c13).
**Data:** 2026-07-02. **Tryb:** produkcja PRZYGOTOWANA, ZERO deployu/kasowania na żywo.
**Partycja:** `observability/log_rotation.py` (NOWY) + `tests/test_log_rotation.py` (NOWY) +
`deploy_staging/` + `eod_drafts/2026-07-02/FALA1_gc_eventsdb_plan.md` + ten raport. Zero cudzych plików.

---

## 1. STAN ZASTANY (pomiary READ-ONLY)

**(a) GC observability = ATRAPA — potwierdzone + DOPRECYZOWANE.**
- `log_rotation.py` NIE istniał (`find / -name` pusto). Był **USUNIĘTY 2026-06-11** (commit b39e928)
  z założeniem „rotacja = systemowy logrotate" — `tests/test_observability.py:8` to zapisuje.
- To założenie BŁĘDNE: logrotate rotuje STAŁE nazwy wg rozmiaru; pliki DATOWANE
  `candidate_decisions_YYYYMMDD.jsonl` / `fleet_filter_YYYYMMDD.jsonl` (każda doba = nowa nazwa)
  są poza jego modelem. Komentarz `/etc/logrotate.d/dispatch-v2:113-116` JAWNIE deleguje je do
  `log_rotation.py` → kontrakt-widmo.
- Zmierzony katalog `/root/.openclaw/workspace/dispatch_state/observability`: **326-327 MB / 120 plików**
  (60× candidate_decisions + 60× fleet_filter), daty **2026-05-04 → 2026-07-02**, **88 starszych niż 14 dni**.
  Prefiksy wprost z `candidate_logger.py:27-28`. Tempo ~18 MB/dobę.

**(c) events.db.** `/root/.openclaw/workspace/dispatch_state/events.db`: **30,17 MiB** (page 4096×7723),
`auto_vacuum=0`, `journal_mode=wal`, freelist 139 stron (0,54 MiB). `audit_log` **80 666 wierszy**,
span **2026-04-11 → 2026-07-01 (81 dni)**; wiek: >90d=**0**, >60d=8 974, >30d=**42 773**, >14d=61 698.
**⭐ KOREKTA L13:** retencja audit_log NIE jest widmem — `event_bus.cleanup_audit_log(90d)` istnieje i biega
przez `dispatch-event-bus-cleanup.timer` (daily 04:00 UTC, ostatnio 02.07 04:00:02, log: `usunieto 0` —
poprawnie, bo nic >90d). Detal + plan → `FALA1_gc_eventsdb_plan.md`.

---

## 2. ZMIANY (przygotowane, NIE wdrożone)

1. **`observability/log_rotation.py` (NOWY, cron-safe, bez zależności od dispatch_v2).**
   - DENYLIST (sprawdzana PIERWSZA, wygrywa zawsze): `shadow_decisions*`, `decision_outcomes*`,
     `gps_delivery_truth*`, `sla_log*`, `orders_state*`, `courier_plans*`, `courier_last_pos*`,
     `pending_proposals*`, `*.db`, `*.py`.
   - ALLOWLIST (wąska, jawna, z komentarzem skąd): TYLKO `candidate_decisions_\d{8}\.jsonl` +
     `fleet_filter_\d{8}\.jsonl` (+ opcjonalny sufiks rotacji). Plik nietrafiony = UNMATCHED (nietykany).
   - Domyślnie `--dry-run` (raport: lista/liczba/bajty/najstarszy ZACHOWANY). `--apply` wymagany do kasowania.
     `--max-delete` (default 500) bezpiecznik. Wiek z mtime (`now - retention*86400`, retention default 14d).
     Każda decyzja logowana; w `--apply` NAJPIERW plan, POTEM unlink. Exit 0 cron-safe (także brak katalogu).
2. **`tests/test_log_rotation.py` (NOWY, 13 testów behawioralnych, C13).** Import modułu po ŚCIEŻCE
   (co-located), bo conftest wpina kanon /scripts gdzie modułu nie ma. Fixtures: `os.utime` + wstrzykiwany `now`.
3. **`deploy_staging/etc/systemd/system/dispatch-log-rotation.{service,timer}` + `deploy_staging/README.md`.**
   Oneshot `--apply`, `OnCalendar=*-*-* 03:00:00` (off-peak) + `Persistent=true` (świadomie OnCalendar, nie
   OnUnitActiveSec — patrz L13 §3b: samo OnUnitActiveSec bywa odkotwiczane przez daemon-reload), MemoryMax 200M,
   OnFailure→Telegram. `systemd-analyze verify` OBU = OK (exit 0). **NIE zainstalowane.**
4. **`eod_drafts/2026-07-02/FALA1_gc_eventsdb_plan.md`** — plan events.db (KROK A weryfikacja ~10.07 /
   B VACUUM / C auto_vacuum / D doc-fix), wykonanie = koordynator za ACK.

---

## 3. DOWODY (nie deklaracje)

**Dry-run na ŻYWYCH danych (default, BEZ --apply) — nic nie skasowano:**
```
BEFORE: 120 plików, 327M
SUMMARY mode=DRY-RUN candidates=90 planned=90 deleted=0 freed=0.0MB would_free=174.0MB
        kept=30 denied=0 unmatched=0 capped=False errors=0 oldest_kept=(candidate_decisions_20260618.jsonl, 2026-06-18)
AFTER:  120 plików, 327M     ← IDENTYCZNE (przed==po)
```
Realny apply zwolniłby **~174 MB / 90 plików**, zostawiając 30 najnowszych (retencja 14d).

**Testy:** `13 passed` (izolowany bieg). Pokrywają: dry-run nic-nie-kasuje (przed==po), apply kasuje tylko
stary allowlist, **denylist nietykalny nawet stary+datowany**, granica 13.9d zostaje/14.1d leci, `--max-delete`
respektowany (najstarsze najpierw, capped=True), UNMATCHED nietykany, brak-katalogu no-op, CLI default=dry-run.

**MUTATION-CHECK (C13, 2 niezależne kille — każdy na świeżo przywróconej, diff-zweryfikowanej bazie):**
- Mut #1 — warunek wieku `mtime < cutoff_ts` → `>`: **9 testów FAIL** (wszystkie zależne od wieku). Restore → 13 pass.
- Mut #2 — kolejność w `classify()` denylist-first → allowlist-first: **1 test FAIL**
  (`test_denylist_wins_over_overlapping_allowlist`, jedyny broniący precedencji). Restore → 13 pass, moduł == ORIG (bajt-identyczny).
  (Uwaga: precedencja denylist>allowlist jest future-proofingiem — przy obecnych wąskich wzorcach listy są rozłączne,
  więc test celowo ROZSZERZA allowlist do nachodzenia, by kill był realny.)

**Pełna regresja (`pytest tests/` z worktree):** `3699 passed, 23 failed, 23 skipped, 11 xfailed`.
**23 „failed" = ARTEFAKT ścieżki worktree, NIE regresja i NIE moja wina.** Dowód:
- git status worktree = **additive-only** (4 nowe untracked, ZERO edycji istniejących).
- 23 failów = tylko `test_a2_selection_shadow.py` (15) + `test_courier_reliability.py` (8) — script-style testy
  liczące `MODULE_PATH = Path(__file__).parents[2] / "dispatch_v2" / ...`. Z kanonu `/scripts/.../tests`
  `parents[2]`=`/scripts` → ścieżka istnieje. Z worktree `/wt-gc/tests` `parents[2]`=`/workspace` →
  `/workspace/dispatch_v2/...` NIE istnieje → SkipTest→fail.
- **Te 2 pliki puszczone z KANONU `/scripts` → `23 passed`.** Baseline (3709/0) też z kanonu (per HANDOFF §2).
- Mój `test_log_rotation.py` importuje po ścieżce co-located (`parent.parent`) → odporny na lokalizację, PASS.
**Po merge do kanonu regresja = 3709 + 13 = 3722 passed / 0 failed.** py_compile modułu = OK.

---

## 4. DEPLOY ZA ACK (koordynator, off-peak, nadzorowany)
Kolejność (szczegóły + komendy: `deploy_staging/README.md`):
1. Merge `observability/log_rotation.py` + `tests/test_log_rotation.py` do kanonu; regresja z kanonu = 3722/0.
2. **Nadzorowany `--dry-run`** na żywym katalogu (potwierdź ~90 plików / ~174 MB / oldest_kept 2026-06-18).
3. **Pierwszy `--apply` RĘCZNIE**, off-peak, z licznikiem przed/po (~120 → ~30 plików, ~174 MB odzysku).
4. Dopiero potem instal `dispatch-log-rotation.{service,timer}` + `systemctl enable --now …timer`.
5. **events.db (plan osobny):** KROK A = weryfikacja że delete odpali ~**2026-07-10** (grep log cleanup);
   B/C/D (VACUUM / auto_vacuum / doc-fix logrotate) za ACK — część dotyka plików spoza tego lane'u.

## 5. ROLLBACK
- Timer: `systemctl disable --now dispatch-log-rotation.timer` + `rm /etc/systemd/system/dispatch-log-rotation.*` + `daemon-reload`.
  Bez timera moduł jest martwy (zero efektu) → powrót do stanu sprzed (unbounded, ale ZERO ryzyka kasowania).
- Kod: usunąć `observability/log_rotation.py` (revert merge).

## 6. POZA PARTYCJĄ (nie tknięte, do koordynatora)
- Instalacja/enable timera, pierwszy realny `--apply`, VACUUM/retencja events.db — wszystko za ACK.
- events.db KROK C (dodać VACUUM do `event_bus_cleanup.py` / `PRAGMA` na `event_bus.py`) — pliki rdzenia event-bus.
- events.db KROK D (poprawka fałszywego komentarza `/etc/logrotate.d/dispatch-v2:130`) — zapis do /etc.
- `__init__.py:20` znów zgodny (moduł wraca) — bez zmiany. Podwójny log w `event_bus_cleanup.log` (kosmetyka).
- Rotation-awareness readerów (L13 pkt b) = domena L1.2 (już LIVE), nie ten lane.
