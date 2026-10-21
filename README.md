# Blended RPE dashboard

A local dashboard that shows what Leased Lines, Broadband and Energy are really
worth once every lead is credited to the journey it started in.

Everything runs on your machine. No lead export or other CSV is ever committed:
`.gitignore` excludes `data/` and every `.csv`.

## Using it

1. Download `dashboard.html` and save it somewhere on your machine.
2. Double-click it. It opens in your browser (Chrome or Edge work best).
3. Drag your `enquiries-master-anon-*.csv` exports into the box at the top left.

That's it. No install, no internet needed. The exports are read inside the
browser and kept in the browser's storage on this machine, so next time you
open the file they're still there and only new exports need adding. Nothing is
sent anywhere.

To check the numbers, open **Acceptance checks** on the page and click
**Run checks**. With exports covering 1 June to 21 October 2026, all 15 should
pass.

## Headline

The top of the page has two parts. Both follow the date range and filters.

1. **Routes**: pick a route from the dropdown (it only lists routes found in
   the data, Leased Lines to Broadband first). One sentence says how many
   enquiries took that route and what they were worth, then a table compares
   reported and actual RPE for both channels. Cross sells are described as
   extra leads rather than replacements.
2. **Broadband step by step**: Broadband as reported today, less the Leased
   Lines customers routed in, its own leads split into broadband and
   broadband-mobile, plus the energy cross sells from its customers, ending at
   Broadband blended (the real figure). Each step shows its effect on RPE, and
   there's a table by period you can download.

Change the defaults under `headline` in the EDIT HERE section, and the friendly
form type names under `formTypeLabels`.

## Changing settings

Open `dashboard.html` in a text editor (Notepad is fine) and look for
`EDIT HERE` near the top. That section holds the form id and form type
mappings, the channels shown, traffic source rules and event markers. Save and
reload the page.

## How it's built

| File | What it does |
|---|---|
| `dashboard.html` | The whole dashboard: upload, processing, cards, trend, breakdown, data health, export. Settings at the top |
| `config.toml`, `loader.py`, `attribution.py`, `metrics.py`, `pipeline.py`, `settings.py`, `formatting.py` | The same logic in Python, used as a second implementation to test the page against. Keep `config.toml` in step with the settings in the page (a test checks this) |
| `tests/` | Automated tests |

## Adding a new form

When the data health panel flags an unmapped form id (or form type), add it to
the right channel in the `EDIT HERE` section of `dashboard.html` (and in
`config.toml`), save and reload.

## Tests

```
pip install -r requirements.txt
pytest
```

- `tests/test_html_engine.py` runs the engine inside `dashboard.html` with
  Node and checks it gives exactly the same figures as the Python code.

- `tests/test_loader.py` and `tests/test_attribution.py` use small made-up data
  generated in memory, so they always run.
- `tests/test_acceptance.py` holds the acceptance checks from the brief. They
  need the real exports covering 1 June to 21 October 2026, and are skipped
  when none are found. Run them with:

```
RPE_EXPORTS_DIR=/path/to/exports pytest tests/test_acceptance.py
```

(or just `pytest` once the exports are uploaded into `data/exports/`).

## Still to confirm against `reference_attribution.py`

The reference script was not available when this was built, so these parts
follow the brief and need checking against it:

- `source_group`: the traffic source rules in `config.toml` are a best guess.
- Campaign type: brand is any campaign containing "brand", paid search
  otherwise is "Product search".
- Table A only lists the three dashboard channels. Add the other channels
  (invoice and so on) so their leads aren't shown as Unmapped.
- The session column is assumed to be `session_id` (change `columns` in the EDIT HERE section, and `[columns]` in `config.toml`).

## Parked

Spend and site visits drop in as extra entries in `CARD_FIGURES` and
`BREAKDOWN_GROUPS` in `dashboard.html`, fed from extra fields in `addRatios`.
