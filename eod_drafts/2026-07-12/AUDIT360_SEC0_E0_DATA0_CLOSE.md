# Audit360 — zamknięcie SEC0 / E0 / DATA0

Status dokumentu: **CLOSED / SOURCE-PREP**. Data: 2026-07-12 UTC.
Wspólna baza trzech lane'ów:
`1cf6ae4bdc52223ff0accafdea5fdadd593c70cf`.

## Wynik w jednym zdaniu

SEC0 dostarczył i zintegrował bezpieczny audyt granicy hosta, natomiast E0 i
DATA0 dostarczyły zweryfikowane źródła przygotowawcze na osobnych branchach.
Żaden z lane'ów nie włączył nowej polityki, nie zmienił danych live i nie
wykonał restartu. E0 i DATA0 pozostają świadomie poza masterem, ponieważ ich
pełne uruchomienie wymaga najpierw domknięcia jawnych kontraktów trwałości.

## SEC0 — HOST BOUNDARY / CREDENTIAL

- Branch/push: `security/a360-sec0-host-boundary-truth` @
  `c30d4edc99426fcaa46be6b7cd5d9f83f1b99314`, clean, HEAD równy origin.
- Bezpieczny source został cherry-picknięty do dispatch `master` jako
  `c47031be9a4b16f5fc9a0a79fb72fcc94c914692`, wypchnięty i oznaczony tagiem
  `a360-sec0-source-integrated-20260712`.
- Nowe narzędzie tylko odczytu wykrywa publiczne wildcard listenery 8767/9222,
  klasyfikuje brak skutecznej reguły hosta oraz nigdy nie udaje lokalnym
  odczytem, że provider firewall jest potwierdzony.
- Bieżący werdykt narzędzia: `HOLD`. Potwierdzono publiczny IPv4 na 8767,
  publiczny IPv4+IPv6 na 9222 i brak udowodnionej skutecznej ochrony
  INPUT/DOCKER-USER; provider pozostaje `UNKNOWN`.
- Integracja do mastera niczego nie zamknęła sieciowo. Nie zmieniono bindu,
  firewalla, providera, kontenera, unitu ani poświadczenia.
- Targeted: 11 passed. Final DEFAULT: 5154 passed, 24 skipped, 8 xfailed,
  0 failed/XPASS. Final STRICT: 5104 passed, 74 skipped, 8 xfailed,
  0 failed/XPASS.

Faktyczna naprawa hosta wymaga osobnego ACK, drugiej działającej sesji
administracyjnej, bezpiecznej ścieżki wejścia, backupu, source manifestu
kontenera, dowodu provider firewall oraz testów allowed/denied z niezależnej
sieci dla IPv4 i IPv6. Starego poświadczenia nie wolno przywracać jako
rollbacku; rotacja zawsze przechodzi do nowej rewizji.

## E0 — EVENT RELIABILITY / FSM

- Branch: `reliability/a360-e0-event-fsm`.
- Commit implementacyjny: `b2a602755a101a991074d06d3c4e819b09531c3e`.
  Finalny HEAD/push z raportem: `451f092234a0e1ddccfdc87a84a82362f1e16f14`;
  worktree clean i HEAD równy originowi.
- Źródło łączy retry/DLQ i formalny FSM u jednego ownera, zachowując historyczne
  zachowanie, gdy wykonawcza polityka retry i enforcement są wyłączone.
- Efektywne stałe source: `AUTOMATIC_RETRY_ENABLED=False`,
  `SELECTED_RETRY_POLICY_ID=None`, `ORDER_FSM_OBSERVER_ENABLED=True` log-only,
  `ORDER_FSM_ENFORCEMENT_ENABLED=False`; worker retry nie istnieje.
- Focused: 72 passed. Szeroki targeted: 427 passed, 1 xfailed, 0 failed.
- Final DEFAULT 2026-07-12 00:44:30Z–00:49:15Z: 5189 passed, 24 skipped,
  8 xfailed, 0 failed/XPASS. Final STRICT 00:49:15Z–00:53:35Z:
  5139 passed, 74 skipped, 8 xfailed, 0 failed/XPASS.
- Nie wykonano migracji runtime, nie wybrano retry policy i niczego nie
  włączono. Branch nie jest gotowy do merge'u ani ON.

Twarde HOLD są architektoniczne, nie kosmetyczne: nieograniczone receipts,
utrata deduplikacji po retencji, brak kanonicznego event ID, brak trwałego
failure journal dla audit-only, efekt coordinator activation przed receipt,
brak replay audit→state, brak wspólnego `created_at` w kopercie oraz dwóch
wykonawców PICKED/DELIVERED dzielących jeden globalny receipt. E0 chroni
pojedynczy zapis stanu, ale nie udowadnia exactly-once całego call graphu.

## DATA0 — PRIVATE LEDGER / RETENTION

- Branch/push: `privacy/a360-data0-ledger-retention` @
  `a6ca337b53b33cf50c41ce1eff564d1cabae9396`, clean, HEAD równy origin.
- Domyślny `compat` deleguje do starego writera i zachowuje byte parity.
  `private` tworzy uwierzytelniony, pseudonimizowany artefakt 0600 i failuje
  głośno. `mirror` jest twardym HOLD przed pierwszym zapisem, bo bez outboxa
  dual-write nie jest retry-safe.
- Rotacja używa rename/reopen pod stabilnym lockiem; nie ma `copytruncate`.
  Retencja ma wyłącznie `would-delete`; kod kasowania i timer nie istnieją.
  Migracja apply pozostaje HOLD.
- Targeted: 126 passed, 1 istniejący skip. Final DEFAULT: 5175 passed,
  24 skipped, 8 xfailed, 0 failed/XPASS. Final STRICT: 5125 passed,
  74 skipped, 8 xfailed, 0 failed/XPASS.
- Syntetyczny koszt private względem legacy: +14,23% bajtów, gzip 2,584x,
  p95 redakcji 0,801 ms i p95 bezpiecznego appendu 0,731 ms. To nie jest
  ekstrapolacja na live corpus.

DATA0 nie wchodzi do mastera przed migracją wszystkich aktywnych direct
readerów, retry-safe outboxem dla ewentualnego mirror, decyzją B-05 o retencji
i backupach, zatwierdzonym sealerem dla pełnego wrażliwego replay oraz osobnym
ACK na klucz, migrację, chmod, deploy, restart lub delete.

## Wspólny dowód i wpływ na produkcję

Baseline na frozen base, 2026-07-11 23:05:55Z–23:10:40Z: 5143 passed,
24 skipped, 8 xfailed, 0 failed/XPASS, 147 warnings. Wszystkie pełne biegi były
serializowane przez `/tmp/ziomek_full_regression.lock`; ich przedziały UTC są
wejściem do sensitivity job 214. Próby DATA0 przerwane RC=143 nie są liczone
jako dowód.

Operacje live w tej fali: **zero** zmian flag, danych, praw, DB, logów,
systemd, bindu, firewalla, providera, credentialu, deployów i restartów.
Jedyna zmiana dostępna na masterze to read-only audytor SEC0 oraz dokumentacja.
Dlatego zachowanie decyzji Ziomka, HARD/SOFT, przydział kuriera, plan i SLA są
niezmienione.

## Bezkolizyjność, chronione pliki i rollback

SEC0 używał nowych artefaktów security/ops, E0 rodziny event/FSM, a DATA0
granicy ledger/world-record. Wspólne backlogi i memory pozostawały własnością
integratora. Zastany dirty dispatch
`eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`, dirty Papu
`papu_dispatch_bridge/restaurant_map.json`, niewersjonowany Papu
`DEPLOY_PROCEDURE.md` oraz chroniony
`daily_accounting/kurier_full_names.json` pozostały nietknięte.

- SEC0 source rollback: jawny revert `c47031b`; nie daje to rollbacku hosta,
  bo żadna operacja hosta nie zaszła.
- E0 rollback: pozostawić OFF i jawnie revertować `b2a6027`; DATA0 rollback:
  pozostawić `compat` i revertować `a6ca337`. Addytywnych schematów nie należy
  cofać destrukcyjnie.
- Nie ma backupu danych tej fali, ponieważ nie było zapisu ani migracji live.
- Żaden rollback nie obejmuje restartu `dispatch-telegram`.

## Następna kolejka

Trzy kolejne bezkolizyjne sprinty opisuje
`eod_drafts/2026-07-12/AUDIT360_NEXT_THREE_AFTER_SEC0_E0_DATA0.md`:

1. `A360-V214 CANARY-DISPOSITION` — uczciwy odczyt zaplanowanego werdyktu;
2. `A360-SEC1 HOST-REMEDIATION` — faktyczna granica hosta, wyłącznie po ACK;
3. `A360-E1 DURABLE EVENT OUTBOX` — usunięcie architektonicznych HOLD E0.
