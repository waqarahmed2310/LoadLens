"""Tests for the LoadLens core. Run with:  pytest -q"""

from __future__ import annotations

import sqlite3

import pytest

from loadlens import ScenarioConfig, run_scenario
from loadlens.capacity import fill_status
from loadlens.data_generator import generate_shift
from loadlens.database import PRESET_QUERIES, build_database, run_readonly_query
from loadlens.optimizer import bucket_matrix, evaluate, optimize_assignment
from loadlens.simulator import default_assignment, simulate

SMALL = ScenarioConfig(n_packages=800, n_zones=3, vans_per_zone=4, shift_minutes=90, seed=7)


@pytest.fixture(scope="module")
def result():
    return run_scenario(SMALL)


# --- Configuration ---------------------------------------------------------
def test_invalid_config_is_rejected():
    with pytest.raises(ValueError):
        ScenarioConfig(n_zones=0).validate()
    with pytest.raises(ValueError):
        ScenarioConfig(sensor_read_rate=1.5).validate()


# --- Data generation -------------------------------------------------------
def test_generation_is_reproducible():
    a, b = generate_shift(SMALL), generate_shift(SMALL)
    assert a.packages.equals(b.packages)


def test_generated_data_is_consistent():
    data = generate_shift(SMALL)
    assert len(data.packages) == SMALL.n_packages
    assert len(data.vans) == SMALL.n_vans
    assert data.packages["van_id"].isin(data.vans["van_id"]).all()
    assert data.packages["arrival_minute"].between(0, SMALL.shift_minutes - 1).all()
    assert data.packages["shelf"].between(1, SMALL.shelves_per_van).all()
    assert (data.packages["volume_m3"] > 0).all()


# --- Simulation --------------------------------------------------------------
def test_every_package_is_accounted_for(result):
    loads = result.baseline.loads
    assert len(loads) == SMALL.n_packages
    assert set(loads["status"]) <= {"loaded", "missed"}
    loaded = loads[loads["status"] == "loaded"]
    assert (loaded["load_minute"] < SMALL.cutoff_minute).all()
    assert (loaded["wait_minutes"] >= 0).all()


def test_loader_never_exceeds_capacity(result):
    ts = result.baseline.timeseries
    assert (ts["loaded"] <= ts["capacity"]).all()


def test_no_errors_when_error_rate_is_zero():
    cfg = ScenarioConfig(n_packages=500, base_error_rate=0.0, seed=3)
    r = run_scenario(cfg, optimize=False)
    assert r.baseline.kpis["misloads"] == 0


def test_wrong_van_stays_in_same_zone(result):
    loads = result.baseline.loads
    wrong = loads[loads["error_kind"] == "wrong_van"]
    zone_of = result.baseline.assignment
    assert all(zone_of[v] == z for v, z in zip(wrong["loaded_van_id"], wrong["zone_id"]))
    assert (wrong["loaded_van_id"] != wrong["van_id"]).all()


def test_more_loader_capacity_means_less_waiting():
    slow = run_scenario(ScenarioConfig(n_packages=1500, loader_rate_per_min=3.0, seed=1), optimize=False)
    fast = run_scenario(ScenarioConfig(n_packages=1500, loader_rate_per_min=7.0, seed=1), optimize=False)
    assert fast.baseline.kpis["avg_wait_min"] < slow.baseline.kpis["avg_wait_min"]


# --- Detection -------------------------------------------------------------
def test_perfect_sensors_catch_every_wrong_van():
    cfg = ScenarioConfig(n_packages=1500, sensor_read_rate=1.0, base_error_rate=0.03, seed=5)
    k = run_scenario(cfg, optimize=False).baseline.kpis
    assert k["wrong_van"] > 0
    assert k["wrong_van_caught"] == k["wrong_van"]
    assert k["no_read_alerts"] == 0


def test_wrong_shelf_is_never_detected(result):
    exc = result.baseline.exceptions
    shelf = exc[exc["exception_type"] == "WRONG_SHELF"]
    assert not shelf["detected_in_real_time"].any()


# --- Capacity ----------------------------------------------------------------
@pytest.mark.parametrize("pct,status", [(50, "ok"), (85, "watch"), (100, "watch"), (100.1, "overflow")])
def test_fill_status_thresholds(pct, status):
    assert fill_status(pct) == status


def test_overflow_vans_get_a_recommendation(result):
    vf = result.baseline.van_fill
    assert (vf.loc[vf["status"] == "overflow", "recommendation"].str.len() > 0).all()


# --- Optimizer ---------------------------------------------------------------
def test_optimizer_keeps_layout_valid_and_never_worse():
    data = generate_shift(SMALL)
    base = default_assignment(data)
    opt = optimize_assignment(SMALL, data, base)
    zones = list(data.zones["zone_id"])
    counts = {z: list(opt.assignment.values()).count(z) for z in zones}
    assert set(opt.assignment) == set(base)
    assert all(c == SMALL.vans_per_zone for c in counts.values())
    assert opt.peak_bucket_after <= opt.peak_bucket_before
    vans, mat = bucket_matrix(SMALL, data)
    assert evaluate(opt.assignment, vans, mat, zones)[0] == opt.peak_bucket_after


def test_layouts_share_the_same_random_events(result):
    """Common random numbers: same package draws in both runs."""
    data = result.data
    rerun = simulate(SMALL, data, default_assignment(data))
    assert rerun.loads.equals(result.baseline.loads.drop(columns=["sensor_read", "sensed_van_id"]))


# --- Database ----------------------------------------------------------------
def test_database_round_trip(result):
    conn = build_database(result)
    n = conn.execute("SELECT COUNT(*) FROM load_event WHERE run_id = 1").fetchone()[0]
    assert n == SMALL.n_packages
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert bad == []


@pytest.mark.parametrize("name", list(PRESET_QUERIES))
def test_preset_queries_run(result, name):
    conn = build_database(result)
    df = run_readonly_query(conn, PRESET_QUERIES[name])
    assert df is not None


@pytest.mark.parametrize("sql", ["DROP TABLE van", "DELETE FROM package", "SELECT 1; DROP TABLE van",
                                 "INSERT INTO zone VALUES ('Z9', 9, 'x', 10)"])
def test_write_queries_are_blocked(result, sql):
    conn = build_database(result)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        run_readonly_query(conn, sql)
    assert conn.execute("SELECT COUNT(*) FROM van").fetchone()[0] == SMALL.n_vans
