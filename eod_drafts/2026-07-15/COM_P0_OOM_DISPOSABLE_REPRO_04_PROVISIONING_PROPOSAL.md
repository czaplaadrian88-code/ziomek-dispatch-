# COM-P0-OOM-DISPOSABLE-REPRO-04 — propozycja fazy provisioningowej

Status: `PROPOSAL_ONLY / HOLD`; bez tworzenia hosta, obrazu, sieci, kontenera
lub procesu.

## Cel i zakres

Faza ma wyłącznie przygotować i odczytowo atestować jeden disposable NON-PROD
host oraz exact image już obecny offline. Nie uruchamia reprodukcji. Nie dotyka
produkcji, OpenClaw, systemd, Compose, kanałów, recovery ani repo kodu.
Budżet provisioningowy i reguły egress są zapisane hash-bound w receipt i
muszą odpowiadać polom `budget_sha256` oraz `egress_deny_receipt_sha256`
manifestu; rozbieżność zatrzymuje fazę.

## Wymagane decyzje właściciela

- wskazanie hosta/provider oraz maksymalnego okna i budżetu;
- potwierdzenie, że host może zostać całkowicie usunięty po fazie;
- akceptacja lokalnego obrazu tylko przez exact digest (bez pull/build/load);
- akceptacja retencji i szyfrowania synthetic evidence;
- wybór: `PROVISION_ONLY` albo anulowanie bez skutków;
- osobny ACK dopiero później dla execution run (nie wynika z provisioning ACK).

## Koszt i limity (estymata, nie faktura)

- jednorazowy disposable host: zwykle 1–2 h czasu operatora + koszt instancji
  za maks. 4 h; dokładna cena zależy od wskazanego providera;
- provisioning i atestacja: maks. 30 min wall / 15 CPU-min;
- transfer: 0, bo pull/build/load obrazu zabronione;
- storage: maks. 2 GiB encrypted synthetic artifacts, TTL 24 h;
- cleanup reserve: 15 min i nie może zostać zużyte przez test;
- przekroczenie dowolnego limitu = HOLD i zachowanie hosta do ręcznej decyzji.

## Bramki i kolejność

1. Owner wskazuje host/provider i zatwierdza budżet.
2. Atestator potwierdza odrębny machine-id, Docker endpoint/storage root,
   runtime, cgroup, userns, seccomp/AppArmor oraz host-level egress deny.
3. Read-only sprawdzenie, że exact image digest jest już lokalny; brak = HOLD.
4. Utworzenie prywatnego, pustego katalogu fixture/evidence i walidacja
   materializera oraz manifest template; bez startu kontenera.
5. Niezależny review provisioning receipt; CTO gate `PROVISION_READY`.
6. Owner może przyjąć provisioning receipt albo zamknąć host. To nie daje prawa
   do execution; execution wymaga osobnego runbooku, manifestu, nonce i ACK.

Rollback provisioning: zniszczyć disposable host i prywatne artefakty według
receipt; nie ma rollbacku produkcyjnego, bo produkcja nie jest dotykana.
