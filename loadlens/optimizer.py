"""Rebalance which vans park in which loading zone.

Problem
-------
Every zone has the same number of van spots and one loader. If several heavy
routes, or several routes whose freight arrives in the same surge, park in one
zone, that loader falls behind while others wait. Backlog builds, packages go
around the belt again, and mistakes rise under pressure.

Formulation (constraint programming, Google OR-Tools CP-SAT)
------------------------------------------------------------
* ``x[v, z] = 1`` if van ``v`` parks in zone ``z``.
* Each van parks in exactly one zone; each zone keeps exactly ``vans_per_zone``
  spots (the physical layout does not change).
* ``load[z, t]`` = packages arriving for zone ``z`` in time bucket ``t``,
  known in advance from the manifest.
* Objective, in priority order:
    1. minimise the **peak** bucket load across all zones (surge-aware),
    2. minimise the spread of total packages between zones,
    3. move as few vans as possible from their usual spot.

If OR-Tools is not installed, a greedy heuristic is used instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ScenarioConfig
from .data_generator import ShiftData

try:  # OR-Tools is optional so the core still runs in minimal environments.
    from ortools.sat.python import cp_model
    HAS_ORTOOLS = True
except ImportError:  # pragma: no cover
    HAS_ORTOOLS = False


@dataclass
class OptimizationResult:
    assignment: dict
    method: str
    status: str
    solve_seconds: float
    peak_bucket_before: int
    peak_bucket_after: int
    spread_before: int
    spread_after: int
    vans_moved: int


def bucket_matrix(cfg: ScenarioConfig, data: ShiftData) -> tuple[list, np.ndarray]:
    """Planned arrivals per van per time bucket (vans x buckets)."""
    n_buckets = int(np.ceil(cfg.shift_minutes / cfg.optimizer_bucket_minutes))
    vans = list(data.vans["van_id"])
    idx = {v: i for i, v in enumerate(vans)}
    mat = np.zeros((len(vans), n_buckets), dtype=int)
    b = (data.packages["arrival_minute"] // cfg.optimizer_bucket_minutes).clip(upper=n_buckets - 1)
    for v, bucket in zip(data.packages["van_id"], b):
        mat[idx[v], bucket] += 1
    return vans, mat


def evaluate(assignment: dict, vans: list, mat: np.ndarray, zones: list) -> tuple[int, int]:
    """Return (peak bucket load, spread of zone totals) for an assignment."""
    zone_load = {z: np.zeros(mat.shape[1], dtype=int) for z in zones}
    for i, v in enumerate(vans):
        zone_load[assignment[v]] += mat[i]
    peak = max(int(l.max()) for l in zone_load.values())
    totals = [int(l.sum()) for l in zone_load.values()]
    return peak, max(totals) - min(totals)


def _greedy(cfg: ScenarioConfig, vans: list, mat: np.ndarray, zones: list) -> dict:
    """Largest-first: place each van where it raises the peak load the least."""
    order = np.argsort(-mat.max(axis=1) * 1000 - mat.sum(axis=1))
    load = {z: np.zeros(mat.shape[1], dtype=int) for z in zones}
    slots = {z: cfg.vans_per_zone for z in zones}
    out = {}
    for i in order:
        best = min(
            (z for z in zones if slots[z] > 0),
            key=lambda z: ((load[z] + mat[i]).max(), (load[z] + mat[i]).sum()),
        )
        out[vans[i]] = best
        load[best] += mat[i]
        slots[best] -= 1
    return out


def optimize_assignment(cfg: ScenarioConfig, data: ShiftData, baseline: dict) -> OptimizationResult:
    vans, mat = bucket_matrix(cfg, data)
    zones = list(data.zones["zone_id"])
    totals = mat.sum(axis=1)
    peak_before, spread_before = evaluate(baseline, vans, mat, zones)
    start = time.perf_counter()

    if HAS_ORTOOLS:
        model = cp_model.CpModel()
        V, Z, T = len(vans), len(zones), mat.shape[1]
        x = {(v, z): model.NewBoolVar(f"x_{v}_{z}") for v in range(V) for z in range(Z)}
        for v in range(V):
            model.AddExactlyOne(x[v, z] for z in range(Z))
        for z in range(Z):
            model.Add(sum(x[v, z] for v in range(V)) == cfg.vans_per_zone)

        peak = model.NewIntVar(0, int(mat.sum()), "peak")
        for z in range(Z):
            for t in range(T):
                model.Add(sum(int(mat[v, t]) * x[v, z] for v in range(V)) <= peak)

        zone_total = [model.NewIntVar(0, int(totals.sum()), f"total_{z}") for z in range(Z)]
        for z in range(Z):
            model.Add(zone_total[z] == sum(int(totals[v]) * x[v, z] for v in range(V)))
        tmax = model.NewIntVar(0, int(totals.sum()), "tmax")
        tmin = model.NewIntVar(0, int(totals.sum()), "tmin")
        model.AddMaxEquality(tmax, zone_total)
        model.AddMinEquality(tmin, zone_total)

        zi = {z: i for i, z in enumerate(zones)}
        moved = sum(1 - x[v, zi[baseline[vans[v]]]] for v in range(V))

        # Weights make the objective lexicographic: peak >> spread >> moves.
        w_spread = V + 1
        w_peak = (int(totals.sum()) + 1) * w_spread
        model.Minimize(w_peak * peak + w_spread * (tmax - tmin) + moved)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = cfg.optimizer_time_limit_s
        solver.parameters.num_search_workers = 4
        solver.parameters.random_seed = cfg.seed
        code = solver.Solve(model)

        if code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            assignment = {
                vans[v]: zones[z] for v in range(V) for z in range(Z) if solver.Value(x[v, z])
            }
            method = "OR-Tools CP-SAT"
            status = solver.StatusName(code).lower()
        else:  # pragma: no cover - only on solver failure
            assignment, method, status = _greedy(cfg, vans, mat, zones), "greedy heuristic", "fallback"
    else:  # pragma: no cover
        assignment, method, status = _greedy(cfg, vans, mat, zones), "greedy heuristic", "ortools not installed"

    # Never return something worse than the starting layout.
    peak_after, spread_after = evaluate(assignment, vans, mat, zones)
    if (peak_after, spread_after) > (peak_before, spread_before):
        assignment, peak_after, spread_after = dict(baseline), peak_before, spread_before
        status += " (kept baseline)"

    return OptimizationResult(
        assignment=assignment,
        method=method,
        status=status,
        solve_seconds=round(time.perf_counter() - start, 2),
        peak_bucket_before=peak_before,
        peak_bucket_after=peak_after,
        spread_before=spread_before,
        spread_after=spread_after,
        vans_moved=sum(assignment[v] != baseline[v] for v in vans),
    )


def assignment_table(data: ShiftData, baseline: dict, optimized: dict) -> pd.DataFrame:
    counts = data.packages.groupby("van_id").size()
    df = data.vans[["van_id", "route_code"]].copy()
    df["planned_packages"] = df["van_id"].map(counts).fillna(0).astype(int)
    df["baseline_zone"] = df["van_id"].map(baseline)
    df["optimized_zone"] = df["van_id"].map(optimized)
    df["moved"] = df["baseline_zone"] != df["optimized_zone"]
    return df
