# BRIEF ITER3 (sesja 297) — OD-7 archiver: N1 z delta-blind #2 (gorący -journal = ciche fałszywe liczby)

## Kontekst
Iter2 `5575cb14e` przeszedł delta-blind #2: WSZYSTKIE 4 findingi rundy 1 zamknięte + monotoniczność
potwierdzona. JEDEN nowy defekt wprowadzony przez fix: N1 (medium-low).
Werdykt: `/root/artifacts/blind-297/od72/verdict.json` — PRZECZYTAJ W CAŁOŚCI.
Turnkey repro recenzenta #2: `/root/artifacts/blind-297/od72/repro_test_od7_hot_journal.py`
(RED na iter2, GREEN na iter1 — po fixie MUSI być GREEN na iter3, a scenariusz wcielony do suity).

## N1 — istota
`immutable=1` pomija GORĄCY dziennik rollback (`-journal`; tryb DELETE = DOMYŚLNY sqlite): po crashu
pisarza plik główny zawiera strony DO WYCOFANIA, narzędzie liczy je jako prawdę (216 vs 200) i NIC nie
ujawnia (`wal_pending=False`, errors puste, exit 0). Iter1 był głośny (błąd + exit 3 + zero liczb).
Docstring `sqlite_connect_readonly` (l.737) obiecuje ujawnianie — dotrzymane tylko dla `-wal`.
`_SQLITE_SIDECARS` (l.710) już zna `-journal` — pominięcie w ujawnianiu to przeoczenie.

## Fix (kierunek: NIE publikować liczb, których nie umiemy policzyć)
Warunek niepełności ma objąć KAŻDY trwały dziennik: niepusty `-wal` LUB istniejący `-journal`.
Dla gorącego `-journal` wybierz wariant MOCNIEJSZY recenzenta (spójny z iter1 i z zasadą „liczby
prawdziwe albo jawnie niepełne"): NIE publikuj liczb — błąd w errors[], exit 3, raport powstaje,
`rows_total=None` + powód. (Dla niepustego `-wal` zostaje dzisiejsze ujawnianie `wal_pending` —
tam liczby są prawdziwe-ale-niepełne; przy `-journal` są FAŁSZYWE, więc nie wolno ich podać.)
Uzasadnij w raporcie rozróżnienie tych dwóch przypadków.

## DoD iter3
1. repro recenzenta #2 = GREEN; jego scenariusz (baza DELETE + gorący -journal przez os._exit przed
   commitem) wcielony do tests/ jako stały oracle.
2. Test kontrolny: baza DELETE z CZYSTYM (nieistniejącym) -journal → liczby publikowane normalnie, exit 0.
3. Stary test `-wal` (wal_pending) nadal zielony; 60 testów iter2 zielone.
4. Mutacja: przywrócenie warunku tylko-`-wal` → RED.
5. Pełna regresja (pkgroot + ZIOMEK_SCRIPTS_ROOT): 0 failed, delta = tylko twoje nodeidy.
6. ⛔ ZERO żywych ścieżek; ⛔ LEDGER = CTO; ⛔ polityka (retention_od7_policy.json) NIETKNIĘTA w tej
   iteracji (aktualizacje P-2/P-3 z ACK ownera robi CTO osobnym commitem po merge); ⛔ RAPORT_ITER3.md
   NIEzacommitowany w korzeniu (zostaw w worktree).
7. `RAPORT_ITER3.md`: fix+dowody, delta, pełny SHA.
