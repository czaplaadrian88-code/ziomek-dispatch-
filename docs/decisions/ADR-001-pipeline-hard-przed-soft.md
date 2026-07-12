# ADR-001: Pipeline 10 warstw + HARD przed SOFT

Status: obowiązuje; semantyka R6/R27 doprecyzowana przez ODR-001 2026-07-12

## Kontekst
Zlecenie przechodzi przez 10 warstw decyzji: wejście → geokod (HARD) → early-bird/czasówka (HARD) → telemetria floty → `check_feasibility_v2` (HARD) → scoring ~19 kar (SOFT) → selekcja (SOFT) → werdykt KOORD (HARD) → zapis+kanon → powierzchnie. HARD = reguły nieprzekraczalne. ODR-001 wiąże R6 z `in_vehicle_age` possession→customer handoff: 35 normalnie / 40 tylko Alarm / nigdy klasa kuriera; R27 ma immutable commitment, normalne `|Δ|<=5`, Alarm breach do 10 i zakaz `>10`. SOFT = kary i ranking. Ta sama reguła żyje w kilku warstwach, więc fix jednego bliźniaka nie wystarcza.

## Decyzja
HARD jest ZAWSZE liczone przed SOFT i SOFT NIGDY nie osłabia HARD (P0). Egzekwowane runtime przez `_assert_feasibility_first` (dispatch_pipeline). Werdykt KOORD powstaje wyłącznie w warstwie 8 (`INV-LAYER-NO-VERDICT-OUTSIDE-L5`). Fix reguły idzie U ŹRÓDŁA we właściwej z 10 warstw, nie łatką na krawędzi (render/instrument).

## Konsekwencje
- Wolno: dodać/zmienić karę SOFT w scoringu (warstwa 6) lub selekcji (7) — nie dotyka bezpieczeństwa.
- Nie wolno: obejść feasibility (np. re-admisja verdict=NO→MAYBE w `FEAS_CARRY_READMIT`) bez re-assertu HARD na EMIT (wzorzec #10); relaksować HARD przez SOFT.
- Przy zmianie dotykającej HARD lub świadomych inwersji P-1..P-7 → ZATRZYMAJ i przedstaw Adrianowi wybór (ETAP 2 protokołu), nie zgaduj.
- Złamanie = objaw wraca (klasa nawrotów ≥4×), bo naprawa trafiła w bliźniak/render zamiast w źródło reguły.

## Źródła
`ODR-001-owner-decisions-2026-07-12.md`; `ZIOMEK_ARCHITECTURE.md` §1 + §4; `ZIOMEK_INVARIANTS.md` kontrakt ②; `memory/ziomek-change-protocol.md`; `docs/audyt/01-ZALEZNOSCI.md` §2.
