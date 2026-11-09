"""Synthetic data for one preload shift.

Nothing here comes from a real company. Every zone, van, route and package is
generated from the scenario settings, so the project can be shared publicly.

Produces three tables:

* ``zones``    - loading zones along the belt, one loader each
* ``vans``     - delivery vans, their routes, capacity and default zone
* ``packages`` - the shift manifest: what arrives, when, and where it must go
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ScenarioConfig

# Size classes: (share of packages, min volume m3, max volume m3, min kg, max kg)
SIZE_CLASSES = {
    "small":     (0.45, 0.004, 0.020, 0.2, 3.0),
    "medium":    (0.35, 0.020, 0.060, 1.0, 10.0),
    "large":     (0.15, 0.060, 0.140, 5.0, 25.0),
    "irregular": (0.05, 0.080, 0.250, 2.0, 32.0),
}


@dataclass
class ShiftData:
    zones: pd.DataFrame
    vans: pd.DataFrame
    packages: pd.DataFrame


def _arrival_minutes(cfg: ScenarioConfig, rng: np.random.Generator) -> np.ndarray:
    """Draw belt arrival times with two volume surges.

    Arrivals are a mix of a flat baseline and two peaks (an early wave and a
    later, larger one), which is the typical shape when inbound trailers are
    unloaded in batches. ``surge_strength`` sets how much volume sits in peaks.
    """
    n = cfg.n_packages
    T = cfg.shift_minutes
    n_peak = int(round(n * cfg.surge_strength * 0.7))
    n_flat = n - n_peak

    flat = rng.uniform(0, T, n_flat)
    peak1_n = int(n_peak * 0.4)
    peak1 = rng.normal(0.25 * T, 0.07 * T, peak1_n)
    peak2 = rng.normal(0.62 * T, 0.09 * T, n_peak - peak1_n)

    minutes = np.concatenate([flat, peak1, peak2])
    minutes = np.clip(minutes, 0, T - 1).astype(int)
    rng.shuffle(minutes)
    return minutes


def generate_shift(cfg: ScenarioConfig) -> ShiftData:
    cfg.validate()
    rng = np.random.default_rng(cfg.seed)

    # --- Zones -----------------------------------------------------------
    zones = pd.DataFrame(
        {
            "zone_id": [f"Z{i + 1}" for i in range(cfg.n_zones)],
            "zone_index": range(cfg.n_zones),
            "loader_name": [f"Loader {i + 1:02d}" for i in range(cfg.n_zones)],
            "belt_buffer": cfg.belt_buffer,
        }
    )

    # --- Vans ------------------------------------------------------------
    # Route demand is uneven on purpose: some routes are dense city routes,
    # others rural. A lognormal weight gives a realistic long tail.
    route_weight = rng.lognormal(mean=0.0, sigma=0.45, size=cfg.n_vans)
    route_weight /= route_weight.sum()

    # Each route also has its own timing: some routes' freight arrives early,
    # others late, depending on which inbound trailer carries it.
    route_time_bias = rng.uniform(-0.18, 0.18, size=cfg.n_vans)

    vans = pd.DataFrame(
        {
            "van_id": [f"V{i + 1:02d}" for i in range(cfg.n_vans)],
            "route_code": [f"R{101 + i}" for i in range(cfg.n_vans)],
            "default_zone_id": [f"Z{i // cfg.vans_per_zone + 1}" for i in range(cfg.n_vans)],
            "position_in_zone": [i % cfg.vans_per_zone + 1 for i in range(cfg.n_vans)],
            "capacity_m3": cfg.van_capacity_m3,
            "shelves": cfg.shelves_per_van,
            "route_weight": route_weight.round(5),
            "route_time_bias": route_time_bias.round(4),
        }
    )

    # --- Packages ----------------------------------------------------------
    n = cfg.n_packages
    van_idx = rng.choice(cfg.n_vans, size=n, p=route_weight)

    # Shift each package's arrival according to its route's timing bias.
    base_minutes = _arrival_minutes(cfg, rng)
    shifted = base_minutes + route_time_bias[van_idx] * cfg.shift_minutes
    arrival = np.clip(np.round(shifted), 0, cfg.shift_minutes - 1).astype(int)

    names = list(SIZE_CLASSES)
    shares = np.array([SIZE_CLASSES[k][0] for k in names])
    size_idx = rng.choice(len(names), size=n, p=shares / shares.sum())
    size_class = np.array(names)[size_idx]

    vol = np.empty(n)
    kg = np.empty(n)
    for i, name in enumerate(names):
        mask = size_idx == i
        _, vmin, vmax, kmin, kmax = SIZE_CLASSES[name]
        vol[mask] = rng.uniform(vmin, vmax, mask.sum())
        kg[mask] = rng.uniform(kmin, kmax, mask.sum())

    # Delivery stop order within the route decides the shelf: first stops
    # go nearest the door. Shelves 1..S split the stop sequence evenly.
    stop_seq = rng.integers(1, 181, size=n)
    shelf = np.minimum((stop_seq - 1) * cfg.shelves_per_van // 180 + 1, cfg.shelves_per_van)

    packages = pd.DataFrame(
        {
            "package_id": [f"PKG{i + 1:06d}" for i in range(n)],
            "van_id": vans["van_id"].to_numpy()[van_idx],
            "shelf": shelf.astype(int),
            "stop_sequence": stop_seq.astype(int),
            "size_class": size_class,
            "volume_m3": vol.round(4),
            "weight_kg": kg.round(2),
            "is_priority": rng.random(n) < cfg.priority_share,
            "arrival_minute": arrival,
        }
    ).sort_values(["arrival_minute", "package_id"], ignore_index=True)

    # Pre-drawn random numbers ("common random numbers"). Drawing them once,
    # per package and per zone-minute, means the baseline and the optimized
    # layout face exactly the same luck, so any difference comes from the
    # layout itself and not from random noise.
    packages["u_error"] = rng.random(n)
    packages["u_error_kind"] = rng.random(n)
    packages["u_wrong_target"] = rng.random(n)
    packages["u_sensor"] = rng.random(n)

    return ShiftData(zones=zones, vans=vans, packages=packages)


def loader_capacity_draws(cfg: ScenarioConfig) -> np.ndarray:
    """Packages each loader can handle in each minute (zones x minutes).

    Drawn from a separate seed stream so it stays identical no matter which
    vans are assigned to which zone.
    """
    rng = np.random.default_rng(cfg.seed + 10_007)
    horizon = cfg.cutoff_minute
    return rng.poisson(cfg.loader_rate_per_min, size=(cfg.n_zones, horizon))
