"""Store a scenario in a relational database and export it for BI tools.

* ``build_database`` writes everything into SQLite using ``sql/schema.sql``.
* ``export_csv`` writes one CSV per table, ready to load into Power BI or Excel.
* ``run_readonly_query`` lets the dashboard run user SQL safely (SELECT only).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .pipeline import RunResult, ScenarioResult

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
MAX_QUERY_ROWS = 5000

PRESET_QUERIES = {
    "Zone scorecard (both layouts)": """
SELECT run_label, zone_id, packages, loaded, missed, misloads,
       misload_rate_pct, avg_wait_min, packages_recirculated
FROM v_zone_performance
ORDER BY run_label, zone_id;""",
    "Exceptions by type": """
SELECT run_label, exception_type, severity, exceptions, detected_in_real_time
FROM v_exception_summary
ORDER BY run_label, exceptions DESC;""",
    "Vans at risk of overflow": """
SELECT van_id, route_code, zone_id, planned_packages, planned_volume_m3,
       capacity_m3, fill_pct, status, recommendation
FROM v_van_fill
WHERE run_label = 'baseline' AND status <> 'ok'
ORDER BY fill_pct DESC;""",
    "Top 10 vans by misloads": """
SELECT le.loaded_van_id AS van_id, COUNT(*) AS misloaded_into
FROM load_event le
JOIN run r ON r.run_id = le.run_id
WHERE r.label = 'baseline' AND le.error_kind = 'wrong_van'
GROUP BY le.loaded_van_id
ORDER BY misloaded_into DESC
LIMIT 10;""",
    "Misload rate vs. belt pressure": """
-- Does the error rate rise when the belt backs up?
WITH le AS (
    SELECT le.*, zm.backlog
    FROM load_event le
    JOIN zone_minute zm
      ON zm.run_id = le.run_id AND zm.zone_id = le.zone_id AND zm.minute = le.load_minute
    WHERE le.status = 'loaded' AND le.run_id = 1
)
SELECT CASE WHEN backlog < 10 THEN '0-9'
            WHEN backlog < 20 THEN '10-19'
            WHEN backlog < 30 THEN '20-29'
            ELSE '30+' END                       AS backlog_band,
       COUNT(*)                                  AS packages_loaded,
       SUM(error_kind <> 'none')                 AS misloads,
       ROUND(100.0 * SUM(error_kind <> 'none') / COUNT(*), 2) AS misload_rate_pct
FROM le
GROUP BY backlog_band
ORDER BY backlog_band;""",
    "Priority packages not loaded": """
SELECT p.package_id, p.van_id, p.size_class, p.arrival_minute, le.zone_id
FROM load_event le
JOIN package p ON p.package_id = le.package_id
JOIN run r ON r.run_id = le.run_id
WHERE r.label = 'baseline' AND le.status = 'missed' AND p.is_priority = 1
ORDER BY p.arrival_minute;""",
}


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == bool:
            out[col] = out[col].astype(int)
    return out.astype(object).where(out.notna(), None)


def _insert(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> None:
    df = _prep(df)
    cols = ", ".join(df.columns)
    marks = ", ".join("?" for _ in df.columns)
    conn.executemany(f"INSERT INTO {table} ({cols}) VALUES ({marks})", df.itertuples(index=False, name=None))


def _insert_run(conn: sqlite3.Connection, run_id: int, run: RunResult) -> None:
    conn.execute("INSERT INTO run (run_id, shift_id, label) VALUES (?, 1, ?)", (run_id, run.label))
    _insert(conn, "van_assignment",
            pd.DataFrame({"run_id": run_id, "van_id": list(run.assignment), "zone_id": list(run.assignment.values())}))

    le = run.loads[["package_id", "zone_id", "status", "load_minute", "wait_minutes", "loaded_van_id",
                    "loaded_shelf", "error_kind", "recirculations", "sensor_read", "sensed_van_id"]].copy()
    for col in ("load_minute", "wait_minutes", "loaded_shelf"):
        le[col] = le[col].astype("Int64")
    le.insert(0, "run_id", run_id)
    _insert(conn, "load_event", le)

    if len(run.exceptions):
        ex = run.exceptions.copy()
        ex.insert(0, "run_id", run_id)
        _insert(conn, "exception_log", ex)

    zm = run.timeseries.copy()
    zm.insert(0, "run_id", run_id)
    _insert(conn, "zone_minute", zm)

    vf = run.van_fill[["van_id", "zone_id", "planned_packages", "planned_volume_m3", "planned_weight_kg",
                       "priority_packages", "fill_pct", "spare_m3", "status", "recommendation"]].copy()
    vf.insert(0, "run_id", run_id)
    _insert(conn, "van_fill_forecast", vf)


def build_database(result: ScenarioResult, path: str | Path = ":memory:") -> sqlite3.Connection:
    """Create the schema and load one scenario (baseline + optimized runs)."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).unlink(missing_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    cfg = result.config
    conn.execute(
        "INSERT INTO shift VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cfg.seed, cfg.n_zones, cfg.vans_per_zone, cfg.n_packages, cfg.shift_minutes,
         cfg.cutoff_minute, json.dumps(cfg.to_dict()), datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    _insert(conn, "zone", result.data.zones)
    _insert(conn, "van", result.data.vans)
    pk_cols = ["package_id", "van_id", "shelf", "stop_sequence", "size_class", "volume_m3",
               "weight_kg", "is_priority", "arrival_minute"]
    _insert(conn, "package", result.data.packages[pk_cols])

    _insert_run(conn, 1, result.baseline)
    if result.optimized is not None:
        _insert_run(conn, 2, result.optimized)
    conn.commit()
    return conn


def export_csv(conn: sqlite3.Connection, folder: str | Path) -> list[Path]:
    """Export every table and view to CSV (one file each) for Power BI / Excel."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY type DESC, name")]
    written = []
    for name in names:
        path = folder / f"{name}.csv"
        pd.read_sql_query(f"SELECT * FROM {name}", conn).to_csv(path, index=False)
        written.append(path)
    return written


def _readonly_authorizer(action, *_):
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def run_readonly_query(conn: sqlite3.Connection, sql: str) -> pd.DataFrame:
    """Run one SELECT/WITH statement. Anything that writes is refused."""
    text = sql.strip().rstrip(";").strip()
    code_lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("--")]
    first = code_lines[0].split(None, 1)[0].upper() if code_lines else ""
    if first not in {"SELECT", "WITH"}:
        raise ValueError("Only SELECT (or WITH ... SELECT) queries are allowed.")
    if ";" in text:
        raise ValueError("Run one statement at a time.")
    conn.set_authorizer(_readonly_authorizer)
    try:
        df = pd.read_sql_query(f"SELECT * FROM (\n{text}\n) LIMIT {MAX_QUERY_ROWS}", conn)
    finally:
        conn.set_authorizer(None)
    return df
