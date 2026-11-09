"""LoadLens dashboard.

Run locally:      streamlit run app.py
Deploy:           push to GitHub, then create an app on share.streamlit.io
                  pointing at this file.
"""

from __future__ import annotations

import io
import json
import zipfile

import pandas as pd
import streamlit as st

from loadlens import ScenarioConfig, run_scenario
from loadlens import charts
from loadlens.database import PRESET_QUERIES, build_database, run_readonly_query

st.set_page_config(page_title="LoadLens · Preload Simulator", page_icon="📦", layout="wide")

PRESETS = {
    "Typical day": {},
    "Peak season": {"n_packages": 4200, "surge_strength": 0.75},
    "Short-staffed": {"loader_rate_per_min": 4.2},
    "Sensor trouble": {"sensor_read_rate": 0.85},
}

LAYOUT_NAMES = {"baseline": "Usual layout", "optimized": "Optimized layout"}


# --------------------------------------------------------------------------
# Cached computation
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=8)
def get_result(cfg_dict: dict):
    return run_scenario(ScenarioConfig(**cfg_dict), optimize=True)


@st.cache_resource(show_spinner=False, max_entries=4)
def get_db(cfg_json: str):
    return build_database(get_result(json.loads(cfg_json)))


@st.cache_data(show_spinner=False, max_entries=4)
def db_bytes(cfg_json: str) -> bytes:
    return get_db(cfg_json).serialize()


@st.cache_data(show_spinner=False, max_entries=4)
def csv_zip(cfg_json: str) -> bytes:
    conn = get_db(cfg_json)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY type DESC, name")]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.writestr(f"{name}.csv", pd.read_sql_query(f"SELECT * FROM {name}", conn).to_csv(index=False))
    return buf.getvalue()


# --------------------------------------------------------------------------
# Sidebar: scenario settings
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("Scenario")
    preset = st.selectbox("Start from a preset", list(PRESETS), help="Fills the settings below.")
    d = {**ScenarioConfig().to_dict(), **PRESETS[preset]}
    k = f"_{preset}"  # new widget keys per preset so the defaults reset

    with st.form("scenario"):
        st.subheader("Volume", divider="gray")
        n_packages = st.slider("Packages in the shift", 500, 8000, d["n_packages"], 100, key="pk" + k)
        shift_minutes = st.slider("Belt running time (min)", 90, 300, d["shift_minutes"], 15, key="sm" + k)
        surge = st.slider("Surge strength", 0.0, 1.0, d["surge_strength"], 0.05, key="su" + k,
                          help="0 = packages arrive evenly; 1 = most arrive in two waves.")

        st.subheader("Facility", divider="gray")
        n_zones = st.slider("Loading zones (one loader each)", 2, 8, d["n_zones"], key="nz" + k)
        vans_per_zone = st.slider("Vans per zone", 2, 8, d["vans_per_zone"], key="vz" + k)
        van_cap = st.slider("Van capacity (m³)", 5.0, 15.0, d["van_capacity_m3"], 0.5, key="vc" + k)
        belt_buffer = st.slider("Belt buffer per zone (packages)", 15, 80, d["belt_buffer"], 5, key="bb" + k,
                                help="When this many packages are waiting, new ones go around the belt again.")

        st.subheader("People & sensors", divider="gray")
        rate = st.slider("Loader speed (packages / min)", 2.5, 8.0, d["loader_rate_per_min"], 0.1, key="lr" + k)
        err = st.slider("Base misload rate (%)", 0.0, 5.0, d["base_error_rate"] * 100, 0.1, key="er" + k) / 100
        pressure = st.slider("Extra errors when the belt is full (×)", 0.0, 5.0, d["pressure_factor"], 0.5,
                             key="pf" + k)
        read = st.slider("Sensor read rate (%)", 70.0, 100.0, d["sensor_read_rate"] * 100, 0.5, key="rr" + k) / 100

        seed = st.number_input("Random seed", 0, 99_999, d["seed"], key="sd" + k,
                               help="Same seed + same settings = the same shift every time.")
        st.form_submit_button("Run simulation", type="primary", width="stretch")

cfg = ScenarioConfig(
    seed=int(seed), n_zones=n_zones, vans_per_zone=vans_per_zone, van_capacity_m3=van_cap,
    n_packages=n_packages, shift_minutes=shift_minutes, surge_strength=surge,
    loader_rate_per_min=rate, base_error_rate=err, pressure_factor=pressure,
    belt_buffer=belt_buffer, sensor_read_rate=read,
)
cfg_dict = cfg.to_dict()
cfg_json = json.dumps(cfg_dict, sort_keys=True)

with st.spinner("Simulating the shift and optimizing the zone layout…"):
    result = get_result(cfg_dict)

base, opt = result.baseline, result.optimized

# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------
st.title("LoadLens")
st.markdown(
    "A simulator for the **preload**: the shift where parcels come off a belt and loaders put them into "
    "delivery vans before the vans leave. It shows where backlogs build, how mistakes rise under pressure, "
    "how in-van sensors catch them, and how a smarter van layout spreads the work."
)
st.caption(
    f"All data is synthetic · {cfg.n_packages:,} packages · {cfg.n_zones} zones × {cfg.vans_per_zone} vans · "
    f"vans depart at minute {cfg.cutoff_minute} · seed {cfg.seed}"
)

view = st.segmented_control("Layout to inspect", ["Usual layout", "Optimized layout"], default="Usual layout",
                            help="The optimizer moves vans between zones. Compare both in the Optimizer tab.")
run = opt if view == "Optimized layout" else base
other = base if run is opt else opt


def kpi(col, label, key, fmt="{:,}", good="down", help=None):
    val = run.kpis[key]
    delta = None
    if run is opt:
        diff = val - other.kpis[key]
        delta = fmt.format(diff) if diff else None
        if delta and diff > 0:
            delta = "+" + delta
    col.metric(label, fmt.format(val), delta=delta, delta_color="inverse" if good == "down" else "normal",
               help=help)


c = st.columns(6)
kpi(c[0], "Loaded before cutoff", "loaded_pct", "{:.1f}%", good="up")
kpi(c[1], "Missed cutoff", "missed_cutoff", help="Packages still on the belt when the vans left.")
kpi(c[2], "Misload rate", "misload_rate_pct", "{:.2f}%")
kpi(c[3], "Wrong-van caught", "catch_rate_pct", "{:.0f}%", good="up",
    help="Share of wrong-van mistakes the in-van sensors flagged before departure.")
kpi(c[4], "Escaped misloads", "escaped_misloads",
    help="Wrong-van loads with no sensor read, plus wrong-shelf loads (invisible to van-level sensors).")
kpi(c[5], "Avg wait on belt", "avg_wait_min", "{:.1f} min")
if run is opt:
    st.caption("Deltas compare the optimized layout with the usual layout.")

tabs = st.tabs(["Overview", "Belt & backlog", "Exceptions", "Van capacity", "Optimizer", "Data & SQL", "How it works"])

# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------
with tabs[0]:
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Arrivals vs. loader capacity")
        st.caption("When arrivals rise above what the loaders can handle, packages pile up.")
        st.plotly_chart(charts.arrivals_vs_capacity(run.timeseries), width="stretch")
    with right:
        st.subheader("Mistakes rise with pressure")
        st.caption("Misload rate by how many packages were waiting in the zone at the time.")
        st.plotly_chart(charts.misload_vs_pressure(run.loads, run.timeseries), width="stretch")

    st.subheader("Zone scorecard")
    zt = run.zones.rename(columns={
        "zone_id": "Zone", "packages": "Packages", "loaded": "Loaded", "misloads": "Misloads",
        "avg_wait_min": "Avg wait (min)", "packages_recirculated": "Sent around again",
        "peak_backlog": "Peak backlog", "minutes_belt_full": "Minutes belt full",
        "loader_utilization_pct": "Loader utilization", "misload_rate_pct": "Misload rate",
    })
    st.dataframe(
        zt, hide_index=True, width="stretch",
        column_config={
            "Loader utilization": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100),
            "Misload rate": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )

# --------------------------------------------------------------------------
# Belt & backlog
# --------------------------------------------------------------------------
with tabs[1]:
    st.subheader("Packages waiting in each zone")
    st.caption("A zone that sits at the dashed line is overloaded: new packages go around the belt and come "
               f"back {cfg.recirculation_minutes} minutes later, and its loader works under pressure.")
    st.plotly_chart(charts.backlog_by_zone(run.timeseries, cfg.belt_buffer), width="stretch")
    worst = run.zones.sort_values(["minutes_belt_full", "peak_backlog"], ascending=False).iloc[0]
    if worst["minutes_belt_full"] > 0:
        st.warning(f"**{worst['zone_id']}** had a full belt for {int(worst['minutes_belt_full'])} minutes and "
                   f"sent {int(worst['packages_recirculated'])} packages around again.", icon="⚠️")
    else:
        st.success("No zone ran out of belt space in this run.", icon="✅")

# --------------------------------------------------------------------------
# Exceptions
# --------------------------------------------------------------------------
with tabs[2]:
    exc = run.exceptions
    a, b = st.columns([2, 3], gap="large")
    with a:
        st.subheader("Exceptions by type")
        if len(exc):
            st.plotly_chart(charts.exceptions_by_type(exc), width="stretch")
        else:
            st.info("No exceptions in this run.")
        with st.expander("What each type means"):
            st.markdown(
                "- **WRONG_VAN_CAUGHT**: sensor read it in the wrong van; fixed before departure.\n"
                "- **WRONG_VAN_ESCAPED**: wrong van and no sensor read; leaves on the wrong van.\n"
                "- **WRONG_SHELF**: right van, wrong shelf. Van-level sensors cannot see this.\n"
                "- **NO_READ**: loaded but never read; needs a manual confirmation scan.\n"
                "- **MISSED_CUTOFF**: still on the belt when the vans left."
            )
    with b:
        st.subheader("Exception log")
        f1, f2, f3 = st.columns(3)
        types = f1.multiselect("Type", sorted(exc["exception_type"].unique()) if len(exc) else [])
        sev = f2.multiselect("Severity", ["high", "medium", "low"])
        zones_f = f3.multiselect("Zone", sorted(run.loads["zone_id"].unique()))
        view_exc = exc
        if types:
            view_exc = view_exc[view_exc["exception_type"].isin(types)]
        if sev:
            view_exc = view_exc[view_exc["severity"].isin(sev)]
        if zones_f:
            view_exc = view_exc[view_exc["zone_id"].isin(zones_f)]
        st.dataframe(
            view_exc[["exception_id", "minute", "zone_id", "exception_type", "severity", "package_id",
                      "planned_van_id", "sensed_van_id", "is_priority", "description"]],
            hide_index=True, width="stretch", height=420,
            column_config={"is_priority": st.column_config.CheckboxColumn("Priority")},
        )
        st.caption(f"{len(view_exc):,} of {len(exc):,} exceptions shown.")

# --------------------------------------------------------------------------
# Van capacity
# --------------------------------------------------------------------------
with tabs[3]:
    st.subheader("Van fill forecast, before the shift starts")
    st.caption("Built from the manifest, so supervisors can split an overloaded route before loading begins. "
               "The forecast is per van, so it is the same for both layouts.")
    vf = run.van_fill
    s1, s2, s3 = st.columns(3)
    s1.metric("Overflow", int((vf["status"] == "overflow").sum()))
    s2.metric("Watch", int((vf["status"] == "watch").sum()))
    s3.metric("OK", int((vf["status"] == "ok").sum()))
    st.plotly_chart(charts.van_fill(vf), width="stretch")
    actions = vf[vf["recommendation"] != ""][["van_id", "route_code", "zone_id", "fill_pct", "status",
                                               "recommendation"]].sort_values("fill_pct", ascending=False)
    if len(actions):
        st.subheader("Recommended actions")
        st.dataframe(actions, hide_index=True, width="stretch",
                     column_config={"fill_pct": st.column_config.NumberColumn("Fill", format="%.1f%%")})

# --------------------------------------------------------------------------
# Optimizer
# --------------------------------------------------------------------------
with tabs[4]:
    o = result.optimization
    st.subheader("Rebalancing which vans park in which zone")
    st.markdown(
        f"Using the manifest, the optimizer ({o.method}) decides which zone each van parks in so that no "
        f"loader gets several heavy routes, or several routes whose packages arrive in the same surge. "
        f"Each zone keeps {cfg.vans_per_zone} spots. It moved **{o.vans_moved} of {cfg.n_vans} vans** "
        f"in {o.solve_seconds}s."
    )
    m1, m2, m3 = st.columns(3)
    m1.metric("Busiest zone, busiest 15 min", f"{o.peak_bucket_after} packages",
              delta=f"{o.peak_bucket_after - o.peak_bucket_before} vs usual", delta_color="inverse")
    m2.metric("Gap between busiest and quietest zone", f"{o.spread_after} packages",
              delta=f"{o.spread_after - o.spread_before} vs usual", delta_color="inverse")
    m3.metric("Missed cutoff", opt.kpis["missed_cutoff"],
              delta=f"{opt.kpis['missed_cutoff'] - base.kpis['missed_cutoff']} vs usual", delta_color="inverse")

    g1, g2 = st.columns(2, gap="large")
    with g1:
        st.markdown("**Packages per zone**")
        st.plotly_chart(charts.compare_by_zone(base.zones, opt.zones, "packages", "Packages"),
                        width="stretch")
    with g2:
        st.markdown("**Peak backlog per zone**")
        st.plotly_chart(charts.compare_by_zone(base.zones, opt.zones, "peak_backlog", "Packages waiting"),
                        width="stretch")

    st.markdown("**Before and after**")
    rows = [
        ("Loaded before cutoff (%)", "loaded_pct"), ("Missed cutoff", "missed_cutoff"),
        ("Misloads", "misloads"), ("Misload rate (%)", "misload_rate_pct"),
        ("Misloads that escaped", "escaped_misloads"), ("Average wait (min)", "avg_wait_min"),
        ("95th percentile wait (min)", "p95_wait_min"), ("Peak backlog", "peak_backlog"),
        ("Packages sent around again", "packages_recirculated"),
        ("Zone-minutes with a full belt", "zone_minutes_belt_full"),
    ]
    comp = pd.DataFrame(
        [(label, base.kpis[key], opt.kpis[key]) for label, key in rows],
        columns=["KPI", "Usual layout", "Optimized layout"],
    )
    comp["Change"] = comp["Optimized layout"] - comp["Usual layout"]
    st.dataframe(comp, hide_index=True, width="stretch",
                 column_config={"Change": st.column_config.NumberColumn(format="%+.2f")})

    with st.expander("Van moves"):
        moves = result.assignment_changes.rename(columns={"baseline_zone": "usual_zone"})
        st.dataframe(moves.sort_values(["moved", "van_id"], ascending=[False, True]), hide_index=True,
                     width="stretch")
    st.caption("Both layouts face exactly the same packages, loader speeds and random events, so the "
               "difference comes from the layout alone.")

# --------------------------------------------------------------------------
# Data & SQL
# --------------------------------------------------------------------------
with tabs[5]:
    st.subheader("Query the shift database")
    st.caption("Every run is stored in a relational database (SQLite here; the schema ports to SQL Server or "
               "PostgreSQL). Pick a ready-made query or write your own. Only SELECT queries run.")
    conn = get_db(cfg_json)
    choice = st.selectbox("Ready-made query", list(PRESET_QUERIES))
    sql = st.text_area("SQL", PRESET_QUERIES[choice].strip(), height=200, key=f"sql_{choice}")
    if st.button("Run query"):
        try:
            out = run_readonly_query(conn, sql)
            st.dataframe(out, hide_index=True, width="stretch")
            st.caption(f"{len(out):,} rows")
        except Exception as e:  # show SQL errors to the user, don't crash the app
            st.error(f"Query failed: {e}")

    st.subheader("Tables")
    tables = pd.read_sql_query(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY type DESC, name", conn)
    tables["rows"] = [conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in tables["name"]]
    st.dataframe(tables, hide_index=True, width="stretch")

    st.subheader("Downloads")
    d1, d2, d3 = st.columns(3)
    d1.download_button("SQLite database", db_bytes(cfg_json), "loadlens.db", "application/x-sqlite3",
                       width="stretch")
    d2.download_button("All tables as CSV (for Power BI / Excel)", csv_zip(cfg_json), "loadlens_csv.zip",
                       "application/zip", width="stretch")
    d3.download_button("KPIs (JSON)", json.dumps({"config": cfg_dict, "usual_layout": base.kpis,
                                                  "optimized_layout": opt.kpis}, indent=2),
                       "kpis.json", "application/json", width="stretch")

# --------------------------------------------------------------------------
# How it works
# --------------------------------------------------------------------------
with tabs[6]:
    st.subheader("How the model works")
    st.markdown(f"""
**1. Synthetic manifest.** {cfg.n_packages:,} packages are generated for {cfg.n_vans} delivery routes. Routes have
uneven volume (dense city vs. rural), packages come in four size classes, and arrivals follow two surges,
like trailers being unloaded in waves. Each package has a target van and a shelf based on its delivery stop.

**2. Minute-by-minute simulation.** Each zone has one loader who handles a random number of packages per minute
(average {cfg.loader_rate_per_min}). A zone holds up to {cfg.belt_buffer} waiting packages; beyond that, packages
go around the belt again. The chance of a mistake grows with the backlog:

`p(error) = base rate × (1 + pressure factor × backlog / belt buffer)`

Mistakes are either a wrong van (usually the neighbouring one) or a wrong shelf in the right van.

**3. Sensor-based detection.** Each van reads packages as they are loaded (read rate {cfg.sensor_read_rate:.0%}).
A read in the wrong van is flagged instantly and fixed. A wrong-van load with no read escapes. Wrong-shelf loads
can't be seen by a van-level sensor, which shows why shelf-level checks matter.

**4. Capacity forecast.** The manifest gives each van's total volume before the shift starts, so overflowing vans
are flagged early, with a suggestion for where to move the excess.

**5. Optimization.** A constraint-programming model (Google OR-Tools CP-SAT) assigns vans to zones. It minimises the
busiest zone's peak 15-minute load, then evens out total work, then moves as few vans as possible.

**Fair comparison.** Random events are drawn once per package and per zone-minute, so both layouts face exactly
the same luck.

**Limitations.** One loader per zone, no breaks, no shared help between zones, and a simple error model. These
are deliberate simplifications to keep the project small and explainable.
""")
    st.caption("All data in this app is generated. It does not describe any real company or facility.")
