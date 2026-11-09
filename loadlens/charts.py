"""Plotly figures for the dashboard.

Colors follow a fixed, color-blind-checked order: a series keeps its color no
matter how many series are shown. Status colors (ok / watch / overflow) are
reserved for status and always come with a text label.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# Categorical slots, in fixed order (validated for color-vision deficiency).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
# "OK" vans are drawn in neutral gray so the vans that need action stand out.
STATUS = {"ok": "#b9b8b0", "watch": "#fab219", "overflow": "#d03b3b"}
STATUS_LABEL = {"ok": "OK (< 85%)", "watch": "Watch (85–100%)", "overflow": "Overflow (> 100%)"}
MUTED = "#898781"


def _base(fig: go.Figure, height: int = 340, y_title: str | None = None, x_title: str | None = None) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=None),
        hoverlabel=dict(namelength=-1),
        barcornerradius=4,
        bargap=0.25,
        bargroupgap=0.08,
    )
    fig.update_xaxes(title=x_title, showgrid=False)
    fig.update_yaxes(title=y_title, zeroline=False)
    return fig


def arrivals_vs_capacity(ts: pd.DataFrame, bucket: int = 5) -> go.Figure:
    df = ts.assign(b=(ts["minute"] // bucket) * bucket).groupby("b")[["arrivals", "loaded", "capacity"]].sum()
    df = df / bucket  # per-minute rate
    fig = go.Figure()
    fig.add_scatter(x=df.index, y=df["arrivals"], name="Packages arriving / min", mode="lines",
                    line=dict(color=SERIES[0], width=2))
    fig.add_scatter(x=df.index, y=df["capacity"], name="Loader capacity / min (all zones)", mode="lines",
                    line=dict(color=SERIES[1], width=2, dash="dot"))
    fig.update_layout(hovermode="x unified")
    fig.update_traces(hovertemplate="%{y:.1f}")
    fig.update_yaxes(rangemode="tozero")
    return _base(fig, y_title="Packages per minute", x_title="Minute of shift")


def backlog_by_zone(ts: pd.DataFrame, belt_buffer: int) -> go.Figure:
    fig = go.Figure()
    for i, (zone, d) in enumerate(ts.groupby("zone_id", sort=True)):
        fig.add_scatter(x=d["minute"], y=d["backlog"], name=zone, mode="lines",
                        line=dict(color=SERIES[i % len(SERIES)], width=2), hovertemplate="%{y}")
    fig.add_hline(y=belt_buffer, line=dict(color=MUTED, width=1, dash="dash"),
                  annotation_text="Belt full: packages go around again", annotation_position="top left",
                  annotation_font_color=MUTED)
    fig.update_layout(hovermode="x unified")
    return _base(fig, height=380, y_title="Packages waiting", x_title="Minute of shift")


def misload_vs_pressure(loads: pd.DataFrame, ts: pd.DataFrame) -> go.Figure:
    loaded = loads[loads["status"].eq("loaded")].copy()
    loaded["load_minute"] = loaded["load_minute"].astype(int)
    m = loaded.merge(ts[["zone_id", "minute", "backlog"]], left_on=["zone_id", "load_minute"],
                     right_on=["zone_id", "minute"], how="left")
    bands = pd.cut(m["backlog"], bins=[-1, 9, 19, 29, 10_000], labels=["0–9", "10–19", "20–29", "30+"])
    g = m.assign(band=bands, err=m["error_kind"].ne("none")).groupby("band", observed=False)["err"]
    rate = (100 * g.mean()).fillna(0)
    n = g.size()
    fig = go.Figure(go.Bar(
        x=rate.index.astype(str), y=rate.values, marker_color=SERIES[0],
        text=[f"{v:.1f}%" for v in rate.values], textposition="outside",
        customdata=n.values, hovertemplate="Backlog %{x}<br>Misload rate %{y:.2f}%<br>%{customdata} packages<extra></extra>",
    ))
    fig.update_yaxes(rangemode="tozero")
    fig.update_traces(cliponaxis=False)
    return _base(fig, height=300, y_title="Misload rate (%)", x_title="Packages waiting in the zone")


def exceptions_by_type(exc: pd.DataFrame) -> go.Figure:
    counts = exc["exception_type"].value_counts().sort_values()
    fig = go.Figure(go.Bar(
        x=counts.values, y=counts.index, orientation="h", marker_color=SERIES[0],
        text=counts.values, textposition="outside", hovertemplate="%{y}: %{x}<extra></extra>",
    ))
    fig.update_traces(cliponaxis=False)
    return _base(fig, height=60 + 44 * max(len(counts), 1), x_title="Exceptions")


def van_fill(vf: pd.DataFrame) -> go.Figure:
    df = vf.sort_values("van_id", ascending=False)
    fig = go.Figure()
    for status in ["ok", "watch", "overflow"]:
        d = df[df["status"] == status]
        if d.empty:
            continue
        fig.add_bar(
            x=d["fill_pct"], y=d["van_id"], orientation="h", name=STATUS_LABEL[status],
            marker_color=STATUS[status], text=[f"{v:.0f}%" for v in d["fill_pct"]], textposition="outside",
            customdata=d[["route_code", "zone_id", "planned_packages", "planned_volume_m3"]].values,
            hovertemplate="%{y} · %{customdata[0]} · zone %{customdata[1]}<br>"
                          "%{customdata[2]} packages, %{customdata[3]:.2f} m³<br>Fill %{x:.1f}%<extra></extra>",
        )
    fig.add_vline(x=100, line=dict(color=MUTED, width=1, dash="dash"))
    fig.update_layout(barmode="overlay", bargap=0.4)
    fig.update_traces(cliponaxis=False)
    fig.update_yaxes(categoryorder="array", categoryarray=list(df["van_id"]))
    fig = _base(fig, height=max(320, 26 * len(df) + 80), x_title="Forecast fill (% of van volume)")
    fig.update_layout(bargap=0.4)
    return fig


def compare_by_zone(base: pd.DataFrame, opt: pd.DataFrame, column: str, y_title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(x=base["zone_id"], y=base[column], name="Usual layout", marker_color=SERIES[0],
                hovertemplate="%{x}: %{y}<extra>Usual layout</extra>")
    fig.add_bar(x=opt["zone_id"], y=opt[column], name="Optimized layout", marker_color=SERIES[1],
                hovertemplate="%{x}: %{y}<extra>Optimized layout</extra>")
    fig.update_layout(barmode="group")
    return _base(fig, height=300, y_title=y_title)
