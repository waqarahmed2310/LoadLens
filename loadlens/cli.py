"""Command-line entry point.

Examples
--------
    python -m loadlens.cli                       # default scenario, print KPIs
    python -m loadlens.cli --packages 5000 --zones 6 --out data/
    python -m loadlens.cli --no-optimize --seed 7

``--out`` writes ``loadlens.db`` (SQLite), one CSV per table for Power BI, and
``kpis.json``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ScenarioConfig
from .database import build_database, export_csv
from .pipeline import run_scenario

KPI_LABELS = [
    ("loaded_pct", "Loaded before cutoff (%)"),
    ("missed_cutoff", "Missed cutoff"),
    ("misloads", "Misloads"),
    ("misload_rate_pct", "Misload rate (%)"),
    ("catch_rate_pct", "Wrong-van caught by sensors (%)"),
    ("escaped_misloads", "Misloads that escaped"),
    ("avg_wait_min", "Average wait on belt (min)"),
    ("peak_backlog", "Peak backlog (packages)"),
    ("packages_recirculated", "Packages sent around again"),
    ("vans_overflow", "Vans forecast to overflow"),
]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Simulate a parcel preload shift.")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--packages", type=int, default=3000)
    p.add_argument("--zones", type=int, default=4)
    p.add_argument("--vans-per-zone", type=int, default=5)
    p.add_argument("--shift-minutes", type=int, default=180)
    p.add_argument("--loader-rate", type=float, default=5.0)
    p.add_argument("--surge", type=float, default=0.6)
    p.add_argument("--no-optimize", action="store_true")
    p.add_argument("--out", type=Path, help="folder for the database, CSVs and KPI file")
    args = p.parse_args(argv)

    cfg = ScenarioConfig(
        seed=args.seed, n_packages=args.packages, n_zones=args.zones,
        vans_per_zone=args.vans_per_zone, shift_minutes=args.shift_minutes,
        loader_rate_per_min=args.loader_rate, surge_strength=args.surge,
    )
    result = run_scenario(cfg, optimize=not args.no_optimize)

    base, opt = result.baseline.kpis, result.optimized.kpis if result.optimized else None
    print(f"\nLoadLens - {cfg.n_packages} packages, {cfg.n_zones} zones x {cfg.vans_per_zone} vans, seed {cfg.seed}\n")
    header = f"{'KPI':<36}{'Baseline':>12}" + (f"{'Optimized':>12}" if opt else "")
    print(header)
    print("-" * len(header))
    for key, label in KPI_LABELS:
        line = f"{label:<36}{base[key]:>12}"
        if opt:
            line += f"{opt[key]:>12}"
        print(line)

    if result.optimization:
        o = result.optimization
        print(f"\nOptimizer: {o.method} ({o.status}, {o.solve_seconds}s), moved {o.vans_moved} vans; "
              f"peak 15-min zone load {o.peak_bucket_before} -> {o.peak_bucket_after}")

    if args.out:
        conn = build_database(result, args.out / "loadlens.db")
        files = export_csv(conn, args.out / "csv")
        conn.close()
        (args.out / "kpis.json").write_text(
            json.dumps({"config": cfg.to_dict(), "baseline": base, "optimized": opt}, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out / 'loadlens.db'}, {len(files)} CSV files and kpis.json")


if __name__ == "__main__":
    main()
