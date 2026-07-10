# Operacje, deploy, backup i restore

## Stan usług

Pięć kluczowych usług było active/running, bez restart loopa. Parser v2 był
healthy. `atq` było puste; karta claim-ledger została prawidłowo wygenerowana i
zwróciła WAIT z powodu zbyt krótkiego okna.

## Zweryfikowane findings

- `OPS-01` CONFIRMED/P2: dwa listenery SSH; konfiguracja odtworzy po reboot tylko
  nowy port. Stary proces pozostaje w cgroup przy `KillMode=process`, więc opis
  „całkiem poza systemd” był przesadzony. Brak bieżącej awarii.
- `OPS-02` CONFIRMED/P2: panel i API kuriera nie mają `OnFailure` ani bezpośredniego
  liveness. `Restart=` łagodzi awarię, lecz nie gwarantuje alertu crash-loop.
- `OPS-05` PARTIAL/P2: brak hostowego backstopu dla kilku bindów; aktualny Cloud
  Firewall wymaga zewnętrznego/provider-side potwierdzenia.

## Backup/restore

Backupi i sentinel raportują sukces, a klucze DR zostały według zatwierdzonego
handoffu skopiowane poza host. To nie dowodzi odtworzenia. Restore game day na
izolowanej kopii pozostaje P1 backlogiem jakości operacyjnej.

## Deploy discipline

Audyt niczego nie wdraża. Przyszły release: backup → import/compile → kanoniczna
regresja → collision preflight → jawny commit/tag → ACK → jeden restart → health,
PID/NRestarts/fingerprint i smoke → rollback probe.
