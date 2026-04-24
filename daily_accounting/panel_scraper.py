"""Daily Accounting — panel scraper (Step 2+).

Scrape per-courier orders from gastro.nadajesz.pl:
 - main call (all companies) → ilość_zleceń, suma_pobran, suma_platnosci_karta
 - Bar Eljot call (company=27) → eljot_pobrania, eljot_cena

Reuses session logic z dispatch_v2.panel_client.login().
"""
