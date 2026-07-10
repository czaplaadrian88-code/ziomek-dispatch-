# Decyzje biznesowe i unknowns

## Decyzje wymagające Adriana

1. Czy furtka gold p80 ma zostać natychmiast wyłączona zgodnie z D3, czy nowszy
   trade-off ma formalnie zmienić kanon? Technika nie może wybrać po cichu.
2. Jak always-propose ma oznaczać plan >40/unknown: ALERT least-damage, defer czy
   blokada? Nie wolno mylić „jawny alert” z „feasible”.
3. Jaka jest polityka R27 w normal/alarm oraz definicja wejścia w ALARM?
4. Czy katalog floty przed logowaniem jest wymaganym UX? Jeśli tak, jaki minimalny
   zakres danych i model enrollmentu?
5. Którzy klienci naprawdę potrzebują surowego portu API, zanim bind zostanie
   ograniczony do loopback?
6. Retencja dokładnych world records/GPS/adresów i owner prawny.
7. Akceptowalne RTO/RPO oraz koszt drugiego hosta/failover.

## Unknowns techniczne

- aktualny provider-side Cloud Firewall z 10.07;
- pełny restore z off-site backupu;
- fizyczny pickup/handoff jako KPI ETA;
- realny wpływ `manual_overrides` race po pełnej mapie writerów;
- przyczyna każdego OSRM miss w world replay;
- coverage network isolation suity STRICT.

Każdy unknown ma pozostać UNKNOWN do pomiaru; nie wypełniać go intuicją modelu.
