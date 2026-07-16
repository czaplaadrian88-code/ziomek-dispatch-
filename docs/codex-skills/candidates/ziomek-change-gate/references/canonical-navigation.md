# Kanoniczna nawigacja Ziomka

## Zasady

- Przejdź prelude i bootstrap w podanej kolejności przed planem, edycją lub
  werdyktem.
- Czytaj wybrane źródła w całości, z wyjątkiem jawnie ograniczonych pierwszych
  86 linii `CLAUDE.md` i początku/current handoff timeline.
- Nie dodawaj innego globalnego źródła kanonicznego. Po bootstrapie dobierz
  pliki zadaniowe wyłącznie przez `CODEMAP`.
- Źródła `CONDITIONAL` czytaj tylko dla wskazanego zakresu.

## Prelude instrukcji

<!-- ZCG_AGENTS_PRELUDE_START -->
1. MANDATORY | ROOT_AGENTS | [global instructions](</root/AGENTS.md>)
2. MANDATORY | CODEX_AGENTS | [Codex global instructions](</root/.codex/AGENTS.md>)
<!-- ZCG_AGENTS_PRELUDE_END -->

## Uporządkowany bootstrap

<!-- ZCG_BOOTSTRAP_ORDER_START -->
1. MANDATORY | CLAUDE_86 | [pierwsze 86 linii kanonicznego CLAUDE](</root/.openclaw/workspace/scripts/dispatch_v2/CLAUDE.md>)
2. MANDATORY | CODEMAP | [mapa kodu](../../../../../docs/CODEMAP.md)
3. MANDATORY | ARCHITECTURE | [architektura repo](../../../../../docs/ARCHITECTURE.md)
4. MANDATORY | ZIOMEK_ARCHITECTURE | [kanon architektury](../../../../../ZIOMEK_ARCHITECTURE.md)
5. MANDATORY | ZIOMEK_INVARIANTS | [inwarianty](../../../../../ZIOMEK_INVARIANTS.md)
6. MANDATORY | ZIOMEK_DEFINITION_OF_DONE | [definition of done](../../../../../ZIOMEK_DEFINITION_OF_DONE.md)
7. MANDATORY | MEMORY_INDEX | [indeks pamięci](</root/.claude/projects/-root/memory/MEMORY.md>)
8. MANDATORY | TODO_MASTER | [bieżące zadania](</root/.claude/projects/-root/memory/todo_master.md>)
9. MANDATORY | SPRINT_TIMELINE | [początek i CURRENT HANDOFF](</root/.claude/projects/-root/memory/sprint_timeline.md>)
10. MANDATORY | SHADOW_JOBS | [rejestr jobów](</root/.claude/projects/-root/memory/shadow-jobs-registry.md>)
11. MANDATORY | BUSINESS_CANON | [kanon reguł](</root/.claude/projects/-root/memory/ZIOMEK_REGULY_KANON.md>)
12. MANDATORY | CHANGE_PROTOCOL | [Przykazanie numer zero](</root/.claude/projects/-root/memory/ziomek-change-protocol.md>)
13. MANDATORY | BACKLOG | [repozytoryjny backlog](../../../../../ZIOMEK_BACKLOG.md)
14. CONDITIONAL | HANDOVER_MAP | [mapa wiedzy dla infra, topologii, handoffu lub cross-project](</root/handover/MAPA_WIEDZY.md>)
15. CONDITIONAL | HANDOVER_TODO | [lista handover dla infra, topologii, handoffu lub cross-project](</root/handover/CO_TRZEBA_ZROBIC.md>)
16. CONDITIONAL | DECISION_RECORD | [właściwy ADR lub ODR dla decyzji architektonicznej](../../../../../docs/decisions/)
17. DYNAMIC | CODEMAP_SELECTED_TASK_FILES | CODEMAP_SELECTED_TASK_FILES
<!-- ZCG_BOOTSTRAP_ORDER_END -->

## Precedencja sprzeczności

1. Najnowsza jawna decyzja właściciela, z weryfikacją scope i revoke.
2. Efektywny runtime, jeżeli odczyt jest dozwolony.
3. Kod aktualnego HEAD, testy i inwarianty.
4. Kanon, ADR/ODR i aktualny handoff.
5. Backlog i dokumentacja techniczna.
6. Komentarze, stare raporty i snapshoty.

Nazwij każdą rozbieżność. Nie czytaj sekretów ani PII. Bezpieczny read-only
runtime baseline odczytuj, gdy mieści się w jawnie zleconym scope; `N-D` stosuj,
gdy task go zabrania albo ochrona danych blokuje odczyt. Odczyt nie nadaje
capability ani prawa do mutacji.
