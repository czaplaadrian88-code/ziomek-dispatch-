# Bag integrity fix shadow delta — 2026-04-19

Ground truth: `orders_state.json` NOW (@2026-04-19T14:10:28Z) jako oracle dla final status.

Phantom = order w `bag_context` propozycji ma NOW status `delivered`/`returned_to_pool`/`cancelled`. Znaczy że w momencie propozycji pipeline ufał stale `status=assigned` dla orderu już terminalnego w panelu.


## last_4h — 135 PROPOSE decisions

| metric | count | % |
|---|---|---|
| decisions with bag_context | 394 | — |
| PHANTOM in best bag_context | 49 | 36.3% |
| PHANTOM in any candidate | 113 | 83.7% |
| total phantom entries (bag size inflation) | 613 | — |

**Top 10 phantom order IDs:**

- `467009` 'Chicago Pizza': **52×** phantom
- `467055` 'Baanko': **39×** phantom
- `467057` 'Rany Julek': **39×** phantom
- `467000` 'Rukola Kaczorowskiego': **36×** phantom
- `467051` 'Pani Pierożek': **36×** phantom
- `467058` 'Raj': **36×** phantom
- `467005` 'Zapiecek': **30×** phantom
- `467042` 'Trójkąty i Kwadraty': **30×** phantom
- `467049` 'Trójkąty i Kwadraty': **30×** phantom
- `467034` 'Chicago Pizza': **26×** phantom

**Top 10 couriers with phantom entries:**

- cid=179 ('Gabriel'): **160×**
- cid=509 ('Dariusz M'): **102×**
- cid=441 ('Sylwia L'): **80×**
- cid=508 ('Michał Li'): **60×**
- cid=400 ('Adrian R'): **60×**
- cid=520 ('Michał Rom'): **40×**
- cid=387 ('Aleksander G'): **30×**
- cid=484 ('Andrei K'): **25×**
- cid=413 ('Mateusz O'): **22×**
- cid=518 ('Michał Ro'): **22×**


## last_24h — 277 PROPOSE decisions

| metric | count | % |
|---|---|---|
| decisions with bag_context | 414 | — |
| PHANTOM in best bag_context | 51 | 18.4% |
| PHANTOM in any candidate | 129 | 46.6% |
| total phantom entries (bag size inflation) | 641 | — |

**Top 10 phantom order IDs:**

- `467009` 'Chicago Pizza': **52×** phantom
- `467055` 'Baanko': **39×** phantom
- `467057` 'Rany Julek': **39×** phantom
- `467000` 'Rukola Kaczorowskiego': **36×** phantom
- `467051` 'Pani Pierożek': **36×** phantom
- `467058` 'Raj': **36×** phantom
- `467005` 'Zapiecek': **30×** phantom
- `467042` 'Trójkąty i Kwadraty': **30×** phantom
- `467049` 'Trójkąty i Kwadraty': **30×** phantom
- `467034` 'Chicago Pizza': **26×** phantom

**Top 10 couriers with phantom entries:**

- cid=179 ('Gabriel'): **160×**
- cid=509 ('Dariusz M'): **105×**
- cid=441 ('Sylwia L'): **86×**
- cid=508 ('Michał Li'): **62×**
- cid=400 ('Adrian R'): **60×**
- cid=520 ('Michał Rom'): **55×**
- cid=387 ('Aleksander G'): **30×**
- cid=484 ('Andrei K'): **25×**
- cid=413 ('Mateusz O'): **22×**
- cid=518 ('Michał Ro'): **22×**


## all_time — 1162 PROPOSE decisions

| metric | count | % |
|---|---|---|
| decisions with bag_context | 414 | — |
| PHANTOM in best bag_context | 51 | 4.4% |
| PHANTOM in any candidate | 129 | 11.1% |
| total phantom entries (bag size inflation) | 641 | — |

**Top 10 phantom order IDs:**

- `467009` 'Chicago Pizza': **52×** phantom
- `467055` 'Baanko': **39×** phantom
- `467057` 'Rany Julek': **39×** phantom
- `467000` 'Rukola Kaczorowskiego': **36×** phantom
- `467051` 'Pani Pierożek': **36×** phantom
- `467058` 'Raj': **36×** phantom
- `467005` 'Zapiecek': **30×** phantom
- `467042` 'Trójkąty i Kwadraty': **30×** phantom
- `467049` 'Trójkąty i Kwadraty': **30×** phantom
- `467034` 'Chicago Pizza': **26×** phantom

**Top 10 couriers with phantom entries:**

- cid=179 ('Gabriel'): **160×**
- cid=509 ('Dariusz M'): **105×**
- cid=441 ('Sylwia L'): **86×**
- cid=508 ('Michał Li'): **62×**
- cid=400 ('Adrian R'): **60×**
- cid=520 ('Michał Rom'): **55×**
- cid=387 ('Aleksander G'): **30×**
- cid=484 ('Andrei K'): **25×**
- cid=413 ('Mateusz O'): **22×**
- cid=518 ('Michał Ro'): **22×**


## Interpretation

Fix (V3.14 TTL filter @ build_fleet_snapshot) wyeliminuje phantom orders z bag_context niezależnie od panel_watcher reconcile lag. Assigned >90 min bez `picked_up_at` (lub picked_up >90 min bez delivered) wykluczone z `active_bag`.

**Limitation**: Strategy sprawdza status NOW — może niedoszacować phantom count dla propozycji z ostatnich minut (panel_watcher jeszcze nie reconciliował).


## Pending LIVE

Fix committed (3 commits + 3 tagów, master pending). WYMAGA restart `dispatch-panel-watcher` + `dispatch-shadow`. `dispatch-telegram` nie wymaga.
