# Blended RPE dashboard

A local dashboard that shows what Leased Lines, Broadband and Energy are really
worth once every lead is credited to the journey it started in.

Everything runs on your machine. Lead exports are kept in `data/exports/` and
are never committed: `.gitignore` excludes `data/` and every `.csv`.

## Set up

```
pip install -r requirements.txt
streamlit run app.py
```

Then drag one or more `enquiries-master-anon-*.csv` exports into the upload
area at the top of the sidebar.

## Files

| File | What it does |
|---|---|
| `app.py` | The Streamlit dashboard |
| `config.toml` | Everything Rich edits: form id and form type mappings, channels shown, traffic source rules, event markers, column names |
| `loader.py` | Upload checks, saving, reading, date parsing, dedupe and caching. The only module that knows about CSVs, so a database can replace it later |
| `attribution.py` | Landed as, Started in, cross sell links, same-session follow-ons, form starts, mapping health |
| `metrics.py` | Filters, periods, reported / flattened / blended figures, breakdown, rolling RPE |
| `pipeline.py` | Joins loader and attribution |
| `formatting.py` | Money, counts and change formatting |

## Adding a new form

When the data health panel flags an unmapped form id (or form type), add it to
the right channel in `config.toml` and restart the dashboard.

## Tests

```
pytest
```

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
- The session column is assumed to be `session_id` (change under `[columns]`).

## Parked

Spend and site visits drop in as extra entries in `CARD_METRICS` and
`BREAKDOWN_COLUMNS` in `app.py`, fed from extra fields in
`metrics._add_ratios`.
