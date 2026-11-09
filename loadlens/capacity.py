"""Van capacity forecast, made from the manifest *before* the shift starts.

The manifest says which packages are coming for which van, so the volume each
van will need is known in advance. Flagging overflow before the belt starts
gives supervisors time to split a route instead of discovering a full van at
the end of the shift.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data_generator import ShiftData

WATCH_PCT = 85.0
OVERFLOW_PCT = 100.0


def fill_status(fill_pct: float) -> str:
    if fill_pct > OVERFLOW_PCT:
        return "overflow"
    if fill_pct >= WATCH_PCT:
        return "watch"
    return "ok"


def forecast_van_fill(data: ShiftData, assignment: dict) -> pd.DataFrame:
    pk = data.packages
    agg = pk.groupby("van_id").agg(
        planned_packages=("package_id", "count"),
        planned_volume_m3=("volume_m3", "sum"),
        planned_weight_kg=("weight_kg", "sum"),
        priority_packages=("is_priority", "sum"),
    )
    vans = data.vans.set_index("van_id")[["route_code", "capacity_m3"]].join(agg).fillna(0)
    vans["zone_id"] = [assignment[v] for v in vans.index]
    vans["fill_pct"] = (100 * vans["planned_volume_m3"] / vans["capacity_m3"]).round(1)
    vans["status"] = vans["fill_pct"].map(fill_status)
    vans["spare_m3"] = (vans["capacity_m3"] - vans["planned_volume_m3"]).round(3)
    vans["recommendation"] = _recommendations(vans, pk)
    vans["planned_volume_m3"] = vans["planned_volume_m3"].round(3)
    vans["planned_weight_kg"] = vans["planned_weight_kg"].round(1)
    vans[["planned_packages", "priority_packages"]] = vans[["planned_packages", "priority_packages"]].astype(int)
    return vans.reset_index()


def _recommendations(vans: pd.DataFrame, pk: pd.DataFrame) -> list:
    """For each overflowing van, suggest moving the excess to the van in the
    same zone with the most spare room (or any zone if none has room)."""
    spare = vans["spare_m3"].copy()
    avg_vol = pk.groupby("van_id")["volume_m3"].mean()
    out = []
    for van, row in vans.iterrows():
        if row["status"] == "watch":
            out.append("Close to full: load large items first and watch the last surge.")
            continue
        if row["status"] != "overflow":
            out.append("")
            continue
        excess = -row["spare_m3"]
        same_zone = spare[(vans["zone_id"] == row["zone_id"]) & (spare.index != van)]
        pool = same_zone if (same_zone > excess).any() else spare.drop(van)
        target = pool.idxmax()
        n_pkgs = int(np.ceil(excess / max(avg_vol.get(van, 0.03), 1e-6)))
        out.append(
            f"Over by {excess:.2f} m³. Move about {n_pkgs} packages (last stops) "
            f"to {target}, which has {pool[target]:.2f} m³ spare."
        )
        spare[target] -= excess
    return out
