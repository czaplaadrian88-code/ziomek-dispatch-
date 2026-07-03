# ADR-003: Always-propose — Ziomek NIGDY „brak kandydatów"

Status: obowiązuje (od ~V3.16/maj 2026; utrwalone po incydencie firmowego konta 07.05 „BRAK KANDYDATÓW")

## Kontekst
Koordynator potrzebuje kandydata na KAŻDE aktywne zlecenie. „BRAK KANDYDATÓW" = operacyjna ślepota i utrata zaufania (LESSON-QA-9: operational awareness > scoring quality). Historyczny bug: `pickup_coords=None` → sentinel haversine ~6285 km → wszyscy `pickup_too_far` HARD REJECT → „brak" dla każdego zlecenia firmowego (~3-5/dzień). Ziomek ma zawsze coś zaproponować, nawet gdy propozycja łamie R6 — z uczciwym oznaczeniem.

## Decyzja
Eskalacja 3-stopniowa zamiast „brak": (1) feasible-first (przechodzi HARD); (2) najbliższy łamiący R6; (3) best-effort najszybszy wolny (`_best_effort_fastest_pickup_key`, MOŻE łamać R6, otagowany `auto_route=ALERT` / `best_effort` / `feasibility=NO`, score bywa sentinel ≈ −1e9). Werdykt KOORD (hold) TYLKO dla early-bird/czasówka ≥60 min naprzód. Jedyny legalny „brak" = 0 floty pracującej (panel zamawiania wyłączony).

## Konsekwencje
- Wolno/trzeba: traktować sentinel best-effort w konsoli/Telegramie jako POPRAWNY (uczciwy framing przez `_serialize_result`) — to NIE bug.
- Nie wolno: interpretować best-effort/`feasibility=NO`/sentinel-score jako awarię i „naprawiać" go tak, by zwracał „brak"; wysyłać kandydata bez tagu ALERT gdy łamie R6.
- Psuje się przy złamaniu: coords=None → fałszywy „brak" (bronione fail-loud haversine na None/(0,0) — Lekcja #81 — i fallback coords).
- Zmiana selekcji „równego traktowania bez GPS" dotyka best-effort → tknij WSZYSTKIE bliźniaki (8), nie tylko klaster selekcji.

## Źródła
`memory/ziomek-change-protocol.md` „Reguły biznesowe zakodowane: Always-propose" + „dyskryminacja pozycji"; `ZIOMEK_ARCHITECTURE.md` §1 (warstwa 7-8); `CLAUDE.md` sprint firmowe konto 07.05 (6-warstwowa defense, Lekcja #81); `dispatch_pipeline.py` (`_best_effort_fastest_pickup_key`).
