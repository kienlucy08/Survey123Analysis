"""Survey123 scope-of-work analytics dashboard.

Run with: streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis import aggregate as agg
from analysis import pipeline, report

# Fixed categorical color order (never cycled) — see dataviz skill palette.
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
STATUS_GOOD = "#0ca30c"
STATUS_CRITICAL = "#d03b3b"
GRIDLINE = "#e1e0d9"
MUTED = "#898781"

CHART_LAYOUT = dict(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
    xaxis=dict(gridcolor=GRIDLINE, zeroline=False, linecolor=MUTED),
    yaxis=dict(gridcolor=GRIDLINE, zeroline=False, linecolor=MUTED),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    margin=dict(l=10, r=10, t=30, b=10),
)

PRESETS = {
    "Last 30 days": lambda now: (now - timedelta(days=30), now),
    "Last 90 days": lambda now: (now - timedelta(days=90), now),
    "This month": lambda now: (now.replace(day=1), now),
    "Last 6 months": lambda now: (now - timedelta(days=182), now),
}


def scope_color_map(scope_keys: list[str]) -> dict[str, str]:
    """Assign the fixed categorical palette in order; anything past the 8th
    scope folds into a shared muted gray rather than cycling hues (a repeated
    hue would make two different scopes indistinguishable)."""
    color_map = {}
    for i, key in enumerate(scope_keys):
        color_map[key] = SERIES_COLORS[i] if i < len(SERIES_COLORS) else MUTED
    return color_map


@st.cache_data(show_spinner="Pulling Survey123 data...")
def load_data(use_sample: bool, cache_max_age_hours: float, refresh_token: int) -> pd.DataFrame:
    return pipeline.get_unified_data(use_sample=use_sample, cache_max_age_hours=cache_max_age_hours, force_refresh=refresh_token > 0)


@st.cache_data(show_spinner="Updating monthly report archive...")
def update_archive(df: pd.DataFrame, refresh_token: int) -> list[str]:
    """Cached on (df, refresh_token) so this only actually does work right
    after a real data refresh — a filter/date-range change reruns the script
    but reuses this result. ensure_monthly_archive() is itself idempotent
    (skips months that already have a report), so this is just avoiding that
    idempotency check's cost on every trivial widget interaction."""
    written = report.ensure_monthly_archive(df)
    return [str(p) for p in written]


def main() -> None:
    st.set_page_config(page_title="Survey123 Scope of Work Analytics", layout="wide")
    st.title("Survey123 Scope of Work Analytics")
    st.caption("Usage and growth across every survey in the org, rolled up by scope of work.")

    live_available = pipeline.live_config_available()

    with st.sidebar:
        st.header("Data source")
        if live_available:
            use_sample = st.radio("Source", ["Live ArcGIS org", "Sample data"], index=0) == "Sample data"
        else:
            st.info("Live ArcGIS credentials/config not found — showing sample data. See README to connect your org.")
            use_sample = True

        if "refresh_token" not in st.session_state:
            st.session_state.refresh_token = 0
        if st.button("Refresh data"):
            st.session_state.refresh_token += 1
            st.cache_data.clear()

        st.header("Time frame")
        preset = st.selectbox("Preset", list(PRESETS.keys()) + ["Custom"])
        now = datetime.now(timezone.utc)
        if preset == "Custom":
            start_date, end_date = st.date_input(
                "Range", value=(now.date() - timedelta(days=30), now.date())
            )
            start = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
            end = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)
        else:
            start, end = PRESETS[preset](now)

    df = load_data(use_sample, 6.0, st.session_state.refresh_token)

    if df.empty:
        st.warning("No data available yet.")
        return

    update_archive(df, st.session_state.refresh_token)

    # Order by all-time usage so the busiest scopes keep stable, distinct colors
    # across every time-frame filter; only the least-used scopes (past the 8th)
    # fold into the shared muted gray.
    all_scopes = agg.usage_by_scope(df)[["scope", "scope_label"]]
    color_map = scope_color_map(all_scopes["scope"].tolist())

    with st.sidebar:
        st.header("Scope of work")
        selected_labels = st.multiselect(
            "Filter scopes", all_scopes["scope_label"].tolist(), default=all_scopes["scope_label"].tolist()
        )
    selected_scopes = all_scopes[all_scopes["scope_label"].isin(selected_labels)]["scope"].tolist()

    period_df = agg.filter_by_timeframe(df[df["scope"].isin(selected_scopes)], start, end)
    growth_df = agg.period_over_period_growth(df[df["scope"].isin(selected_scopes)], start, end)

    # ---- KPI row -----------------------------------------------------
    total_current = int(growth_df["current_count"].sum())
    total_prior = int(growth_df["prior_count"].sum())
    total_pct = ((total_current - total_prior) / total_prior * 100) if total_prior else None
    top_scope = agg.usage_by_scope(period_df).head(1)
    att_stats = agg.attachment_stats(period_df)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Submissions this period", f"{total_current:,}", f"{total_pct:+.0f}% vs prior period" if total_pct is not None else None)
    c2.metric("Active scopes", int((agg.usage_by_scope(period_df)["submissions"] > 0).sum()))
    c3.metric("Top scope", top_scope["scope_label"].iloc[0] if not top_scope.empty else "—")
    c4.metric("Unique contributors", period_df["creator"].nunique())
    c5.metric("Avg. photos/submission", f"{att_stats['mean']:.1f}" if att_stats["mean"] is not None else "—")

    tab_trends, tab_scope, tab_growth, tab_team, tab_raw, tab_reports = st.tabs(
        ["Usage Trends", "By Scope of Work", "Growth", "Team & Photos", "Raw Data", "Reports"]
    )

    with tab_trends:
        freq = "D" if (end - start) <= timedelta(days=60) else "W"
        trend_df = agg.trend(period_df, freq=freq)
        if trend_df.empty:
            st.info("No submissions in this time frame.")
        else:
            fig = px.line(
                trend_df, x="period", y="submissions", color="scope_label",
                color_discrete_map={row.scope_label: color_map[row.scope] for row in all_scopes.itertuples()},
                markers=True,
            )
            fig.update_traces(line_width=2)
            fig.update_layout(**CHART_LAYOUT, yaxis_title="Submissions", xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)

    with tab_scope:
        usage_df = agg.usage_by_scope(period_df)
        left, right = st.columns([1, 1])
        with left:
            fig = px.bar(
                usage_df, x="submissions", y="scope_label", orientation="h",
                color="scope", color_discrete_map=color_map,
            )
            fig.update_layout(**CHART_LAYOUT, showlegend=False, yaxis_title="", xaxis_title="Submissions")
            fig.update_yaxes(categoryorder="total ascending")
            st.plotly_chart(fig, use_container_width=True)
        with right:
            if not usage_df.empty:
                focus_scope = st.selectbox("Purpose breakdown for", usage_df["scope_label"].tolist())
                focus_key = all_scopes[all_scopes["scope_label"] == focus_scope]["scope"].iloc[0]
                purpose_df = agg.usage_by_purpose(period_df, focus_key)
                fig = px.bar(purpose_df, x="submissions", y="purpose", orientation="h")
                fig.update_traces(marker_color=color_map[focus_key])
                fig.update_layout(**CHART_LAYOUT, yaxis_title="", xaxis_title="Submissions")
                fig.update_yaxes(categoryorder="total ascending")
                st.plotly_chart(fig, use_container_width=True)

    with tab_growth:
        st.caption(f"Current period vs the prior {(end - start).days}-day period.")
        display_df = growth_df.copy()
        display_df["pct_change_display"] = display_df["pct_change"].apply(
            lambda v: "new" if v == float("inf") else (f"{v:+.0f}%" if v is not None else "—")
        )
        st.dataframe(
            display_df[["scope_label", "current_count", "prior_count", "pct_change_display"]].rename(
                columns={"scope_label": "Scope", "current_count": "Current", "prior_count": "Prior", "pct_change_display": "Change"}
            ),
            use_container_width=True,
            hide_index=True,
        )

        finite = growth_df[growth_df["pct_change"].apply(lambda v: v is not None and v != float("inf"))]
        if not finite.empty:
            fig = go.Figure(
                go.Bar(
                    x=finite["pct_change"],
                    y=finite["scope_label"],
                    orientation="h",
                    marker_color=[STATUS_GOOD if v >= 0 else STATUS_CRITICAL for v in finite["pct_change"]],
                    text=[f"{v:+.0f}%" for v in finite["pct_change"]],
                    textposition="outside",
                )
            )
            fig.update_layout(**CHART_LAYOUT, xaxis_title="% change vs prior period", yaxis_title="")
            st.plotly_chart(fig, use_container_width=True)

    with tab_team:
        activity = agg.contributor_activity(df[df["scope"].isin(selected_scopes)], start, end)
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Contributors this period", activity["total_contributors"])
        a2.metric("New contributors", activity["new_contributors"])
        a3.metric("Returning contributors", activity["returning_contributors"])
        a4.metric("Avg. submissions/contributor", f"{activity['avg_submissions_per_contributor']:.1f}")

        left, right = st.columns([1, 1])
        with left:
            st.caption("Top contributors")
            top = agg.top_contributors(period_df, n=15).rename(
                columns={"creator": "Contributor", "submissions": "Submissions", "avg_attachments": "Avg. Photos"}
            )
            top["Avg. Photos"] = top["Avg. Photos"].round(1)
            st.dataframe(top, use_container_width=True, hide_index=True)
        with right:
            st.caption(f"Photos per submission — avg {att_stats['mean']:.1f}, median {att_stats['median']:.1f}, most common {att_stats['mode']}" if att_stats["mean"] is not None else "No attachment data.")
            dist_df = agg.attachment_distribution(period_df)
            if not dist_df.empty:
                fig = px.bar(dist_df, x="attachment_count", y="submissions")
                fig.update_traces(marker_color=SERIES_COLORS[0])
                fig.update_layout(**CHART_LAYOUT, xaxis_title="Photos per submission", yaxis_title="Submissions")
                # A handful of outlier submissions with very high photo counts
                # would otherwise squash the typical 0-20 range down to a sliver.
                p95 = dist_df["attachment_count"].quantile(0.95)
                if dist_df["attachment_count"].max() > p95 * 1.5:
                    fig.update_xaxes(range=[-0.5, max(p95, 5)])
                    st.caption(f"Chart trimmed to the typical range (up to {int(p95)} photos) — a few submissions go higher; see Raw Data for the full distribution.")
                st.plotly_chart(fig, use_container_width=True)

    with tab_raw:
        st.dataframe(period_df.sort_values("submitted_date", ascending=False), use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            period_df.to_csv(index=False).encode("utf-8"),
            file_name="survey123_scope_of_work.csv",
            mime="text/csv",
        )

    with tab_reports:
        st.caption("Generate a PDF for whatever time frame and scopes are currently selected in the sidebar.")
        if st.button("Generate PDF report for this view"):
            pdf_bytes = report.generate_report(df[df["scope"].isin(selected_scopes)], start, end)
            st.download_button(
                "Download PDF",
                pdf_bytes,
                file_name=f"survey123_report_{start:%Y%m%d}_{end:%Y%m%d}.pdf",
                mime="application/pdf",
            )

        st.divider()
        st.caption("Monthly archive — one report per calendar month, saved automatically to data/reports/ whenever data refreshes.")
        archive_files = sorted(report.REPORTS_DIR.glob("*.pdf"), reverse=True)
        if not archive_files:
            st.info("No archived reports yet.")
        else:
            visible, rest = archive_files[:12], archive_files[12:]
            for path in visible:
                st.download_button(
                    f"Download {path.stem}", path.read_bytes(), file_name=path.name, mime="application/pdf", key=f"archive_{path.stem}"
                )
            if rest:
                with st.expander(f"{len(rest)} more"):
                    for path in rest:
                        st.download_button(
                            f"Download {path.stem}", path.read_bytes(), file_name=path.name, mime="application/pdf", key=f"archive_{path.stem}"
                        )


if __name__ == "__main__":
    main()
