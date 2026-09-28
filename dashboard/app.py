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
from analysis import deliverables as dlv
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
def update_archive(df: pd.DataFrame, refresh_token: int, completion_config: dict, family_order: dict) -> list[str]:
    """Cached on (df, refresh_token) so this only actually does work right
    after a real data refresh — a filter/date-range change reruns the script
    but reuses this result. ensure_monthly_archive() is itself idempotent
    (skips months that already have a report), so this is just avoiding that
    idempotency check's cost on every trivial widget interaction."""
    written = report.ensure_monthly_archive(df, completion_config=completion_config, family_order=family_order)
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

    completion_config = pipeline.get_completion_config(use_sample)
    family_order = pipeline.get_family_order(use_sample)
    update_archive(df, st.session_state.refresh_token, completion_config, family_order)

    # QA submissions (Inspection's *_qa surveys) are a reviewer's second pass
    # over a site that's already counted once — every rollup metric below
    # excludes them so "submissions" means actual field survey visits.
    # site_completion() is the one thing that needs QA rows too (to know
    # whether a site's been QA-reviewed), so it works off `df`/`full_period_df`.
    primary_df = agg.primary_only(df)

    # Order by all-time usage so the busiest scopes keep stable, distinct colors
    # across every time-frame filter; only the least-used scopes (past the 8th)
    # fold into the shared muted gray.
    all_scopes = agg.usage_by_scope(primary_df)[["scope", "scope_label"]]
    color_map = scope_color_map(all_scopes["scope"].tolist())

    with st.sidebar:
        st.header("Scope of work")
        selected_labels = st.multiselect(
            "Filter scopes", all_scopes["scope_label"].tolist(), default=all_scopes["scope_label"].tolist()
        )
    selected_scopes = all_scopes[all_scopes["scope_label"].isin(selected_labels)]["scope"].tolist()

    period_df = agg.filter_by_timeframe(primary_df[primary_df["scope"].isin(selected_scopes)], start, end)
    growth_df = agg.period_over_period_growth(primary_df[primary_df["scope"].isin(selected_scopes)], start, end)
    full_period_df = agg.filter_by_timeframe(df[df["scope"].isin(selected_scopes)], start, end)

    # ---- KPI row -----------------------------------------------------
    total_current = int(growth_df["current_count"].sum())
    total_prior = int(growth_df["prior_count"].sum())
    total_pct = ((total_current - total_prior) / total_prior * 100) if total_prior else None
    top_scope = agg.usage_by_scope(period_df).head(1)
    att_stats = agg.attachment_stats(period_df)
    period_days = max((end - start).days, 1)
    avg_per_day = total_current / period_days

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Submissions this period", f"{total_current:,}", f"{total_pct:+.0f}% vs prior period" if total_pct is not None else None)
    c2.metric("Active scopes", int((agg.usage_by_scope(period_df)["submissions"] > 0).sum()))
    c3.metric("Top scope", top_scope["scope_label"].iloc[0] if not top_scope.empty else "—")
    c4.metric("Unique contributors", period_df["creator"].nunique())
    c5.metric("Avg. photos/submission", f"{att_stats['mean']:.1f}" if att_stats["mean"] is not None else "—")
    c6.metric("Avg. surveys/day", f"{avg_per_day:.1f}")

    tab_trends, tab_scope, tab_org, tab_growth, tab_detail, tab_team, tab_all_surveys, tab_deliverables, tab_raw, tab_reports = st.tabs(
        ["Usage Trends", "By Scope of Work", "By Organization", "Growth", "Inspection Detail", "Team & Photos", "All Surveys", "Deliverables", "Raw Data", "Reports"]
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

    with tab_org:
        st.caption(
            "Submissions by organization (\"the organization this data needs to be sent to\") across the "
            "selected scopes — some entries (e.g. TestOrg) may be dev/test data, not real client work."
        )
        org_usage_df = agg.usage_by_organization(period_df)
        org_color_map = scope_color_map(org_usage_df["organization"].tolist())

        left, right = st.columns([1, 1])
        with left:
            if not org_usage_df.empty:
                fig = px.bar(
                    org_usage_df, x="submissions", y="organization", orientation="h",
                    color="organization", color_discrete_map=org_color_map,
                )
                fig.update_layout(**CHART_LAYOUT, showlegend=False, yaxis_title="", xaxis_title="Submissions")
                fig.update_yaxes(categoryorder="total ascending")
                st.plotly_chart(fig, use_container_width=True)
        with right:
            org_growth_df = agg.organization_growth(primary_df[primary_df["scope"].isin(selected_scopes)], start, end)
            if not org_growth_df.empty:
                disp = org_growth_df.copy()
                disp["pct_change"] = disp["pct_change"].apply(lambda v: "new" if v == float("inf") else (f"{v:+.0f}%" if v is not None else "—"))
                st.dataframe(
                    disp.rename(columns={"organization": "Organization", "current_count": "Current", "prior_count": "Prior", "pct_change": "Change"}),
                    use_container_width=True, hide_index=True,
                )

        freq = "D" if (end - start) <= timedelta(days=60) else "W"
        org_trend_df = agg.organization_trend(period_df, freq=freq)
        if not org_trend_df.empty:
            st.caption("Submissions over time by organization.")
            fig = px.line(org_trend_df, x="period", y="submissions", color="organization", color_discrete_map=org_color_map, markers=True)
            fig.update_traces(line_width=2)
            fig.update_layout(**CHART_LAYOUT, yaxis_title="Submissions", xaxis_title="")
            st.plotly_chart(fig, use_container_width=True)

        st.divider()
        st.caption("Unique contributors and avg. photos per submission, by organization.")
        org_display = org_usage_df.copy()
        org_display["avg_attachments"] = org_display["avg_attachments"].round(1)
        st.dataframe(
            org_display.rename(
                columns={"organization": "Organization", "submissions": "Submissions", "unique_contributors": "Unique Contributors", "avg_attachments": "Avg. Photos"}
            ),
            use_container_width=True, hide_index=True,
        )

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

    with tab_detail:
        detail_scopes = [key for key in completion_config if key in selected_scopes]
        if not detail_scopes:
            st.info("No scope with multiple required surveys (e.g. Inspection) is selected/configured.")
        for scope_key in detail_scopes:
            required_families = completion_config[scope_key]
            all_families = family_order.get(scope_key, required_families)
            optional_families = [f for f in all_families if f not in required_families]
            scope_label = all_scopes.loc[all_scopes["scope"] == scope_key, "scope_label"].iloc[0]
            st.subheader(scope_label)

            family_df = agg.usage_by_family(period_df, scope_key)
            family_growth_df = agg.family_growth(primary_df[primary_df["scope"] == scope_key], scope_key, start, end)
            # Color by the scope's full family list (required + optional), not
            # just required_families — otherwise an optional family (e.g. guy,
            # pnt) falls outside the map and Plotly silently substitutes one of
            # its own default colors instead of our fixed palette.
            family_color_map = {fam: SERIES_COLORS[i % len(SERIES_COLORS)] for i, fam in enumerate(all_families)}

            fc1, fc2 = st.columns([1, 1])
            with fc1:
                caption = f"Submissions by family this period — {', '.join(required_families)} required"
                if optional_families:
                    caption += f", {', '.join(optional_families)} optional"
                st.caption(caption + " (QA excluded).")
                if not family_df.empty:
                    fig = px.bar(family_df, x="submissions", y="family", orientation="h", color="family", color_discrete_map=family_color_map)
                    fig.update_layout(**CHART_LAYOUT, showlegend=False, yaxis_title="", xaxis_title="Submissions")
                    fig.update_yaxes(categoryorder="total ascending")
                    st.plotly_chart(fig, use_container_width=True)
            with fc2:
                st.caption("Growth vs. prior period, by family.")
                if not family_growth_df.empty:
                    disp = family_growth_df.copy()
                    disp["pct_change"] = disp["pct_change"].apply(lambda v: "new" if v == float("inf") else (f"{v:+.0f}%" if v is not None else "—"))
                    st.dataframe(
                        disp.rename(columns={"family": "Family", "current_count": "Current", "prior_count": "Prior", "pct_change": "Change"}),
                        use_container_width=True, hide_index=True,
                    )

            freq = "D" if (end - start) <= timedelta(days=60) else "W"
            family_trend_df = agg.family_trend(period_df, scope_key, freq=freq)
            if not family_trend_df.empty:
                st.caption("Submissions over time by family.")
                fig = px.line(family_trend_df, x="period", y="submissions", color="family", color_discrete_map=family_color_map, markers=True)
                fig.update_traces(line_width=2)
                fig.update_layout(**CHART_LAYOUT, yaxis_title="Submissions", xaxis_title="")
                st.plotly_chart(fig, use_container_width=True)

            st.divider()
            site_df = agg.site_completion(full_period_df, scope_key, required_families)
            summary = agg.completion_summary(site_df)
            st.caption(
                f"Site completion — a site counts as complete once it has a submission for every required survey "
                f"({', '.join(required_families)}). Site IDs are free text entered by field techs, so treat this as "
                "directionally right rather than exact."
            )
            s1, s2, s3 = st.columns(3)
            s1.metric("Sites worked this period", summary["total_sites"])
            s2.metric("Complete packages", summary["complete_sites"], f"{summary['pct_complete']:.0f}%" if summary["pct_complete"] is not None else None)
            s3.metric("Partial (missing a survey)", summary["partial_sites"])
            if not site_df.empty:
                st.dataframe(
                    site_df.rename(
                        columns={
                            "site_id": "Site ID", "site_name": "Site Name", "families_present": "Families Present",
                            "missing_families": "Missing", "is_complete": "Complete", "submissions": "Submissions",
                            "last_submitted": "Last Submitted", "qa_reviewed": "QA Reviewed",
                        }
                    ),
                    use_container_width=True, hide_index=True,
                )

    with tab_team:
        activity = agg.contributor_activity(primary_df[primary_df["scope"].isin(selected_scopes)], start, end)
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

    with tab_all_surveys:
        st.caption(
            "Every individual survey across the selected scopes, not rolled up — the same view you'd get "
            "checking each survey in AGOL one at a time, but side by side. Includes QA surveys as their own rows."
        )
        survey_df = agg.survey_summary(full_period_df)
        if survey_df.empty:
            st.info("No submissions in this time frame.")
        else:
            fig = px.bar(survey_df, x="submissions", y="survey_title", orientation="h")
            fig.update_traces(marker_color=SERIES_COLORS[0])
            fig.update_layout(**CHART_LAYOUT, yaxis_title="", xaxis_title="Submissions", height=max(300, 28 * len(survey_df)))
            fig.update_yaxes(categoryorder="total ascending")
            st.plotly_chart(fig, use_container_width=True)

            display = survey_df.copy()
            display["avg_attachments"] = display["avg_attachments"].round(1)
            st.dataframe(
                display.rename(
                    columns={
                        "survey_title": "Survey", "scope_label": "Scope", "submissions": "Submissions",
                        "unique_contributors": "Unique Contributors", "avg_attachments": "Avg. Photos",
                        "last_submitted": "Last Submitted",
                    }
                )[["Survey", "Scope", "Submissions", "Unique Contributors", "Avg. Photos", "Last Submitted"]],
                use_container_width=True, hide_index=True,
            )

    with tab_deliverables:
        st.caption(
            "Monthly deliverable/report counts from your internal database (not ArcGIS) — upload each month's "
            "export to see true deliverable counts per organization, and compare against Survey123 activity."
        )

        with st.expander("Upload deliverables", expanded=not dlv.list_periods()):
            st.caption("Upload one file per month, or several at once — each gets its own month picker below, guessed from the filename where possible. Files resolved to the same month are merged together.")
            uploaded_files = st.file_uploader(
                "CSV(s) with columns: organization_name, template, report_count (organizationId/templateId/engine optional)",
                type="csv",
                accept_multiple_files=True,
            )
            file_frames: dict[str, pd.DataFrame] = {}
            file_periods: dict[str, str] = {}
            for f in uploaded_files or []:
                try:
                    raw_upload = pd.read_csv(f)
                except Exception as e:
                    st.error(f"{f.name}: couldn't read file ({e})")
                    continue
                missing = dlv.validate_columns(raw_upload)
                if missing:
                    st.error(f"{f.name}: missing required column(s): {', '.join(missing)}")
                    continue

                fcol1, fcol2 = st.columns([2, 1])
                with fcol1:
                    st.write(f"**{f.name}** — {len(raw_upload)} rows, {raw_upload['organization_name'].nunique()} organizations")
                with fcol2:
                    month_value = st.date_input(
                        "Month", value=dlv.guess_period_from_filename(f.name),
                        key=f"deliverables_period_{f.name}", label_visibility="collapsed",
                    )
                file_frames[f.name] = raw_upload
                file_periods[f.name] = f"{month_value.year}-{month_value.month:02d}"

            if file_frames:
                by_period: dict[str, list[pd.DataFrame]] = {}
                for fname, frame in file_frames.items():
                    by_period.setdefault(file_periods[fname], []).append(frame)
                summary = ", ".join(f"{p} ({len(fs)} file{'s' if len(fs) > 1 else ''})" for p, fs in sorted(by_period.items()))
                if st.button(f"Save {len(file_frames)} file(s) — {summary}", key="save_deliverables_btn"):
                    for period_str, frames in by_period.items():
                        combined = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
                        dlv.save_deliverables(combined, period_str)
                    st.success(f"Saved {len(file_frames)} file(s) across {len(by_period)} month(s).")
                    st.rerun()

        periods = dlv.list_periods()
        if not periods:
            st.info("No deliverables uploaded yet.")
        else:
            selected_period = st.selectbox("Period", periods, index=len(periods) - 1)
            deliverables_df = dlv.load_deliverables(selected_period)

            d1, d2, d3 = st.columns(3)
            d1.metric("Total deliverables", int(deliverables_df["report_count"].sum()))
            d2.metric("Organizations", deliverables_df["organization_name"].nunique())
            d3.metric("Templates used", deliverables_df["template"].nunique())

            dleft, dright = st.columns([1, 1])
            with dleft:
                st.caption("Deliverables by organization")
                org_df = dlv.deliverables_by_org(deliverables_df)
                fig = px.bar(org_df, x="report_count", y="organization_name", orientation="h")
                fig.update_traces(marker_color=SERIES_COLORS[0])
                fig.update_layout(**CHART_LAYOUT, yaxis_title="", xaxis_title="Reports")
                fig.update_yaxes(categoryorder="total ascending")
                st.plotly_chart(fig, use_container_width=True)
            with dright:
                st.caption("Deliverables by engine (fluent = legacy template engine, fieldsync = current)")
                engine_df = dlv.deliverables_by_engine(deliverables_df)
                fig = px.bar(engine_df, x="report_count", y="engine", orientation="h")
                fig.update_traces(marker_color=SERIES_COLORS[1])
                fig.update_layout(**CHART_LAYOUT, yaxis_title="", xaxis_title="Reports")
                fig.update_yaxes(categoryorder="total ascending")
                st.plotly_chart(fig, use_container_width=True)

            st.caption("Deliverables by template")
            template_display = dlv.deliverables_by_template(deliverables_df)
            template_display["engine"] = template_display["engine"].fillna("—")
            st.dataframe(
                template_display.rename(
                    columns={"template": "Template", "engine": "Engine", "report_count": "Reports", "org_count": "Organizations"}
                ),
                use_container_width=True, hide_index=True,
            )

            st.divider()
            period_start, period_end = dlv.period_bounds(selected_period)
            s123_period_df = agg.filter_by_timeframe(primary_df, period_start, period_end)
            s123_org_usage = agg.usage_by_organization(s123_period_df)
            comparison_df = dlv.compare_to_survey123(deliverables_df, s123_org_usage)
            st.caption(
                f"DB deliverables vs. Survey123 submissions per organization, both for {selected_period}. Not a "
                "strict 1:1 — one deliverable can bundle several submissions — but a large gap either way is worth a look."
            )
            st.dataframe(
                comparison_df.rename(columns={"organization": "Organization", "report_count": "DB Deliverables", "submissions": "Survey123 Submissions"}),
                use_container_width=True, hide_index=True,
            )

            st.divider()
            if st.button(f"Delete {selected_period}", key="delete_deliverables_btn"):
                (dlv.DELIVERABLES_DIR / f"{selected_period}.csv").unlink(missing_ok=True)
                st.rerun()

    with tab_raw:
        st.dataframe(full_period_df.sort_values("submitted_date", ascending=False), use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV",
            full_period_df.to_csv(index=False).encode("utf-8"),
            file_name="survey123_scope_of_work.csv",
            mime="text/csv",
        )

    with tab_reports:
        st.caption("Generate a PDF for whatever time frame and scopes are currently selected in the sidebar.")
        if st.button("Generate PDF report for this view"):
            pdf_bytes = report.generate_report(
                df[df["scope"].isin(selected_scopes)], start, end,
                completion_config=completion_config, family_order=family_order,
            )
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
