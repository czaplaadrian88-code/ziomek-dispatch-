# ADR-001: Pipeline 10 warstw + HARD przed SOFT

Status: obowiązuje (od ~początku projektu; doktryna Adriana Z2 „jakość ponad szybkość, root cause przed fix"; kanon Fazy 2 audytu zatwierdzony 2026-07-01)

## Kontekst
Zlecenie przechodzi przez 10 warstw decyzji: wejście → geokod (HARD) → early-bird/czasówka (HARD) → telemetria floty → `check_feasibility_v2` (HARD) → scoring ~19 kar (SOFT) → selekcja (SOFT) → werdykt KOORD (HARD) → zapis+kanon → powierzchnie (konsola/apka/Telegram). HARD = reguły nieprzekraczalne (R6=35/40 tier, R-DECLARED-TIME, shift, geokod ∈ bbox). SOFT = kary i ranking. Diagnoza audytu: ta sama reguła często żyje w KILKU warstwach naraz (feasibility↔greedy↔plan_recheck) — to korzeń kruchości K1 i powód, że klasa łatana ≥4× wraca (naprawa trafiała w jeden bliźniak albo w render, nie w źródło).

## Decyzja
HARD jest ZAWSZE liczone przed SOFT i SOFT NIGDY nie osłabia HARD (P0). Egzekwowane runtime przez `_assert_feasibility_first` (dispatch_pipeline). Werdykt KOORD powstaje wyłącznie w warstwie 8 (`INV-LAYER-NO-VERDICT-OUTSIDE-L5`). Fix reguły idzie U ŹRÓDŁA we właściwej z 10 warstw, nie łatką na krawędzi (render/instrument).

## Konsekwencje
- Wolno: dodać/zmienić karę SOFT w scoringu (warstwa 6) lub selekcji (7) — nie dotyka bezpieczeństwa.
- Nie wolno: obejść feasibility (np. re-admisja verdict=NO→MAYBE w `FEAS_CARRY_READMIT`) bez re-assertu HARD na EMIT (wzorzec #10); relaksować HARD przez SOFT.
- Przy zmianie dotykającej HARD lub świadomych inwersji P-1..P-7 → ZATRZYMAJ i przedstaw Adrianowi wybór (ETAP 2 protokołu), nie zgaduj.
- Złamanie = objaw wraca (klasa nawrotów ≥4×), bo naprawa trafiła w bliźniak/render zamiast w źródło reguły.

## Źródła
`ZIOMEK_ARCHITECTURE.md` §1 (10 warstw) + §4 (bliźniaki); `ZIOMEK_INVARIANTS.md` kontrakt ② (HARD przed SOFT); `memory/ziomek-change-protocol.md` (fakty kotwiczące: `_assert_feasibility_first`; ETAP 1-2); `docs/audyt/01-ZALEZNOSCI.md` §2 (mapa warstw→symbole, zweryfikowana z kodem).
