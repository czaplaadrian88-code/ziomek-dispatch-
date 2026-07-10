# Bezpieczeństwo, prywatność i supply chain

## Zweryfikowane granice

- `BEZP-02`: endpointy status/arrival wymagają sesji, lecz nie mają wspólnego
  guarda ownership order→CID. Indywidualnie P2; w publicznym łańcuchu wpływ rośnie.
- `BEZP-04`: katalog kurierów jest dostępny przed logowaniem. Per-IP limiter jest
  już LIVE (20/15 min), więc stara matematyka brute-force była przeterminowana;
  pozostaje disclosure i decyzja UX, nie prosty „dodaj auth”.
- `OPS-05`: proces API nasłuchuje na interfejsie hosta; publiczne HTTPS przez nginx
  jest pewne. Stan Cloud Firewall z 10.07 nie jest odczytywalny z hosta; ostatni
  zatwierdzony dowód zewnętrzny pochodził z 05.07.
- hasło administracyjne było przechowywane w drop-inie o zbyt szerokim trybie.
  Raport nie zapisuje lokalizacji ani wartości; naprawa to bezpieczny carrier i
  rotacja za osobnym ACK, nie kopiowanie treści.

## Prywatność

World records, decision ledgers i GPS mają różne retencje oraz zakres PII.
Docelowy kontrakt powinien definiować purpose, odbiorców, pseudonimizację, tryb
0600, retencję i usuwanie. Sam logrotate nie rozwiązuje podstawy prawnej.

## Supply chain

Nie znaleziono kompletnego, odtwarzalnego manifestu wszystkich środowisk.
`requirements-dispatch-venv.txt` pokrywa rdzeń, ale nie cały test/ML/API stack.
Nie wykonano masowego upgrade’u ani automatycznej interpretacji CVE; propozycja
jest w raporcie 15.

## Priorytet

Najpierw ownership guard i zewnętrzna weryfikacja ekspozycji, potem minimalizacja
katalogu pre-login i host-level defense-in-depth. Każda zmiana sieci/usługi wymaga
oddzielnego planu oraz ACK.
