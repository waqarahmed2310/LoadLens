"""Run a full scenario: generate -> simulate -> detect -> forecast -> optimize."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .capacity import forecast_van_fill
from .config import ScenarioConfig
from .data_generator import ShiftData, generate_shift
from .detection import apply_sensing, build_exceptions
from .metrics import compute_kpis, zone_performance
from .optimizer import OptimizationResult, assignment_table, optimize_assignment
from .simulator import default_assignment, simulate


@dataclass
class RunResult:
    """Everything produced by one simulated shift under one zone layout."""
    label: str
    assignment: dict
    loads: pd.DataFrame
    timeseries: pd.DataFrame
    exceptions: pd.DataFrame
    van_fill: pd.DataFrame
    zones: pd.DataFrame
    kpis: dict


@dataclass
class ScenarioResult:
    config: ScenarioConfig
    data: ShiftData
    baseline: RunResult
    optimized: RunResult | None
    optimization: OptimizationResult | None
    assignment_changes: pd.DataFrame | None


def _run(label: str, cfg: ScenarioConfig, data: ShiftData, assignment: dict) -> RunResult:
    sim = simulate(cfg, data, assignment)
    loads = apply_sensing(cfg, data, sim)
    exceptions = build_exceptions(loads, cfg)
    van_fill = forecast_van_fill(data, assignment)
    kpis = compute_kpis(loads, exceptions, sim.timeseries, van_fill, cfg)
    zones = zone_performance(loads, sim.timeseries, cfg)
    return RunResult(label, assignment, loads, sim.timeseries, exceptions, van_fill, zones, kpis)


def run_scenario(cfg: ScenarioConfig, optimize: bool = True) -> ScenarioResult:
    data = generate_shift(cfg)
    base_assign = default_assignment(data)
    baseline = _run("baseline", cfg, data, base_assign)

    optimized = opt = changes = None
    if optimize:
        opt = optimize_assignment(cfg, data, base_assign)
        optimized = _run("optimized", cfg, data, opt.assignment)
        changes = assignment_table(data, base_assign, opt.assignment)

    return ScenarioResult(cfg, data, baseline, optimized, opt, changes)
