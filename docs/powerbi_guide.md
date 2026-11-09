# Building a Power BI report on LoadLens data

This guide turns the CSV export into a one-page Power BI report. It takes about an hour.

## 1. Export the data

Either:

- in the dashboard, open **Data & SQL** and click **All tables as CSV**, then unzip, or
- run `python -m loadlens.cli --out data/` and use the files in `data/csv/`.

## 2. Load the tables

In Power BI Desktop: **Get data → Text/CSV**, and load:

`zone`, `van`, `package`, `run`, `load_event`, `exception_log`, `zone_minute`, `van_fill_forecast`

The `v_` views are optional; the model below rebuilds them with measures.

## 3. Set up relationships

In **Model view**, create these (one-to-many, single direction):

| From (one) | To (many) |
|---|---|
| `zone[zone_id]` | `load_event[zone_id]`, `exception_log[zone_id]`, `zone_minute[zone_id]` |
| `van[van_id]` | `package[van_id]`, `van_fill_forecast[van_id]` |
| `package[package_id]` | `load_event[package_id]`, `exception_log[package_id]` |
| `run[run_id]` | `load_event[run_id]`, `exception_log[run_id]`, `zone_minute[run_id]`, `van_fill_forecast[run_id]` |

`zone`, `van` and `run` act as dimension tables; the rest are fact tables.

## 4. Add measures (DAX)

```dax
Packages = COUNTROWS ( load_event )

Loaded = CALCULATE ( [Packages], load_event[status] = "loaded" )

Missed Cutoff = CALCULATE ( [Packages], load_event[status] = "missed" )

Loaded % = DIVIDE ( [Loaded], [Packages] )

Misloads = CALCULATE ( [Packages], load_event[error_kind] <> "none" )

Misload Rate = DIVIDE ( [Misloads], [Loaded] )

Avg Wait (min) = AVERAGE ( load_event[wait_minutes] )

Wrong Van = CALCULATE ( [Packages], load_event[error_kind] = "wrong_van" )

Wrong Van Caught =
    CALCULATE ( COUNTROWS ( exception_log ), exception_log[exception_type] = "WRONG_VAN_CAUGHT" )

Catch Rate = DIVIDE ( [Wrong Van Caught], [Wrong Van] )

Peak Backlog = MAX ( zone_minute[backlog] )

Vans Overflow = CALCULATE ( COUNTROWS ( van_fill_forecast ), van_fill_forecast[status] = "overflow" )
```

Format `Loaded %`, `Misload Rate` and `Catch Rate` as percentages.

## 5. Build the page

| Visual | Fields |
|---|---|
| **Slicer** | `run[label]` (usual vs. optimized) |
| **Cards** (row across the top) | Loaded %, Missed Cutoff, Misload Rate, Catch Rate, Avg Wait (min) |
| **Line chart**: backlog over time | Axis `zone_minute[minute]`, values `Peak Backlog`, legend `zone[zone_id]` |
| **Clustered column**: zone comparison | Axis `zone[zone_id]`, values `Packages`, legend `run[label]` (no slicer filter) |
| **Bar chart**: exceptions by type | Axis `exception_log[exception_type]`, values count of `exception_id` |
| **Bar chart**: van fill | Axis `van[van_id]`, values `van_fill_forecast[fill_pct]`, conditional color by `status` |
| **Table**: exception log | `minute`, `zone_id`, `exception_type`, `severity`, `package_id`, `description` |

Tips:

- Keep colors consistent: one color per zone across every visual.
- Use red and amber only for overflow and watch, and always with a text label.
- Add a text box stating that all data is synthetic.

## 6. Save and share

Save the report as `docs/loadlens_report.pbix` in the repository and add a screenshot to the README.
