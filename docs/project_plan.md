# Project plan

## Problem

During a parcel preload, packages arrive on a belt in surges and loaders place them into delivery vans before a
fixed departure time. Three things go wrong regularly:

- **Backlogs.** One zone gets more work than its loader can handle, while others wait.
- **Misloads.** Packages go into the wrong van or onto the wrong shelf, and mistakes rise under pressure.
- **Overflow.** A van runs out of room, which is only discovered at the end of loading.

## Goal

Build a small, working tool that shows these problems with data and tests three improvements: real-time sensor
checks, a pre-shift capacity forecast, and an optimized assignment of vans to zones.

## Scope

**In scope:** synthetic data, simulation, detection logic, capacity forecast, optimization, relational database,
dashboard, documentation, tests.

**Out of scope:** real company data, integration with real systems, hardware, staffing decisions.

## Success measures

Measured on the default scenario (3,000 packages, 4 zones, seed 42), usual layout vs. optimized layout:

| Measure | Target | Result |
|---|---|---|
| Packages missing the vans | Reduce by at least 50% | 77 → 0 (−100%) |
| Average wait on the belt | Reduce by at least 25% | 4.0 → 1.7 min (−58%) |
| Wrong-van loads caught by sensors | At least 95% at a 97% read rate | 98% |
| Optimizer run time | Under 10 seconds | About 1 second |
| Automated tests | All passing in CI | 28 passing |

## Milestones

| Week | Milestone | Deliverables | Status |
|---|---|---|---|
| 1 | Plan and data model | Project plan, ER diagram, schema, data dictionary draft | Done |
| 2 | Synthetic data and simulation | Data generator, minute-by-minute simulator, first KPIs | Done |
| 3 | Detection and capacity | Sensor model, exception log, van fill forecast | Done |
| 4 | Optimization | CP-SAT model, fair before/after comparison | Done |
| 5 | Dashboard and database | Streamlit app, SQLite database, SQL console, CSV export | Done |
| 6 | Quality and presentation | Tests, CI, README, Power BI report, results presentation | In progress |

## Task list

- [x] Define the problem and success measures
- [x] Design the database and write the schema
- [x] Generate a synthetic manifest
- [x] Simulate the shift minute by minute
- [x] Model sensor reads and build the exception log
- [x] Forecast van fill and generate recommendations
- [x] Build and validate the optimizer
- [x] Store runs in the database and export CSVs
- [x] Build the dashboard
- [x] Write tests and set up CI
- [x] Write the README and data dictionary
- [ ] Build the Power BI report from the CSV export
- [ ] Record a short demo video
- [ ] Deliver the results presentation

## Risks

| Risk | Mitigation |
|---|---|
| Synthetic data may not match reality | Make every assumption a visible setting; explain limitations in the app. |
| Optimizer too slow for large scenarios | Time limit on the solver; greedy fallback. |
| Results depend on random luck | Fixed seeds and common random numbers across layouts. |
| Scope creep | Roadmap list for extensions; core features first. |
