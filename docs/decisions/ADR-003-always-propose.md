# ADR-003: Always-propose — Ziomek NIGDY „brak kandydatów"

Status: obowiązuje; execution boundary doprecyzowana przez ODR-001 i ODR-002 2026-07-12

## Kontekst
„BRAK KANDYDATÓW" przy istniejącej flocie jest operacyjną ślepotą. Ziomek ma zawsze pokazać najlepszy feasible albo jawny least-damage/`ALERT`. Widoczność problemu i kandydata nie oznacza jednak, że niedopuszczalny plan stał się feasible lub dostał prawo wykonania.

## Decyzja
Zamiast „brak" zwracaj: (1) feasible-first albo (2) jawny `feasibility=NO` least-damage/`ALERT` z przyczyną i granicą authority. D-A3-2 (03.08) doprecyzowuje no_solo: najpierw wyłącznie HARD-safe (`MAYBE/YES` + plan), potem — przy kompletnym dowodzie SOFT całej klasy — najmniejszy pickup distance, wyższy istniejący score i kanoniczny CID. Jeśli choć jednemu kandydatowi brakuje porównywalnej odległości/GPS, sygnały SOFT są ignorowane dla całej klasy i zostaje neutralny tie po CID; brak GPS nie jest karą. Gdy wszyscy są HARD-NO, nadal pokazujemy deterministyczny diagnostyczny CID, lecz wyłącznie jako `COORDINATOR_ESCALATION`, `hard_safe=false`, `recommend_only=true`. ODR-001 utrzymuje R6 `>40` i R27 `|Δ|>10` jako niedopuszczalne — taki przypadek wolno uwidocznić, lecz nie przedstawiać jako wykonywalny plan. Przerzut, plan Alarmowy i pozostały least-damage początkowo wymagają approval przed execute. ODR-002 zabrania wywodzenia execute z tego dokumentu, kodu, flagi lub sentinela; authority daje wyłącznie aktualna podpisana karta po owner-only promocji. Jedyny legalny brak obiektu floty to 0 floty pracującej; early-bird/czasówka może pozostać jawnym holdem.

D-A3-1/3 ustanawiają osobny typ `COORDINATOR_ESCALATION` oraz trwałą parę
decyzja→późniejszy assignment w istniejącym `learning_log`. Ręczny wybór inny
niż wynik silnika jest automatycznie `OWNER_EXCEPTION` z początkowym
`reason="nieokreślony"`; operator nie jest blokowany pytaniem o powód.
`COORDINATOR_ESCALATION` ma obowiązkową podklasę: `STALE`, `GEOMETRY`, `COMMIT`,
`DIFFICULT` albo jawne `UNKNOWN`; mapę prefiksów posiada wyłącznie
`core.proposal_output`. Diagnostyczny score próby R29 ma osobne pole
`a3_solo_score` i nie może nadpisywać kanonicznego `score` w danych uczących.

Granica A-3 jest totalna: wyjątek instrumentu nigdy nie wychodzi z
`select_and_emit`. Powstaje deterministyczna diagnostyczna eskalacja i log
klasy błędu, a istniejący jawny wynik `KOORD/no_solo` zawsze wraca do callera.
Techniczny hold Koordynatora zachowuje legacy learning-record i nie jest
traktowany jako finalne rozwiązanie D1.

## Konsekwencje
- Wolno/trzeba: pokazać sentinel/best-effort jako jawny `NO/ALERT` z przyczyną; sam sentinel nie jest bugiem.
- Nie wolno: zamienić `NO/ALERT` w „brak", ale też nie wolno utożsamić go z feasible lub execute. Auto-aktywacja Alarmu nie jest auto-wykonaniem.
- Psuje się przy złamaniu: coords=None → fałszywy „brak" (bronione fail-loud haversine na None/(0,0) — Lekcja #81 — i fallback coords).
- Zmiana selekcji „równego traktowania bez GPS" dotyka best-effort → tknij WSZYSTKIE bliźniaki (8), nie tylko klaster selekcji.

## Źródła
`ODR-001-owner-decisions-2026-07-12.md`; `ODR-002-autonomy-authority-ownership-2026-07-12.md`; `memory/ZIOMEK_REGULY_KANON.md`; `memory/ziomek-change-protocol.md`; baseline implementation `core/selection.py` / `dispatch_pipeline.py`.
