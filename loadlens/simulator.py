"""Minute-by-minute simulation of a preload shift.

Model
-----
* Packages arrive on the belt at their ``arrival_minute`` and travel to the
  zone where their van is parked.
* Each zone has one loader. Every minute the loader can handle a random
  number of packages (Poisson around ``loader_rate_per_min``), first in,
  first out.
* A zone can only hold ``belt_buffer`` packages. When it is full, a newly
  arriving package goes around the belt again and comes back
  ``recirculation_minutes`` later.
* Mistakes become more likely as the backlog grows:
  ``p_error = base_error_rate * (1 + pressure_factor * backlog / belt_buffer)``.
  A mistake is either a *wrong van* (usually a neighbouring van) or a
  *wrong shelf* inside the right van.
* Vans depart at ``cutoff_minute``. Anything still on the belt is *missed*.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ScenarioConfig
from .data_generator import ShiftData, loader_capacity_draws

WRONG_VAN_SHARE = 0.7  # share of mistakes that put a package in the wrong van


@dataclass
class SimulationResult:
    loads: pd.DataFrame        # one row per package: what actually happened
    timeseries: pd.DataFrame   # one row per zone per minute
    assignment: dict           # van_id -> zone_id used for this run


def default_assignment(data: ShiftData) -> dict:
    return dict(zip(data.vans["van_id"], data.vans["default_zone_id"]))


def _wrong_van(van_id: str, zone_vans: list, positions: dict, u: float) -> str | None:
    """Pick a wrong van in the same zone; neighbours are the likeliest mistake."""
    others = [v for v in zone_vans if v != van_id]
    if not others:
        return None
    dist = np.array([abs(positions[v] - positions[van_id]) or 1 for v in others], dtype=float)
    weights = 1.0 / dist**2
    cum = np.cumsum(weights / weights.sum())
    return others[int(np.searchsorted(cum, u, side="right").clip(0, len(others) - 1))]


def _wrong_shelf(shelf: int, n_shelves: int, u: float) -> int | None:
    others = [s for s in range(1, n_shelves + 1) if s != shelf]
    if not others:
        return None
    return others[min(int(u * len(others)), len(others) - 1)]


def simulate(cfg: ScenarioConfig, data: ShiftData, assignment: dict | None = None) -> SimulationResult:
    assignment = assignment or default_assignment(data)
    zone_ids = list(data.zones["zone_id"])
    zone_pos = {z: i for i, z in enumerate(zone_ids)}

    # Vans per zone, ordered by their parking position. When the optimizer
    # moves a van to another zone it gets the next free position there.
    zone_vans: dict[str, list] = {z: [] for z in zone_ids}
    for van in data.vans["van_id"]:
        zone_vans[assignment[van]].append(van)
    positions = {}
    for z, vans in zone_vans.items():
        for i, v in enumerate(vans):
            positions[v] = i + 1

    cap = loader_capacity_draws(cfg)
    horizon = cfg.cutoff_minute

    pk = data.packages.reset_index(drop=True)
    n = len(pk)
    van_arr = pk["van_id"].to_numpy()
    shelf_arr = pk["shelf"].to_numpy()
    u_err = pk["u_error"].to_numpy()
    u_kind = pk["u_error_kind"].to_numpy()
    u_target = pk["u_wrong_target"].to_numpy()

    # Arrival schedule: minute -> list of package row indices.
    schedule: list[list[int]] = [[] for _ in range(horizon + cfg.recirculation_minutes + 1)]
    for i, m in enumerate(pk["arrival_minute"].to_numpy()):
        schedule[int(m)].append(i)

    queues = {z: deque() for z in zone_ids}
    enqueue_minute = np.full(n, -1, dtype=int)
    load_minute = np.full(n, -1, dtype=int)
    loaded_van = np.empty(n, dtype=object)
    loaded_shelf = np.zeros(n, dtype=int)
    error_kind = np.array(["none"] * n, dtype=object)
    recirc = np.zeros(n, dtype=int)

    ts_rows = []
    for minute in range(horizon):
        arrivals = {z: 0 for z in zone_ids}
        recirculated = {z: 0 for z in zone_ids}

        for i in schedule[minute]:
            z = assignment[van_arr[i]]
            if len(queues[z]) >= cfg.belt_buffer:
                recirc[i] += 1
                recirculated[z] += 1
                back = minute + cfg.recirculation_minutes
                if back < len(schedule):
                    schedule[back].append(i)
                continue
            if enqueue_minute[i] < 0:
                enqueue_minute[i] = minute
            queues[z].append(i)
            arrivals[z] += 1

        for z in zone_ids:
            q = queues[z]
            capacity = int(cap[zone_pos[z], minute])
            loaded = 0
            while q and loaded < capacity:
                backlog_before = len(q)
                i = q.popleft()
                pressure = min(1.0, backlog_before / cfg.belt_buffer)
                p_err = cfg.base_error_rate * (1.0 + cfg.pressure_factor * pressure)

                van, shelf = van_arr[i], int(shelf_arr[i])
                kind = "none"
                if u_err[i] < p_err:
                    if u_kind[i] < WRONG_VAN_SHARE:
                        wrong = _wrong_van(van, zone_vans[z], positions, u_target[i])
                        if wrong is not None:
                            van, kind = wrong, "wrong_van"
                    if kind == "none":
                        wrong_s = _wrong_shelf(shelf, cfg.shelves_per_van, u_target[i])
                        if wrong_s is not None:
                            shelf, kind = wrong_s, "wrong_shelf"

                load_minute[i] = minute
                loaded_van[i] = van
                loaded_shelf[i] = shelf
                error_kind[i] = kind
                loaded += 1

            ts_rows.append(
                {
                    "minute": minute,
                    "zone_id": z,
                    "arrivals": arrivals[z],
                    "recirculated": recirculated[z],
                    "loaded": loaded,
                    "capacity": capacity,
                    "backlog": len(q),
                }
            )

    loads = pk[["package_id", "van_id", "shelf", "is_priority", "arrival_minute", "volume_m3"]].copy()
    loads["zone_id"] = [assignment[v] for v in van_arr]
    loads["status"] = np.where(load_minute >= 0, "loaded", "missed")
    loads["load_minute"] = np.where(load_minute >= 0, load_minute, np.nan)
    loads["wait_minutes"] = np.where(load_minute >= 0, load_minute - pk["arrival_minute"].to_numpy(), np.nan)
    loads["loaded_van_id"] = np.where(load_minute >= 0, loaded_van, None)
    loads["loaded_shelf"] = np.where(load_minute >= 0, loaded_shelf, np.nan)
    loads["error_kind"] = np.where(load_minute >= 0, error_kind, "none")
    loads["recirculations"] = recirc

    return SimulationResult(
        loads=loads,
        timeseries=pd.DataFrame(ts_rows),
        assignment=dict(assignment),
    )
