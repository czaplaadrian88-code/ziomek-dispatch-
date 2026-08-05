# BRIEF (sesja 297, CTO Fable) — FIX FLAKE ~0,56% oracle no_plaintext (bramka tests.pin-kdf-oracle-substring-flake, due 08.08)

## KIM JESTEŚ / GDZIE PRACUJESZ
Agent-budowniczy (zadanie MAŁE i chirurgiczne). Worktree:
`/root/worktrees/dispatch_v2/active/20260805-pin-oracle-flake-297-cto`
(gałąź `wt/pin-oracle-flake-297-cto-20260805`, base master `06e4d5c39`). Tylko ten worktree; merge = CTO.

## PROBLEM
`test_a6_security_pin_kdf` — oracle `test_security_oracle_no_plaintext_in_store` pada losowo ~0,56% biegów:
szuka PIN-u substringiem w CAŁYM zserializowanym store, a 4-6-cyfrowy PIN potrafi wystąpić jako substring
losowego hexa (sól/hash KDF). Skutek: ~0,6% szansy fałszywej czerwieni nocnego strażnika CO NOC.
Memory potwierdza charakter losowy (nie regresja). To FALSE POSITIVE oracle — nie defekt produkcji.

## ZADANIE (fix TESTU, nie produkcji)
1. Znajdź test i zrozum, co NAPRAWDĘ ma gwarantować: żaden plaintext PIN nie ląduje w store.
2. Zamień substring-scan całego blobu na **asercję strukturalną**: sparsuj store, sprawdź KONKRETNE pola
   (np. pola hash/salt mają być wynikiem KDF o poprawnym kształcie, żadne pole nie równa się PIN-owi,
   żadne pole TEKSTOWE przeznaczone na metadane nie zawiera PIN-u). Hex hasha/soli MOŻE zawierać cyfry PIN-u —
   to nie jest leak.
3. **Oracle musi zostać oraclem** — mutation test: sfabrykuj store z REALNYM leakiem (PIN plaintext w polu,
   PIN w nazwie klucza, PIN dopisany do wartości) → test MUSI być RED na każdej formie. To jest dowód,
   że nie osłabiłeś oracle (ZAKAZ osłabiania — patrz HERMETIC/oracle zasady repo).
4. **Dowód anty-flake:** pętla ≥1000 biegów oracle z losowymi PIN-ami/seedami (może być parametryzowana pętla
   w jednym teście pomocniczym albo skrypt w worktree z zapisanym wynikiem) = 0 fałszywych czerwieni.
5. Pełna regresja: `cd /root/worktrees/dispatch_v2/pkgroot/20260805-pin-oracle-flake-297-cto/dispatch_v2 &&
   /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q` — baseline PRZED, delta = tylko twoje zmiany, 0 failed.

## DELIVERABLES (NA DYSK, PRZED KOŃCEM)
1. Commit na gałęzi (jawny pathspec). 2. `RAPORT_AGENTA.md`: diff-opis, wynik 1000x sweep, wynik mutacji
(każda forma leaku RED), liczby regresji, pełny SHA commita.
