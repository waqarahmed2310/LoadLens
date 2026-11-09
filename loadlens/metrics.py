"""KPIs and zone-level performance summaries."""

from __future__ import annotations

import pandas as pd

from .config import ScenarioConfig


def compute_kpis(loads: pd.DataFrame, exceptions: pd.DataFrame, timeseries: pd.DataFrame,
                 van_fill: pd.DataFrame, cfg: ScenarioConfig) -> dict:
    total = len(loads)
    loaded = int(loads["status"].eq("loaded").sum())
    wrong_van = int(loads["error_kind"].eq("wrong_van").sum())
    wrong_shelf = int(loads["error_kind"].eq("wrong_shelf").sum())
    misloads = wrong_van + wrong_shelf
    exc_types = exceptions["exception_type"].value_counts() if len(exceptions) else pd.Series(dtype=int)
    caught = int(exc_types.get("WRONG_VAN_CAUGHT", 0))
    escaped = int(exc_types.get("WRONG_VAN_ESCAPED", 0)) + wrong_shelf
    waits = loads["wait_minutes"].dropna()

    return {
        "packages": total,
        "loaded": loaded,
        "missed_cutoff": total - loaded,
        "loaded_pct": round(100 * loaded / total, 2) if total else 0.0,
        "misloads": misloads,
        "wrong_van": wrong_van,
        "wrong_shelf": wrong_shelf,
        "misload_rate_pct": round(100 * misloads / loaded, 2) if loaded else 0.0,
        "wrong_van_caught": caught,
        "catch_rate_pct": round(100 * caught / wrong_van, 1) if wrong_van else 100.0,
        "escaped_misloads": escaped,
        "no_read_alerts": int(exc_types.get("NO_READ", 0)),
        "avg_wait_min": round(float(waits.mean()), 2) if len(waits) else 0.0,
        "p95_wait_min": round(float(waits.quantile(0.95)), 1) if len(waits) else 0.0,
        "peak_backlog": int(timeseries["backlog"].max()) if len(timeseries) else 0,
        "zone_minutes_belt_full": int((timeseries["recirculated"] > 0).sum()),
        "packages_recirculated": int((loads["recirculations"] > 0).sum()),
        "recirculation_events": int(loads["recirculations"].sum()),
        "vans_overflow": int(van_fill["status"].eq("overflow").sum()),
        "vans_watch": int(van_fill["status"].eq("watch").sum()),
    }


def zone_performance(loads: pd.DataFrame, timeseries: pd.DataFrame, cfg: ScenarioConfig) -> pd.DataFrame:
    g = loads.groupby("zone_id")
    out = pd.DataFrame(
        {
            "packages": g.size(),
            "loaded": g.apply(lambda d: int(d["status"].eq("loaded").sum()), include_groups=False),
            "misloads": g.apply(lambda d: int(d["error_kind"].ne("none").sum()), include_groups=False),
            "avg_wait_min": g["wait_minutes"].mean().round(2),
            "packages_recirculated": g.apply(lambda d: int((d["recirculations"] > 0).sum()), include_groups=False),
        }
    )
    ts = timeseries.groupby("zone_id")
    out["peak_backlog"] = ts["backlog"].max()
    out["minutes_belt_full"] = ts.apply(lambda d: int((d["recirculated"] > 0).sum()), include_groups=False)
    shift_capacity = timeseries[timeseries["minute"] < cfg.cutoff_minute].groupby("zone_id")["capacity"].sum()
    out["loader_utilization_pct"] = (100 * out["loaded"] / shift_capacity).round(1)
    out["misload_rate_pct"] = (100 * out["misloads"] / out["loaded"].clip(lower=1)).round(2)
    return out.reset_index()
