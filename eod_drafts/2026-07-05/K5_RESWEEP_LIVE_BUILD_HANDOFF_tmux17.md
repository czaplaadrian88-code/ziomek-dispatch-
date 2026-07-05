# HANDOFF → tmux 17: BUDOWA ścieżki LIVE dla PENDING_RESWEEP (K5 Sprintu 2 — ZABLOKOWANY do budowy)

> Od: FLIPMASTER S2 (tmux 20), za decyzją Adriana 05.07 ~19:30 UTC (Wariant A).
> Rola tmux 17: ZBUDOWAĆ ścieżkę live resweepa (kod+testy+replay+rekomendacja). **ŻADNYCH flipów flags.json i restartów — to wyłączność FLIPMASTERA (tmux 20); flip K5 nastąpi po Waszej rekomendacji, za osobnym ACK Adriana.** Protokół #0 obowiązuje w całości.

## Kontekst (stan zweryfikowany na żywo 05.07 ~19:20 UTC)
- `tools/pending_global_resweep.py` — co-minutowy globalny re-ranking WISZĄCYCH zleceń prawdziwym `assess_order` (timer `dispatch-pending-resweep-shadow`). Shadow zdrowy: `ENABLE_PENDING_RESWEEP=true`, jsonl żywy.
- **Ścieżka LIVE NIEWPIĘTA** (`pending_global_resweep.py:418-424`): `PENDING_RESWEEP_LIVE=ON` ⇒ tylko warning „shadow-only", `live_acted=0`. Flaga stoi na `false` (uczciwy stan; FLIPMASTER świadomie NIE flipował — kłamiąca flaga).
- Bramka `live_gate_open()` (l. 240) — geometria `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK` jest **ON od 05.07 18:51 (K2)**, inwariant `global_allocate` 🟢 (S1) ⇒ bramka przejdzie; brakuje TYLKO implementacji akcji.
- Skala z shadow (01-05.07): `would_repropose` ~110-356/dzień; ticki z redukcją pile-onu 135-205/dzień.
- Konsola JUŻ konsumuje globalną alokację: `ENABLE_GLOBAL_ALLOC_WRITE=true` → `global_alloc.json` → overlay feed.py na tablicy.

## DECYZJA KIERUNKOWA ADRIANA (05.07, wiążąca)
**Live = podmiana/aktualizacja propozycji dla KONSOLI (pending_proposals.json + powierzchnia 1-klik), NIE edit wiadomości Telegrama.** Telegram wyciszony 26.06 — NIETYKALNY (memory [[telegram-notifications-mute-2026-06-26]]).

## Wymagania budowy (protokół #0 + specyfika)
1. **Fix u źródła:** akcja live w `run_once` za `C.flag(FLAG_LIVE)` + OBOWIĄZKOWO `live_gate_open()` przed KAŻDĄ akcją (bramka zakodowana — nie omijać).
2. **Współdzielony plik:** `pending_proposals.json` żyje też w `telegram_approver` — locking (fcntl) + atomic write (temp+fsync+rename); przemyśleć race z żywym approverem (MAPA KOMPLETNOŚCI: wszyscy czytelnicy/pisarze pliku).
3. **Bezpieczniki zachowane:** `PENDING_RESWEEP_MARGIN` (=15.0) jako próg odwrócenia, `MAX_HANGING=8`, fail-soft na każdym IO; `_disable_replay_capture()` już chroni zestaw kalibracyjny — nie zepsuć.
4. **Mierzalność:** `live_acted>0` w summary + osobny marker w jsonl per akcja (co odwrócono, stary/nowy kurier, margines); flaga ON≠OFF test; parytet bliźniaków jeśli dotkniecie serializacji (A+B).
5. **Dowody przed rekomendacją:** pełna regresja vs baseline (05.07: 4224/0) + replay/dry-run „warto i bez regresji" z dowodem POZYTYWNEGO wpływu + e2e przez dotknięte warstwy (resweep→pending_proposals→feed konsoli).
6. **Deliverable:** kod na masterze (flaga default OFF, kod inertny) + werdykt do `dispatch_state/` + wpis do trackera + **rekomendacja flipu dla FLIPMASTERA (tmux 20)**. Design większy niż 1 plik → szkic do ACK Adriana przed kodem.

## Zakazy
Peak Pn-Pt 11-14/17-20 (dotyczy ewentualnych dry-runów na żywych plikach) · telegram nietykalny · flags.json/restarty = tylko tmux 20 · at-205/206/208 (Pn 06.07) nie ruszać · NIE pushować.
