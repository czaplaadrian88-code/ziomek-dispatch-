"""Tests — Bar Eljot exception H = pobrania_total - eljot_pobrania + eljot_cena (Step 2).

Cases planowane:
 - test_adrian_cit_23_04_sanity   — 1051.30/57.00/20.00 → H=1014.30
 - test_no_eljot_orders           — 0/0 → H=suma_pobran_total
 - test_eljot_cena_dynamic        — cena=25.00 (nie 20.00) → poprawnie
 - test_multiple_eljot_orders     — 3×20 → eljot_cena=60.00
"""
