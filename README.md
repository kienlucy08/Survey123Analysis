# Survey123 Scope of Work Analytics

A local dashboard that pulls Survey123 submissions from every survey in your
ArcGIS org, rolls them up into your **scopes of work** (Inspection, Close Out,
etc.), and shows usage and growth for a time frame you pick — instead of
checking each survey's usage separately in AGOL.

## How it's organized

- `analysis/auth.py` — logs into your ArcGIS org via the REST API (`generateToken`).
- `analysis/fetch.py` — queries a feature layer's `/query` endpoint, paginating as needed.
- `analysis/scope.py` — reads `config/scopes.yaml` and tags each survey's records with the scope of work they belong to.
- `analysis/aggregate.py` — usage totals, trends over time, and period-over-period growth.
- `analysis/pipeline.py` — wires the above together and caches the result locally (`data/cache/`).
- `analysis/sample_data.py` — synthetic data shaped like real Survey123 output, so the dashboard runs with zero setup.
- `dashboard/app.py` — the Streamlit UI.

## Quickstart (sample data, no credentials needed)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run dashboard/app.py
```

The sidebar will show "Live ArcGIS credentials/config not found — showing
sample data." until you connect your org (below).

## Connecting your real ArcGIS org

1. Copy `.env.example` → `.env` and fill in your portal URL + credentials.
   `.env` is gitignored — never commit it.
2. Copy `config/settings.example.yaml` → `config/settings.yaml` and adjust if
   your surveys use different date/creator field names.
3. Copy `config/scopes.example.yaml` → `config/scopes.yaml`. This is the file
   that defines what a **scope of work** is made of. For each scope, list the
   surveys (feature layers) that roll up into it:
   - `item_id` / `layer_url` — from the feature layer's item page in AGOL
     ("View" → URL of the layer, or Item Details → URL).
   - `purpose_field` — the field in that survey holding a "purpose" value
     (defaults to `purpose`). Leave `purpose_values` empty to count every
     record from that survey toward the scope; set it to specific values if
     one survey's data is split across more than one scope.
4. Restart the dashboard and switch "Source" to **Live ArcGIS org** in the
   sidebar.

Pulled data is cached locally as parquet (`data/cache/`, gitignored) for
`cache_max_age_hours` (default 6h) so filtering the dashboard doesn't
re-query ArcGIS every time. Use the **Refresh data** button to force a pull.

## What the dashboard shows

- **KPIs** — submissions in the selected time frame, growth vs. the prior
  period of equal length, active scopes, unique contributors, avg. photos
  per submission.
- **Usage Trends** — submissions over time per scope of work.
- **By Scope of Work** — totals per scope, and a purpose/survey breakdown
  within a scope you pick (e.g. within Inspection: Compound, Structure
  Flight, Guy Facilities, Plumb & Twist).
- **Growth** — current vs. prior period counts and % change per scope.
- **Team & Photos** — new vs. returning contributors, a top-contributors
  leaderboard (submissions + avg. photos per person), and a photos-per-submission
  distribution. Photo counts come from each record's ArcGIS attachments (real
  field photos), not a form field.
- **Raw Data** — the filtered records, with CSV export.
- **Reports** — a "Generate PDF report for this view" button for whatever
  time frame/scopes are selected, plus a monthly archive: one PDF per
  calendar month, saved automatically to `data/reports/` (gitignored)
  whenever data refreshes. Completed months are generated once and left
  alone; the current month regenerates every refresh.

## Extending

Add a new scope of work by adding an entry to `config/scopes.yaml` — no code
changes needed as long as the surveys share the standard `CreationDate` /
`Creator` fields (configurable in `config/settings.yaml` if not).
