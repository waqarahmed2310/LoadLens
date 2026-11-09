"""Scenario configuration for a simulated parcel preload shift.

Every number in a simulation run comes from one ``ScenarioConfig``. Keeping the
whole scenario in a single, immutable object makes runs reproducible: the same
config (including ``seed``) always produces the same shift.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ScenarioConfig:
    # --- Reproducibility -------------------------------------------------
    seed: int = 42

    # --- Facility layout -------------------------------------------------
    n_zones: int = 4                 # loading zones, one loader each
    vans_per_zone: int = 5           # delivery vans parked along each zone
    van_capacity_m3: float = 9.5     # usable cargo volume per van
    shelves_per_van: int = 3         # shelf sections inside each van

    # --- Volume & timing -------------------------------------------------
    n_packages: int = 3000
    shift_minutes: int = 180         # length of the inbound belt window
    cutoff_grace_minutes: int = 15   # vans depart this long after the belt stops
    surge_strength: float = 0.6      # 0 = flat arrivals, 1 = very peaky
    priority_share: float = 0.05     # share of time-critical packages

    # --- People ----------------------------------------------------------
    loader_rate_per_min: float = 5.0     # average packages one loader handles per minute
    base_error_rate: float = 0.012       # misload probability under no pressure
    pressure_factor: float = 2.0         # extra error multiplier when the belt is full
    belt_buffer: int = 40                # packages a zone can hold before recirculating
    recirculation_minutes: int = 3       # delay for a package sent around the belt again

    # --- Sensing ---------------------------------------------------------
    sensor_read_rate: float = 0.97       # chance an in-van sensor reads a loaded package

    # --- Optimizer -------------------------------------------------------
    optimizer_bucket_minutes: int = 15
    optimizer_time_limit_s: float = 5.0

    @property
    def n_vans(self) -> int:
        return self.n_zones * self.vans_per_zone

    @property
    def cutoff_minute(self) -> int:
        return self.shift_minutes + self.cutoff_grace_minutes

    def validate(self) -> None:
        """Raise ``ValueError`` for settings that cannot produce a sensible run."""
        checks = {
            "n_zones must be between 1 and 8": 1 <= self.n_zones <= 8,
            "vans_per_zone must be between 1 and 12": 1 <= self.vans_per_zone <= 12,
            "n_packages must be between 100 and 50,000": 100 <= self.n_packages <= 50_000,
            "shift_minutes must be between 30 and 600": 30 <= self.shift_minutes <= 600,
            "surge_strength must be between 0 and 1": 0.0 <= self.surge_strength <= 1.0,
            "loader_rate_per_min must be positive": self.loader_rate_per_min > 0,
            "base_error_rate must be between 0 and 0.5": 0.0 <= self.base_error_rate <= 0.5,
            "sensor_read_rate must be between 0 and 1": 0.0 <= self.sensor_read_rate <= 1.0,
            "belt_buffer must be at least 1": self.belt_buffer >= 1,
            "shelves_per_van must be at least 1": self.shelves_per_van >= 1,
            "van_capacity_m3 must be positive": self.van_capacity_m3 > 0,
        }
        failed = [msg for msg, ok in checks.items() if not ok]
        if failed:
            raise ValueError("; ".join(failed))

    def to_dict(self) -> dict:
        return asdict(self)
