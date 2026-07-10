# Z-P1-05 Faza B — delegacja resolverów/norm do identity (RAPORT)

**Sprint 4, agent B.** Data: 2026-07-10. Branch `sprint4/identity-faza-b` (baza `44017e1`).
Cel: delegacja 1:1 istniejących kopii norm + scoringu resolverów do kanonu `identity/` — **ZERO zmiany zachowania**.

## Commity (3, jawne ścieżki, stopka Co-Authored-By)

| # | Hash | Zakres |
|---|---|---|
| A | `0b4b096` | 9 inline sites norm → `identity.normalize.norm` (courier_info._norm, panel_roster._norm_token, telegram_approver _norm+2 inline, courier_resolver panel_packs 4×) + `test_norm_delegation_zp105b.py` |
| B | `955dab2` | `shift_notifications/worker.resolve_cid` — obliczenie score → `identity.score_worker_alias` (zachowane exact/ci, remis=None, logi RESOLVE_CID_AMBIGUOUS_*) |
| C | `a8b3225` | `panel_roster._score` → `identity.score_panel_roster` (profil ×10/×10 zachowany) |

## Dowody (ZERO zmiany zachowania)

- **Golden diff = 0 po każdym commicie.** Snapshot `worker.resolve_cid` + `panel_roster.match_name_to_cid` na 177 żywych nazwach (121 aliasów + 56 grafik), pobrany PRZED 1. edycją (pristine 44017e1), porównany po A/B/C: **worker_diff=0, panel_diff=0 (IDENTYCZNY)** za każdym razem.
- **report.py --parity (live read-only): worker 177/177, panel_roster 177/177, 0 rozjazdów** — po delegacji worker≡identity≡registry i panel≡identity≡registry.
- **Pełna suita: 4847 passed / 27 skipped / 10 xfailed / 0 failed** (baseline 44017e1 = 4773/27/10; +74 = `test_norm_delegation_zp105b`; skipy/xfaile bez zmian — ocena listą, nie sumą).
- **Kontrakty monkeypatch nienaruszone:** `test_resolve_cid_score_based` 16/16 (w tym 2 asercje logowania TIE/RESOLVED), `test_new_courier_pairing` (asercje `match_name_to_cid` + 4 monkeypatche `ncp.resolve_cid`) zielone.

## Świadomie odłożone (poza Fazą B)

- **common.py** — NIE edytowane: na 44017e1 brak kopii norm `.,;:` (tylko `rstrip(",.")` w parserze adresów). Site z briefu = stale po refaktorze.
- **`new_courier_pairing._resolve_cid_trusted`** — KOMPOZYCJA ZACHOWANA (decyzja lidera): woła zdelegowany modułowy `resolve_cid` + filtr goły-klucz identyczny z identity.bare_key_strict; bezpośrednia delegacja łamałaby 4 monkeypatche + logowanie przy zerowym zysku.
- **Krok 4** (przełączenie czytelników PLIKÓW kurier_ids/courier_names w courier_resolver/telegram/sla_tracker na Registry) — hot path silnika, wyższe ryzyko, osobny sprint.
- **Unifikacja profili** ×10/×5 (worker) vs ×10/×10 (panel_roster) — świadomie NIE zunifikowane (osobny temat za pomiarem+ACK).
- **Konsolidacja `courier_api.db`** (zdenormalizowany courier_name w 5 tabelach) — poza zakresem.

## Rollback

`git revert a8b3225 955dab2 0b4b096` — czysta delegacja, zachowanie bajt-identyczne, brak zmian danych/CID.
