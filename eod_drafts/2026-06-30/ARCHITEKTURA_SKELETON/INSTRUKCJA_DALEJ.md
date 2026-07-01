# INSTRUKCJA DALEJ — co musisz zrobić, żeby domknąć (Adrian, rano 2026-07-01)

> Audyt Fazy 1 (tmux 2) **skończył się w nocy** (7 deliverables `FAZA1_00..06` + backing). Ja (ta sesja) **złożyłem szkielet architektury** obok Przykazania #0. **Nic nie zacommitowałem, nie flipnąłem, nie zrestartowałem, nie tknąłem silnika** — to są Twoje kroki z ACK poniżej.

---

## CO POWSTAŁO W NOCY (drafty, do przeglądu)
Katalog: `dispatch_v2/eod_drafts/2026-06-30/ARCHITEKTURA_SKELETON/`
1. **`ZIOMEK_ARCHITECTURE.md`** — czym Ziomek JEST: pipeline 10 warstw + 6 filarów + **8 kontraktów** (cel) + **rejestr bliźniaków** (co rusza się razem).
2. **`ZIOMEK_INVARIANTS.md`** — co MUSI być zawsze prawdą + strażnik. Zrekoncyliowane z oracle: rozróżnia ✅RT / 🟢TEST / ⚠️VOID / 🔴SLOT. **Uwaga: `carried_first_guard` = ⚠️VOID** (audyt wykrył — biega z pustym env).
3. **`ZIOMEK_DEFINITION_OF_DONE.md`** — 1 ekran, 7 ptaszków + bramka anty-entropii (11 RED-checków).
4. **`entropy_dashboard.py`** — stojący miernik 8 metryk (odpalony, działa: flagi 21, sentinele 12 oracle). Auto-liczy sentinele+flagi; reszta = baseline audytu z tagiem.
5. Ten plik.

Audyt (osobno, jego produkt): `FAZA1_00_RAPORT_KONCOWY.md` (READ FIRST — meta-wniosek + 19 VOID), `FAZA1_04_stan_docelowy_dashboard.md` (8 kontraktów), `FAZA1_05_roadmapa_poc.md` (L0-L8 + PoC).

---

## KROK 1 — PRZECZYTAJ (15 min, w tej kolejności)
1. `FAZA1_00_RAPORT_KONCOWY.md` §2 (meta-wniosek) + §4 (co złapał oracle) + §6 (droga).
2. `ARCHITEKTURA_SKELETON/ZIOMEK_ARCHITECTURE.md` (mój szkielet — czy zgadza się z Twoją wizją „ideału").
3. `ARCHITEKTURA_SKELETON/ZIOMEK_INVARIANTS.md` sekcja „⚠️VOID" (4 kłamiące przyrządy = najpilniejsze).

## KROK 2 — ZATWIERDŹ CEL (Faza 2 audytu; decyzja, nie kod)
8 kontraktów (FAZA1_04) = definicja „architektonicznego ideału". Powiedz: **akceptuję / zmieniam X**. To odblokowuje Fazę 3 (naprawy). Nic nie ruszamy, dopóki nie powiesz „cel OK".

## KROK 3 — UMIEŚĆ SZKIELET NA STAŁE (gdy zaakceptujesz drafty; ~2 min, git)
Drafty leżą w `eod_drafts/` (przeżyją, ale to nie kanon). Docelowo obok #0. Gdy OK:
```
cd /root/.openclaw/workspace/scripts/dispatch_v2
cp eod_drafts/2026-06-30/ARCHITEKTURA_SKELETON/ZIOMEK_ARCHITECTURE.md .
cp eod_drafts/2026-06-30/ARCHITEKTURA_SKELETON/ZIOMEK_INVARIANTS.md .
cp eod_drafts/2026-06-30/ARCHITEKTURA_SKELETON/ZIOMEK_DEFINITION_OF_DONE.md .
mkdir -p tools && cp eod_drafts/2026-06-30/ARCHITEKTURA_SKELETON/entropy_dashboard.py tools/
git add ZIOMEK_ARCHITECTURE.md ZIOMEK_INVARIANTS.md ZIOMEK_DEFINITION_OF_DONE.md tools/entropy_dashboard.py
git commit -m "docs: szkielet architektury + inwarianty + DoD + miernik entropii (obok #0)"   # atomowo, C1-git
```
Potem dopisz w `dispatch_v2/CLAUDE.md` pod Przykazaniem #0 jedną linię: „Kanon architektury = `ZIOMEK_ARCHITECTURE.md` + `ZIOMEK_INVARIANTS.md` + `ZIOMEK_DEFINITION_OF_DONE.md`". (memory już zaktualizowane.)

## KROK 4 — WYBIERZ START FAZY 3 (naprawy; każda = osobny mini-sprint ETAP 0→7 + ACK + off-peak>14:00)
Roadmapa L0-L8 (FAZA1_05). **Rekomendowana kolejność wg audytu + bramek czasowych:**

**🔥 NAJPILNIEJSZE (bramki czasu + realny harm — decyzja w tym tygodniu):**
- **L1.1 serializer-kompletność** [🟡 low ryzyko, 0 zmiany zachowania] — **MUSI być PRZED O2 02.07** (odsłania 38 zgubionych kluczy: `eta_source`, `r6_gold4_gate`). Bez tego kalibracja O2 stoi na kłamiącej liczbie. **Zacznij TU.**
- **L6.A1 route-order golden harness** [🟢 test, 0 ryzyka] — **deadline 07-10** (monitor parytetu wygasa). Może iść RÓWNOLEGLE z L1. Zastępuje wygasający monitor testem CI. To jest PoC #1 z audytu.
- **L2.1 sentinel-ingest** [🔴 P0, LIVE harm 8 ofiar/d] — wepnij ISTNIEJĄCY walidator `common.py:513` u ingest; bliźniaki haversine↔osrm RAZEM. Najgorętszy fizycznie DZIŚ.

**🧊 NAJGŁĘBSZE „nigdy nie wraca" (po pilnych, kolejno, z ACK):**
- **L0** strażniki-shadow (F6) + napraw 4 ⚠️VOID → **L3** plan_recheck nie-cofa (F2) → **L4** `available_from` 1 źródło (F1, Q1/Q2 już ACK) → **L5** ETA load-aware (F4, ⛔ dotyka HARD) → **L6** kanon+bliźniaki (F5) → **L7** hardening → **L8** sprzątanie.

**⛔ BLOKADY (nie flipuj bez tego):**
- `PENDING_RESWEEP_LIVE` — **NIE flipować**, dopóki `global_allocate` geometria (⚠️VOID) nie naprawiona (certyfikuje ślepą liczbę, 35% worków spread>8km).
- `C2` re-enable Telegrama — dopiero po L7.5 (fcntl na `pending_proposals` 3-writer).

## KROK 5 — RYTM każdej fali (tak, jak lubisz)
Dla każdego kroku Fazy 3: prosty polski „co robimy + wpływ + jak bezpiecznie" → ETAP 0 recon (świeży grep, linie dryfują) → bezpieczny krok → **FLIP tylko za Twoim ACK**, off-peak. Po każdej fali: re-run `entropy_dashboard.py` → metryka MALEJE (dowód progresu). „go" między punktami.

---

## BEZPIECZEŃSTWO / ROLLBACK
- Szkielet to **drafty w eod_drafts** — nic nie ruszają. Odrzucenie = po prostu ich nie kopiuj (KROK 3).
- **Zero** zmian silnika/flag/serwisów w nocy. HEAD silnika bez zmian (`8024705` wg audytu).
- Bramki, których pilnować: **02.07** O2 (po L1.1) · **03.07** objm/frozen-lex · **04.07** load-aware ETA · **≤07-10** route-order golden.

## JEDNO ZDANIE
Masz teraz **mapę (architektura) + kontrakt (inwarianty) + miernik (dashboard) + drogę (roadmapa audytu L0-L8)**. Domknięcie = zaakceptuj cel (KROK 2), wbij szkielet (KROK 3), i puść Fazę 3 falami zaczynając od L1.1 serializer + L6.A route-order (pilne bramki), z ACK per fala.
