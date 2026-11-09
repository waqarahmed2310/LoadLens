-- LoadLens database schema
-- -----------------------------------------------------------------------------
-- Written in portable SQL. It runs as-is on SQLite (used by the app) and needs
-- only small type changes for SQL Server or PostgreSQL (see docs/data_dictionary.md).
--
-- Design:
--   * Master data   : zone, van, package      (what the shift looks like)
--   * Run data      : run, van_assignment     (one layout = one run)
--   * Event data    : load_event, exception_log, zone_minute, van_fill_forecast
--   * Reporting     : views prefixed v_
-- A shift is simulated twice (baseline layout and optimized layout), so every
-- event table is keyed by run_id. That lets one query compare both layouts.
-- -----------------------------------------------------------------------------

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS shift (
    shift_id            INTEGER PRIMARY KEY,
    seed                INTEGER NOT NULL,
    n_zones             INTEGER NOT NULL CHECK (n_zones > 0),
    vans_per_zone       INTEGER NOT NULL CHECK (vans_per_zone > 0),
    n_packages          INTEGER NOT NULL CHECK (n_packages > 0),
    shift_minutes       INTEGER NOT NULL CHECK (shift_minutes > 0),
    cutoff_minute       INTEGER NOT NULL,
    config_json         TEXT    NOT NULL,
    created_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS zone (
    zone_id             TEXT    PRIMARY KEY,
    zone_index          INTEGER NOT NULL UNIQUE,
    loader_name         TEXT    NOT NULL,
    belt_buffer         INTEGER NOT NULL CHECK (belt_buffer > 0)
);

CREATE TABLE IF NOT EXISTS van (
    van_id              TEXT    PRIMARY KEY,
    route_code          TEXT    NOT NULL UNIQUE,
    default_zone_id     TEXT    NOT NULL REFERENCES zone (zone_id),
    position_in_zone    INTEGER NOT NULL,
    capacity_m3         REAL    NOT NULL CHECK (capacity_m3 > 0),
    shelves             INTEGER NOT NULL CHECK (shelves > 0),
    route_weight        REAL    NOT NULL,
    route_time_bias     REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS package (
    package_id          TEXT    PRIMARY KEY,
    van_id              TEXT    NOT NULL REFERENCES van (van_id),
    shelf               INTEGER NOT NULL CHECK (shelf > 0),
    stop_sequence       INTEGER NOT NULL,
    size_class          TEXT    NOT NULL CHECK (size_class IN ('small', 'medium', 'large', 'irregular')),
    volume_m3           REAL    NOT NULL CHECK (volume_m3 > 0),
    weight_kg           REAL    NOT NULL CHECK (weight_kg > 0),
    is_priority         INTEGER NOT NULL CHECK (is_priority IN (0, 1)),
    arrival_minute      INTEGER NOT NULL CHECK (arrival_minute >= 0)
);

CREATE TABLE IF NOT EXISTS run (
    run_id              INTEGER PRIMARY KEY,
    shift_id            INTEGER NOT NULL REFERENCES shift (shift_id),
    label               TEXT    NOT NULL CHECK (label IN ('baseline', 'optimized')),
    UNIQUE (shift_id, label)
);

CREATE TABLE IF NOT EXISTS van_assignment (
    run_id              INTEGER NOT NULL REFERENCES run (run_id),
    van_id              TEXT    NOT NULL REFERENCES van (van_id),
    zone_id             TEXT    NOT NULL REFERENCES zone (zone_id),
    PRIMARY KEY (run_id, van_id)
);

CREATE TABLE IF NOT EXISTS load_event (
    run_id              INTEGER NOT NULL REFERENCES run (run_id),
    package_id          TEXT    NOT NULL REFERENCES package (package_id),
    zone_id             TEXT    NOT NULL REFERENCES zone (zone_id),
    status              TEXT    NOT NULL CHECK (status IN ('loaded', 'missed')),
    load_minute         INTEGER,
    wait_minutes        INTEGER,
    loaded_van_id       TEXT    REFERENCES van (van_id),
    loaded_shelf        INTEGER,
    error_kind          TEXT    NOT NULL CHECK (error_kind IN ('none', 'wrong_van', 'wrong_shelf')),
    recirculations      INTEGER NOT NULL DEFAULT 0,
    sensor_read         INTEGER NOT NULL CHECK (sensor_read IN (0, 1)),
    sensed_van_id       TEXT    REFERENCES van (van_id),
    PRIMARY KEY (run_id, package_id)
);

CREATE TABLE IF NOT EXISTS exception_log (
    run_id                  INTEGER NOT NULL REFERENCES run (run_id),
    exception_id            TEXT    NOT NULL,
    package_id              TEXT    NOT NULL REFERENCES package (package_id),
    zone_id                 TEXT    NOT NULL REFERENCES zone (zone_id),
    minute                  INTEGER NOT NULL,
    exception_type          TEXT    NOT NULL CHECK (exception_type IN
                                ('WRONG_VAN_CAUGHT', 'WRONG_VAN_ESCAPED', 'WRONG_SHELF', 'NO_READ', 'MISSED_CUTOFF')),
    severity                TEXT    NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    detected_in_real_time   INTEGER NOT NULL CHECK (detected_in_real_time IN (0, 1)),
    planned_van_id          TEXT    NOT NULL REFERENCES van (van_id),
    loaded_van_id           TEXT    REFERENCES van (van_id),
    sensed_van_id           TEXT    REFERENCES van (van_id),
    is_priority             INTEGER NOT NULL CHECK (is_priority IN (0, 1)),
    description             TEXT    NOT NULL,
    PRIMARY KEY (run_id, exception_id)
);

CREATE TABLE IF NOT EXISTS zone_minute (
    run_id              INTEGER NOT NULL REFERENCES run (run_id),
    zone_id             TEXT    NOT NULL REFERENCES zone (zone_id),
    minute              INTEGER NOT NULL,
    arrivals            INTEGER NOT NULL,
    recirculated        INTEGER NOT NULL,
    loaded              INTEGER NOT NULL,
    capacity            INTEGER NOT NULL,
    backlog             INTEGER NOT NULL,
    PRIMARY KEY (run_id, zone_id, minute)
);

CREATE TABLE IF NOT EXISTS van_fill_forecast (
    run_id              INTEGER NOT NULL REFERENCES run (run_id),
    van_id              TEXT    NOT NULL REFERENCES van (van_id),
    zone_id             TEXT    NOT NULL REFERENCES zone (zone_id),
    planned_packages    INTEGER NOT NULL,
    planned_volume_m3   REAL    NOT NULL,
    planned_weight_kg   REAL    NOT NULL,
    priority_packages   INTEGER NOT NULL,
    fill_pct            REAL    NOT NULL,
    spare_m3            REAL    NOT NULL,
    status              TEXT    NOT NULL CHECK (status IN ('ok', 'watch', 'overflow')),
    recommendation      TEXT,
    PRIMARY KEY (run_id, van_id)
);

-- Indexes for the queries the dashboard and reports run most -----------------
CREATE INDEX IF NOT EXISTS ix_package_van         ON package (van_id);
CREATE INDEX IF NOT EXISTS ix_package_arrival     ON package (arrival_minute);
CREATE INDEX IF NOT EXISTS ix_load_event_zone     ON load_event (run_id, zone_id);
CREATE INDEX IF NOT EXISTS ix_load_event_error    ON load_event (run_id, error_kind);
CREATE INDEX IF NOT EXISTS ix_exception_type      ON exception_log (run_id, exception_type);
CREATE INDEX IF NOT EXISTS ix_exception_severity  ON exception_log (run_id, severity);

-- Reporting views ------------------------------------------------------------

-- Zone scorecard per run.
CREATE VIEW IF NOT EXISTS v_zone_performance AS
SELECT
    r.label                                                        AS run_label,
    le.zone_id,
    COUNT(*)                                                       AS packages,
    SUM(le.status = 'loaded')                                      AS loaded,
    SUM(le.status = 'missed')                                      AS missed,
    SUM(le.error_kind <> 'none')                                   AS misloads,
    ROUND(100.0 * SUM(le.error_kind <> 'none') / MAX(SUM(le.status = 'loaded'), 1), 2) AS misload_rate_pct,
    ROUND(AVG(le.wait_minutes), 2)                                 AS avg_wait_min,
    SUM(le.recirculations > 0)                                     AS packages_recirculated
FROM load_event le
JOIN run r ON r.run_id = le.run_id
GROUP BY r.label, le.zone_id;

-- Exceptions by type and severity, and how many were caught in real time.
CREATE VIEW IF NOT EXISTS v_exception_summary AS
SELECT
    r.label                         AS run_label,
    e.exception_type,
    e.severity,
    COUNT(*)                        AS exceptions,
    SUM(e.detected_in_real_time)    AS detected_in_real_time,
    SUM(e.is_priority)              AS priority_packages
FROM exception_log e
JOIN run r ON r.run_id = e.run_id
GROUP BY r.label, e.exception_type, e.severity;

-- Vans at risk of running out of room, worst first.
CREATE VIEW IF NOT EXISTS v_van_fill AS
SELECT
    r.label AS run_label,
    f.van_id,
    v.route_code,
    f.zone_id,
    f.planned_packages,
    f.planned_volume_m3,
    v.capacity_m3,
    f.fill_pct,
    f.status,
    f.recommendation
FROM van_fill_forecast f
JOIN van v ON v.van_id = f.van_id
JOIN run r ON r.run_id = f.run_id;

-- Throughput in 15-minute buckets, ready for a line chart in Power BI.
CREATE VIEW IF NOT EXISTS v_throughput_15min AS
SELECT
    r.label                 AS run_label,
    zm.zone_id,
    (zm.minute / 15) * 15   AS bucket_start_minute,
    SUM(zm.arrivals)        AS arrivals,
    SUM(zm.loaded)          AS loaded,
    MAX(zm.backlog)         AS peak_backlog
FROM zone_minute zm
JOIN run r ON r.run_id = zm.run_id
GROUP BY r.label, zm.zone_id, bucket_start_minute;
