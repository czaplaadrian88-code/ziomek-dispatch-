# SELECTION VETO — werdykt decyzyjny (2026-06-08)

**Kontekst:** notatka todo_master wskazywała "decyzja 08.06 (at-job #110)". Weryfikacja: **#110 NIE ISTNIEJE w atq** (fantom jak #117/#120/#121/#122). Decyzja zrobiona ręcznie na danych z digestu.

## Dane (digest tygodniowy, od 2026-06-01, 1478 decyzji)
- live zwycięzca cross<-0.5 (przeciw-kierunkowy): **60 (4%)**
- dial **informed**: 33 flipów (2%), cross 60→27, wszystkie na znane pozycje (bag) — bezpieczne
- dial **any**: 45 flipów (3%), cross 60→15, z czego 33 flipy na pustych/solo

## Werdykt: **NIE FLIPOWAĆ LIVE TERAZ** (shadow zostaje, zero-behavior)
Powody:
1. Wpływ mały (2-3%); przeciw-kierunkowe wybory wg replayu 259 decyzji są w WIĘKSZOŚCI uzasadnione
   (10/18 late-pickup tier-2 override, 7/18 scarcity, 1/18 czysty score) → veto nadpisywałoby legalne decyzje.
2. A2 reliability soft-score flipnięty LIVE 06-07 (coeff 60); jego efekt na breach mierzymy przy #113 (10.06).
   Druga zmiana w warstwie selekcji TERAZ zafałszowałaby pomiar A2.
3. Gdyby kiedyś flipować → dial **informed** (bezpieczny), NIE `any`.

## Sekwencja
- Re-decyzja PO werdykcie A2 (#113, 10.06): jeśli sam A2 dowozi cel −50% breach → **RETIRE veto** (usuń martwy shadow).
- Jeśli A2 niewystarczający → rozważyć flip diala **informed** jako dodatkową warstwę.
- NIE wyrywam kodu shadow dziś — churn w trakcie żywego eksperymentu A2; shadow darmowy.

## Higiena powiązana (NIE zrobione dziś, świadomie)
- VETO-RETIRE odłożone do post-#113.
- Duplikat reguły R6 (z tego wątku) — odłożony razem z retire.
- ⚠ R6FRESH-DUP-CONFIG-01 (ENABLE_OBJ_R6_SOFT_DEADLINE 2× w service+override) = INNA flaga, osobny temat.
