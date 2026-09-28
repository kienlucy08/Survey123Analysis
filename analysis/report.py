"""Generate a PDF report of usage/growth/contributor/attachment metrics for a
time frame, and maintain a local monthly archive of these reports.

Two ways this gets triggered (dashboard/app.py wires both up):
  - on demand, for whatever time frame + scope filter is currently selected.
  - automatically, once per calendar month, building up data/reports/ as an
    archive. Completed past months are generated once and left alone; the
    current (still-accruing) month is regenerated every time so it stays current.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from analysis import aggregate as agg
from analysis import deliverables as dlv

REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "reports"

SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
MUTED = "#898781"

TABLE_HEADER_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0efec")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0b0b0b")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e1e0d9")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fcfcfb")]),
    ]
)


def _color_map(scope_keys: list[str]) -> dict[str, str]:
    return {key: (SERIES_COLORS[i] if i < len(SERIES_COLORS) else MUTED) for i, key in enumerate(scope_keys)}


CHART_FONT = dict(family="Helvetica", size=13)


def _fig_to_image(fig, width_in: float = 6.8, height_px: int = 620) -> Image:
    """Renders a plotly figure to a print-quality PNG sized for the page.
    automargin on every axis (set by the caller) is what keeps long category
    labels, axis titles, and legends from being clipped — a fixed pixel
    margin can't anticipate how much room "Construction Close Out" needs."""
    width_px = 1360
    fig.update_layout(font=CHART_FONT)
    png_bytes = fig.to_image(format="png", width=width_px, height=height_px, scale=2)
    return Image(io.BytesIO(png_bytes), width=width_in * inch, height=width_in * inch * height_px / width_px)


def _table(headers: list[str], rows: list[list[str]]) -> Table:
    table = Table([headers] + rows, hAlign="LEFT")
    table.setStyle(TABLE_HEADER_STYLE)
    return table


def generate_report(
    df: pd.DataFrame,
    start: datetime,
    end: datetime,
    title: str = "Survey123 Scope of Work Report",
    completion_config: dict[str, list[str]] | None = None,
    family_order: dict[str, list[str]] | None = None,
) -> bytes:
    """Builds the PDF for the submissions in [start, end] and returns its bytes.
    completion_config is {scope_key: [required family, ...]} — see
    scope.get_completion_config() — and adds a site-completion table for any
    scope in it (currently just Inspection). family_order is
    {scope_key: [every family, ...]} — see scope.get_family_order() — used so
    an optional family (e.g. guy, pnt) still gets a stable color from the
    fixed palette instead of falling outside the map."""
    styles = getSampleStyleSheet()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    story = []

    # QA submissions (Inspection's *_qa surveys) are a reviewer's second pass
    # over an already-counted site — excluded from every rollup below so
    # "submissions" means actual field survey visits. Site completion is the
    # one section that needs the un-filtered `df` (it wants QA rows too, to
    # know whether a site's been reviewed), and the All Surveys table uses
    # `df` as well since each survey is its own row there (no double-count risk).
    primary_df = agg.primary_only(df)
    period_df = agg.filter_by_timeframe(primary_df, start, end)
    full_period_df = agg.filter_by_timeframe(df, start, end)
    usage_df = agg.usage_by_scope(period_df)
    growth_df = agg.period_over_period_growth(primary_df, start, end)
    top = agg.top_contributors(period_df, n=10)
    activity = agg.contributor_activity(primary_df, start, end)
    att_stats = agg.attachment_stats(period_df)

    ordered_scope_keys = agg.usage_by_scope(primary_df)["scope"].tolist()
    color_map = _color_map(ordered_scope_keys)

    story.append(Paragraph(title, styles["Title"]))
    story.append(Paragraph(f"{start:%b %d, %Y} &ndash; {end:%b %d, %Y}", styles["Normal"]))
    story.append(Paragraph(f"Generated {datetime.now():%b %d, %Y %H:%M}", styles["Normal"]))
    story.append(Spacer(1, 0.25 * inch))

    total_current = int(growth_df["current_count"].sum())
    total_prior = int(growth_df["prior_count"].sum())
    total_pct = f"{(total_current - total_prior) / total_prior * 100:+.0f}%" if total_prior else "—"
    period_days = max((end - start).days, 1)
    kpi_rows = [
        ["Submissions this period", f"{total_current:,}"],
        ["Change vs. prior period", total_pct],
        ["Avg. surveys per day", f"{total_current / period_days:.1f}"],
        ["Active scopes of work", str(int((usage_df["submissions"] > 0).sum()))],
        ["Unique contributors", str(activity["total_contributors"])],
        ["New contributors", str(activity["new_contributors"])],
        ["Avg. submissions per contributor", f"{activity['avg_submissions_per_contributor']:.1f}"],
        ["Avg. photos per submission", f"{att_stats['mean']:.1f}" if att_stats["mean"] is not None else "—"],
        ["Most common photo count per submission", str(att_stats["mode"]) if att_stats["mode"] is not None else "—"],
    ]
    story.append(Paragraph("Summary", styles["Heading2"]))
    story.append(
        Paragraph(
            "Headline numbers for this period, including growth vs. the immediately preceding period of equal length.",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    story.append(_table(["Metric", "Value"], kpi_rows))
    story.append(Spacer(1, 0.3 * inch))

    if not usage_df.empty:
        fig = px.bar(
            usage_df, x="submissions", y="scope_label", orientation="h",
            color="scope", color_discrete_map=color_map,
        )
        fig.update_layout(
            showlegend=False, plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(l=20, r=30, t=20, b=50),
        )
        fig.update_yaxes(categoryorder="total ascending", title="", automargin=True)
        fig.update_xaxes(title="Submissions", automargin=True)
        story.append(
            KeepTogether(
                [
                    Paragraph("Usage by Scope of Work", styles["Heading2"]),
                    Paragraph(
                        "Total submissions in this period for each scope of work, most active at top. "
                        "Shows where field activity is concentrated right now.",
                        styles["Normal"],
                    ),
                    Spacer(1, 0.1 * inch),
                    _fig_to_image(fig, height_px=max(280, 90 + 60 * len(usage_df))),
                ]
            )
        )
        story.append(Spacer(1, 0.25 * inch))

    org_usage_df = agg.usage_by_organization(period_df)
    if not org_usage_df.empty:
        org_color_map = _color_map(org_usage_df["organization"].tolist())
        fig = px.bar(
            org_usage_df, x="submissions", y="organization", orientation="h",
            color="organization", color_discrete_map=org_color_map,
        )
        fig.update_layout(showlegend=False, plot_bgcolor="white", paper_bgcolor="white", margin=dict(l=20, r=30, t=20, b=50))
        fig.update_yaxes(categoryorder="total ascending", title="", automargin=True)
        fig.update_xaxes(title="Submissions", automargin=True)
        story.append(
            KeepTogether(
                [
                    Paragraph("Submissions by Organization", styles["Heading2"]),
                    Paragraph(
                        "Total submissions this period per organization (\"the organization this data needs to "
                        "be sent to\"). Some entries (e.g. TestOrg) may be dev/test data, not real client work.",
                        styles["Normal"],
                    ),
                    Spacer(1, 0.1 * inch),
                    _fig_to_image(fig, height_px=max(220, 90 + 60 * len(org_usage_df))),
                ]
            )
        )
        story.append(Spacer(1, 0.25 * inch))

    freq = "D" if (end - start).days <= 60 else "W"
    trend_df = agg.trend(period_df, freq=freq)
    if not trend_df.empty:
        fig = px.line(
            trend_df, x="period", y="submissions", color="scope_label",
            color_discrete_map={row.scope_label: color_map[row.scope] for row in agg.usage_by_scope(primary_df).itertuples()},
        )
        fig.update_layout(
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(l=20, r=20, t=70, b=50),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=""),
        )
        fig.update_yaxes(title="Submissions", automargin=True, rangemode="tozero")
        fig.update_xaxes(title="", automargin=True)
        cadence = "Daily" if freq == "D" else "Weekly"
        story.append(
            KeepTogether(
                [
                    Paragraph("Submissions Over Time", styles["Heading2"]),
                    Paragraph(
                        f"{cadence} submission counts per scope across the period, so spikes, gaps, and trend "
                        "direction are visible at a glance.",
                        styles["Normal"],
                    ),
                    Spacer(1, 0.1 * inch),
                    _fig_to_image(fig, height_px=560),
                ]
            )
        )
        story.append(Spacer(1, 0.25 * inch))

    for scope_key, required_families in (completion_config or {}).items():
        family_df = agg.usage_by_family(period_df, scope_key)
        site_df = agg.site_completion(full_period_df, scope_key, required_families)
        if family_df.empty and site_df.empty:
            continue
        scope_label = usage_df.loc[usage_df["scope"] == scope_key, "scope_label"]
        scope_label = scope_label.iloc[0] if not scope_label.empty else scope_key
        all_families = (family_order or {}).get(scope_key, required_families)
        optional_families = [f for f in all_families if f not in required_families]
        # Color by the scope's full family list (required + optional), not
        # just required_families — otherwise an optional family falls outside
        # the map and Plotly substitutes one of its own default colors.
        family_color_map = {fam: SERIES_COLORS[i % len(SERIES_COLORS)] for i, fam in enumerate(all_families)}

        if not family_df.empty:
            fig = px.bar(family_df, x="submissions", y="family", orientation="h", color="family", color_discrete_map=family_color_map)
            fig.update_layout(showlegend=False, plot_bgcolor="white", paper_bgcolor="white", margin=dict(l=20, r=30, t=20, b=50))
            fig.update_yaxes(categoryorder="total ascending", title="", automargin=True)
            fig.update_xaxes(title="Submissions", automargin=True)
            requirement_note = f"{', '.join(required_families)} required"
            if optional_families:
                requirement_note += f", {', '.join(optional_families)} optional"
            story.append(
                KeepTogether(
                    [
                        Paragraph(f"{scope_label} — Breakdown by Survey", styles["Heading2"]),
                        Paragraph(
                            f"{scope_label} submissions this period split across its component surveys "
                            f"({requirement_note}). QA review submissions are excluded so these "
                            "counts reflect actual field visits.",
                            styles["Normal"],
                        ),
                        Spacer(1, 0.1 * inch),
                        _fig_to_image(fig, height_px=max(220, 90 + 60 * len(family_df))),
                    ]
                )
            )
            story.append(Spacer(1, 0.25 * inch))

        if not site_df.empty:
            summary = agg.completion_summary(site_df)
            rows = [
                [r.site_id, r.site_name, r.families_present, r.missing_families, "Yes" if r.qa_reviewed else "No"]
                for r in site_df.itertuples()
            ]
            story.append(
                KeepTogether(
                    [
                        Paragraph(f"{scope_label} — Site Completion", styles["Heading2"]),
                        Paragraph(
                            f"Sites with at least one {scope_label} submission this period, and whether every required "
                            f"survey ({', '.join(required_families)}) has been collected for that site. "
                            f"{summary['complete_sites']} of {summary['total_sites']} sites "
                            f"({summary['pct_complete']:.0f}%) have a complete package. Site IDs are free text entered "
                            "by field techs, so treat this as directionally right rather than exact.",
                            styles["Normal"],
                        ),
                        Spacer(1, 0.1 * inch),
                        _table(["Site ID", "Site Name", "Families Present", "Missing", "QA Reviewed"], rows[:30]),
                    ]
                    + ([Paragraph(f"...and {len(rows) - 30} more sites.", styles["Normal"])] if len(rows) > 30 else [])
                )
            )
            story.append(Spacer(1, 0.3 * inch))

    if not growth_df.empty:
        rows = [
            [r.scope_label, f"{r.current_count:,}", f"{r.prior_count:,}", ("new" if r.pct_change == float("inf") else (f"{r.pct_change:+.0f}%" if r.pct_change is not None else "—"))]
            for r in growth_df.itertuples()
        ]
        story.append(
            KeepTogether(
                [
                    Paragraph("Growth vs. Prior Period", styles["Heading2"]),
                    Paragraph(
                        f"Each scope's submissions this period against the prior {period_days}-day period, to "
                        "flag where usage is climbing or falling off.",
                        styles["Normal"],
                    ),
                    Spacer(1, 0.1 * inch),
                    _table(["Scope", "Current", "Prior", "Change"], rows),
                ]
            )
        )
        story.append(Spacer(1, 0.3 * inch))

    if not top.empty:
        rows = [[r.creator, str(r.submissions), f"{r.avg_attachments:.1f}" if pd.notna(r.avg_attachments) else "—"] for r in top.itertuples()]
        story.append(
            KeepTogether(
                [
                    Paragraph("Top Contributors", styles["Heading2"]),
                    Paragraph(
                        "The most active submitters this period, with their average number of attached photos per submission.",
                        styles["Normal"],
                    ),
                    Spacer(1, 0.1 * inch),
                    _table(["Contributor", "Submissions", "Avg. Photos"], rows),
                ]
            )
        )

    deliverables_period = start.strftime("%Y-%m")
    deliverables_df = dlv.load_deliverables(deliverables_period)
    if not deliverables_df.empty:
        story.append(Spacer(1, 0.3 * inch))
        org_deliverables_df = dlv.deliverables_by_org(deliverables_df)
        template_df = dlv.deliverables_by_template(deliverables_df).head(15)
        s123_org_usage = agg.usage_by_organization(agg.filter_by_timeframe(primary_df, *dlv.period_bounds(deliverables_period)))
        comparison_df = dlv.compare_to_survey123(deliverables_df, s123_org_usage)

        org_rows = [[r.organization_name, str(r.report_count)] for r in org_deliverables_df.itertuples()]
        template_rows = [
            [r.template, r.engine if pd.notna(r.engine) else "—", str(r.report_count), str(r.org_count)]
            for r in template_df.itertuples()
        ]
        comparison_rows = [[r.organization, str(r.report_count), str(r.submissions)] for r in comparison_df.itertuples()]
        total_deliverables = int(deliverables_df["report_count"].sum())
        total_orgs = deliverables_df["organization_name"].nunique()

        story.append(
            KeepTogether(
                [
                    Paragraph("Deliverables (from Database)", styles["Heading2"]),
                    Paragraph(
                        f"True deliverable/report counts for {deliverables_period} from the internal database "
                        f"(not ArcGIS) — {total_deliverables:,} total across {total_orgs} organizations.",
                        styles["Normal"],
                    ),
                    Spacer(1, 0.1 * inch),
                    _table(["Organization", "Deliverables"], org_rows),
                ]
            )
        )
        story.append(Spacer(1, 0.25 * inch))
        story.append(
            KeepTogether(
                [
                    Paragraph("Deliverables by Template", styles["Heading2"]),
                    _table(["Template", "Engine", "Deliverables", "Organizations"], template_rows),
                ]
            )
        )
        story.append(Spacer(1, 0.25 * inch))
        story.append(
            KeepTogether(
                [
                    Paragraph("Deliverables vs. Survey123 Submissions", styles["Heading2"]),
                    Paragraph(
                        "Not a strict 1:1 — one deliverable can bundle several submissions — but a large gap "
                        "either way is worth a look.",
                        styles["Normal"],
                    ),
                    Spacer(1, 0.1 * inch),
                    _table(["Organization", "DB Deliverables", "Survey123 Submissions"], comparison_rows),
                ]
            )
        )

    survey_df = agg.survey_summary(full_period_df)
    if not survey_df.empty:
        story.append(Spacer(1, 0.3 * inch))
        rows = [
            [r.survey_title, r.scope_label, str(r.submissions), f"{r.avg_attachments:.1f}" if pd.notna(r.avg_attachments) else "—"]
            for r in survey_df.itertuples()
        ]
        story.append(
            KeepTogether(
                [
                    Paragraph("All Surveys", styles["Heading2"]),
                    Paragraph(
                        "Every individual survey this period, not rolled up by scope of work — the same view as "
                        "checking each survey in AGOL one at a time, but side by side. Includes QA surveys as "
                        "their own rows.",
                        styles["Normal"],
                    ),
                    Spacer(1, 0.1 * inch),
                    _table(["Survey", "Scope", "Submissions", "Avg. Photos"], rows),
                ]
            )
        )

    doc.build(story)
    return buffer.getvalue()


def ensure_monthly_archive(
    df: pd.DataFrame,
    reports_dir: Path = REPORTS_DIR,
    completion_config: dict[str, list[str]] | None = None,
    family_order: dict[str, list[str]] | None = None,
) -> list[Path]:
    """Writes one report per calendar month present in df into reports_dir.
    Skips months that already have a file, except the current month, which
    is always regenerated so it reflects the latest data."""
    if df.empty:
        return []
    reports_dir.mkdir(parents=True, exist_ok=True)

    months = df[agg.DATE_COLUMN].dt.tz_convert(None).dt.to_period("M").unique()
    current_month = pd.Timestamp.now("UTC").tz_localize(None).to_period("M")
    written = []

    for month in sorted(months):
        out_path = reports_dir / f"{month}.pdf"
        if out_path.exists() and month != current_month:
            continue
        start = month.to_timestamp(how="start").tz_localize("UTC")
        end = month.to_timestamp(how="end").tz_localize("UTC")
        pdf_bytes = generate_report(
            df, start, end, title=f"Survey123 Scope of Work Report — {month.strftime('%B %Y')}",
            completion_config=completion_config, family_order=family_order,
        )
        out_path.write_bytes(pdf_bytes)
        written.append(out_path)

    return written
