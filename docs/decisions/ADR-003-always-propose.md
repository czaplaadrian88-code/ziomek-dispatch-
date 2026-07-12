# ADR-003: Always-propose — Ziomek NIGDY „brak kandydatów"

Status: obowiązuje; execution boundary doprecyzowana przez ODR-001 2026-07-12

## Kontekst
„BRAK KANDYDATÓW" przy istniejącej flocie jest operacyjną ślepotą. Ziomek ma zawsze pokazać najlepszy feasible albo jawny least-damage/`ALERT`. Widoczność problemu i kandydata nie oznacza jednak, że niedopuszczalny plan stał się feasible lub dostał prawo wykonania.

## Decyzja
Zamiast „brak" zwracaj: (1) feasible-first albo (2) jawny `feasibility=NO` least-damage/`ALERT` z przyczyną i granicą authority. ODR-001 utrzymuje R6 `>40` i R27 `|Δ|>10` jako niedopuszczalne — taki przypadek wolno uwidocznić, lecz nie przedstawiać jako wykonywalny plan. Przerzut, plan Alarmowy i pozostały least-damage początkowo wymagają approval przed execute. Jedyny legalny brak obiektu floty to 0 floty pracującej; early-bird/czasówka może pozostać jawnym holdem.

## Konsekwencje
- Wolno/trzeba: pokazać sentinel/best-effort jako jawny `NO/ALERT` z przyczyną; sam sentinel nie jest bugiem.
- Nie wolno: zamienić `NO/ALERT` w „brak", ale też nie wolno utożsamić go z feasible lub execute. Auto-aktywacja Alarmu nie jest auto-wykonaniem.
- Psuje się przy złamaniu: coords=None → fałszywy „brak" (bronione fail-loud haversine na None/(0,0) — Lekcja #81 — i fallback coords).
- Zmiana selekcji „równego traktowania bez GPS" dotyka best-effort → tknij WSZYSTKIE bliźniaki (8), nie tylko klaster selekcji.

## Źródła
`ODR-001-owner-decisions-2026-07-12.md`; `memory/ZIOMEK_REGULY_KANON.md`; `memory/ziomek-change-protocol.md`; baseline implementation `core/selection.py` / `dispatch_pipeline.py`.
