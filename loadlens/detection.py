"""Misload detection using simulated in-van sensors.

Each van has a sensor (think RFID or a scan tunnel at the van door) that reads
packages as they are loaded. A read tells us *which van* a package is in, so:

* **Wrong van, read**   -> caught instantly; the loader moves it before departure.
* **Wrong van, no read** -> escapes; found only at delivery.
* **Wrong shelf**       -> invisible to a van-level sensor; it escapes and costs
  the driver search time. This is a deliberate limitation of the model and a
  talking point: van-level sensing is not shelf-level sensing.
* **No read** on any package -> an "unconfirmed" alert for a manual check.
* **Missed** packages (still on the belt at cutoff) -> high-severity exception.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ScenarioConfig
from .data_generator import ShiftData
from .simulator import SimulationResult

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def apply_sensing(cfg: ScenarioConfig, data: ShiftData, sim: SimulationResult) -> pd.DataFrame:
    """Add sensor read columns to the per-package load table."""
    loads = sim.loads.copy()
    u = data.packages.set_index("package_id").loc[loads["package_id"], "u_sensor"].to_numpy()
    is_loaded = loads["status"].eq("loaded").to_numpy()
    read = is_loaded & (u < cfg.sensor_read_rate)
    loads["sensor_read"] = read
    loads["sensed_van_id"] = np.where(read, loads["loaded_van_id"], None)
    return loads


def build_exceptions(loads: pd.DataFrame, cfg: ScenarioConfig) -> pd.DataFrame:
    """One row per exception, with whether the sensors raised it in real time."""
    rows = []

    def add(r, kind, severity, detected, desc, minute):
        rows.append(
            {
                "package_id": r.package_id,
                "zone_id": r.zone_id,
                "minute": minute,
                "exception_type": kind,
                "severity": severity,
                "detected_in_real_time": detected,
                "planned_van_id": r.van_id,
                "loaded_van_id": r.loaded_van_id,
                "sensed_van_id": r.sensed_van_id,
                "is_priority": bool(r.is_priority),
                "description": desc,
            }
        )

    for r in loads.itertuples(index=False):
        if r.status == "missed":
            add(r, "MISSED_CUTOFF", "high", True,
                f"Still on belt when vans departed (minute {cfg.cutoff_minute}).", cfg.cutoff_minute)
            continue

        minute = int(r.load_minute)
        sev_up = "high" if r.is_priority else "medium"
        if r.error_kind == "wrong_van":
            if r.sensor_read:
                add(r, "WRONG_VAN_CAUGHT", sev_up, True,
                    f"Sensed in {r.sensed_van_id}, planned for {r.van_id}. Corrected before departure.", minute)
            else:
                add(r, "WRONG_VAN_ESCAPED", "high", False,
                    f"Loaded into {r.loaded_van_id} instead of {r.van_id}; no sensor read, so it left on the wrong van.",
                    minute)
        elif r.error_kind == "wrong_shelf":
            add(r, "WRONG_SHELF", "low", False,
                f"Right van ({r.van_id}) but shelf {int(r.loaded_shelf)} instead of {int(r.shelf)}. "
                "Van-level sensing cannot see this.", minute)

        if not r.sensor_read and r.error_kind != "wrong_van":
            add(r, "NO_READ", "low", True,
                f"No sensor read in {r.van_id}; needs a manual confirmation scan.", minute)

    cols = ["exception_id", "package_id", "zone_id", "minute", "exception_type", "severity",
            "detected_in_real_time", "planned_van_id", "loaded_van_id", "sensed_van_id",
            "is_priority", "description"]
    if not rows:
        return pd.DataFrame(columns=cols)
    exc = pd.DataFrame(rows).sort_values(["minute", "package_id"], ignore_index=True)
    exc.insert(0, "exception_id", [f"EX{i + 1:05d}" for i in range(len(exc))])
    return exc[cols]
