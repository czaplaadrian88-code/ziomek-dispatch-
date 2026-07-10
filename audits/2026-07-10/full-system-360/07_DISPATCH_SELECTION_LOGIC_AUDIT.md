# Audyt logiki selekcji

Kolejność kontraktowa to HARD feasibility → SOFT score → selekcja w grupie →
best-effort/werdykt. Wspólny `objm_lexr6` i strażniki warstw ograniczyły dawny
dryf. Always-propose jest świadomym kontraktem, ale nie może ukrywać złamania
HARD pod etykietą zwykłego feasible.

## Potwierdzone osie do rozstrzygnięcia

- `FEAS-01`: żywa furtka gold≤4 zmienia wartość używaną przez bramkę R6. To
  konflikt z nowszym kanonem D3; potrzebny osobny ACK przed flipem.
- `FEAS-02`: best-effort ma ścieżki bez pełnych danych i fallback do surowego
  zwycięzcy. Ponowna kontrola obniżyła wpis do PARTIAL/P2; właściwa semantyka
  ALERT/defer wymaga osobnego golden case i decyzji biznesowej.
- `FLAG-01`: klucz carry-chain w JSON nie jest źródłem wartości konsumenta;
  przyszły flip byłby no-op.
- `BLIZ-01`: serializer zakłada pozycję best w tablicy kandydatów; ścieżki
  re-order mogą naruszyć to założenie.

## Negatywne wyniki

- Nie znaleziono nowej inwersji score nad feasibility w głównym lejku.
- Lex-qual i route-order są dziś w znacznie lepszym stanie niż historyczne mapy;
  nie należy wskrzeszać opisów „3 kopie lex” bez świeżego grepa.
- Brak GPS nie powinien być ponownie „naprawiany” karą rankingową; kanon wymaga
  równego traktowania, a niewykonalność pozostaje osobnym HARD.
