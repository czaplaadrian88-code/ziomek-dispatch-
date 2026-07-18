# Fix recordera `courier_last_pos` (2026-07-18 wieczór) — EVIDENCE

GO Adriana: „Dawaj fix recordera courier_last_pos". Źródło: finding fali #7
(klasa #15/C10, `S7_SENTINEL_WAVE_EVIDENCE.md`): recorder nie nagrywał store'u
TTL-25min → replay czytał ŻYWY store → dzienny dryf pozycji no_gps
(`pool_total ±1` + osrm-missy; nocny „PARITY" bywał parity-bo-noc).

## Zmiana (wzorzec istniejący: `plans`/`eta_quantile`)

- `world_record._capture_courier_last_pos(out, fleet_cids)` — snapshot ZAWARTOŚCI
  store'a do rekordu (`live_inputs.courier_last_pos`), filtr do cid floty (wzór
  `plans`); fail-soft: brak store'a → pole nieobecne = rekord legacy-zgodny.
  Wywołanie w kontenerze capture obok eta/bias.
- `world_replay._serve_live_inputs`: `_redirect(courier_resolver,
  "COURIER_LAST_POS_PATH", li.get("courier_last_pos"))` — loader bez cache
  (cache_obj=None); **rekordy legacy bez pola → `_redirect` skip = passthrough
  do żywego** (zachowanie sprzed fixa, bez udawania determinizmu na starych).
- Guard `_store_blocked_under_test` w loaderze: redirect zmienia ścieżkę ≠ default
  → loader działa też pod pytest/replay (guard zaprojektowany wprost pod patch
  ścieżki — komentarz w resolverze).

## Dowody

- **Testy 4/4** `test_world_record_last_pos.py`: capture filtruje do floty ·
  fail-soft brak store'a (pole nieobecne) · redirect → loader czyta NAGRANE,
  nie żywe (przez PRAWDZIWE `_serve_live_inputs` z minimalnym li — pozostałe
  pola None→skip) · legacy passthrough (ścieżka nietknięta).
- Pełna regresja (łączna z falą #7): → Wyniki końcowe.
- **Dowód ŻYWY = nocny night-guard 02:00**: po restarcie at#219 (19:05) rekordy
  zawierają snapshot → jutrzejszy werdykt `world_replay_gate` na dzisiejszych
  wieczorno-nocnych rekordach będzie pierwszym uczciwym testem dziennego
  determinizmu (soft `pool_total`-klasa powinna zniknąć dla NOWYCH rekordów;
  stare rekordy legacy nadal dryfują — z definicji, odnotowane w rejestrze).

## DoD — tokeny

regresja: 5188 passed / 0 failed / 27 skipped / 8 xfailed (EXIT=0; wspólny frozen bieg z falą #7)
e2e: test przez PRAWDZIWY `_serve_live_inputs` (pełna ścieżka serve-inputs replayu z minimalnym rekordem) + żywy dowód forward = nocny gate na rekordach post-restart (at#219 19:05; werdykt jutro 02:00 → odczyt przy oknach pon.)
replay: klasa fixa = determinizm PRZYRZĄDU (nie zmiana decyzji silnika — capture/redirect poza decide()); pozytyw = zamknięcie luki #15 bramki world_replay (recorder-gap udokumentowany bisekcją w S7 evidence)
rollback: git revert (addytywne pole rekordu + skip-redirect; stare rekordy nietknięte)

N-D: dispatch_pipeline.py / core/* / feasibility_v2.py — decide() nietknięte (fix wyłącznie w recorderze/replayerze)
N-D: courier_resolver.py — loader/guard BEZ zmian (redirect używa istniejącej ścieżki-konstanty zaprojektowanej pod patch)
N-D: tools/world_replay_gate.py — konsument replay_one bez zmian (korzysta automatycznie)

## Wyniki końcowe

- Finalna pełna regresja (fala #7 + recorder, frozen): **5188 passed / 0 failed / 27 skipped / 8 xfailed** (EXIT=0, 299s); celowane 8/8 (4 recorder + 4 s7); py_compile OK.
