"""Blended RPE dashboard.

Run with:  streamlit run app.py
"""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

import formatting as fmt
import loader
import pipeline
from metrics import (
    FILTERS,
    apply_filters,
    breakdown,
    comparison_range,
    describe_filters,
    last_full_day,
    pct_change,
    pp_change,
    rolling_rpe,
    routing_detail,
    starts_available,
    summarise,
)
from settings import CONFIG_PATH, load_settings

st.set_page_config(page_title="Blended RPE", layout="wide")

# Fixed colour per channel (categorical slots 1 to 3, validated together).
CHANNEL_COLOURS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]

DEFINITIONS = [
    ("Enquiry", "A completed lead that started a journey. Partial leads (`formType` starting `partial-`) never count."),
    ("Add-on", "A completed lead that rides on another one, such as an energy cross sell after a broadband quote. Its revenue counts, but it isn't an extra enquiry."),
    ("Started in", "The journey the customer began in, taken from the form they were on."),
    ("Landed as", "The product the lead was recorded as."),
    ("Reported", "Figures by what the lead landed as. This is how channels report today."),
    ("Flattened", "Reported, minus leads that started in another journey."),
    ("Blended", "Everything a journey's enquiries earned, wherever they landed. This is the headline number."),
    ("RPE", "Revenue per enquiry: blended revenue ÷ blended enquiries."),
    ("Form starts", "Unique sessions that landed on a journey's form. This is the volume measure until site visits are available."),
    ("Form conversion", "Form starts that produced a completed lead ÷ form starts."),
]


# Data ----------------------------------------------------------------------


@st.cache_resource(show_spinner="Processing lead exports...", max_entries=2)
def get_model(data_version: str, config_version: float) -> pipeline.Model:
    return pipeline.build(load_settings())


def current_model() -> pipeline.Model:
    return get_model(loader.data_version(), CONFIG_PATH.stat().st_mtime)


# Sidebar: uploads ------------------------------------------------------------


def upload_section() -> None:
    st.sidebar.header("Lead exports")
    key = f"uploader-{st.session_state.get('upload_round', 0)}"
    files = st.sidebar.file_uploader(
        "Drop lead exports here (enquiries-master-anon-*.csv)",
        type=["csv"],
        accept_multiple_files=True,
        key=key,
    )
    if files:
        results = [loader.save_upload(f.name, f.getvalue()) for f in files]
        st.session_state["upload_results"] = results
        st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
        st.session_state["open_health"] = any(r.status == "saved" for r in results)
        st.rerun()

    for result in st.session_state.pop("upload_results", []):
        if result.status == "saved":
            st.sidebar.success(f"{result.name}: {result.message}")
        elif result.status == "duplicate":
            st.sidebar.info(f"{result.name}: {result.message}. Nothing changed.")
        else:
            st.sidebar.error(f"{result.name}: {result.message}")


def manage_files_section(model: pipeline.Model) -> None:
    files = model.health["files"]
    with st.sidebar.expander(f"Uploaded files ({len(files)})"):
        if files.empty:
            st.write("No exports uploaded yet.")
        for _, f in files.iterrows():
            cols = st.columns([4, 1])
            span = f"{f['From']:%d %b %Y} to {f['To']:%d %b %Y}" if f["From"] else "no dated rows"
            cols[0].markdown(f"**{f['File']}**  \n{span}, {f['Rows']:,} rows")
            if cols[1].button("Remove", key=f"rm-{f['File']}"):
                loader.remove_export(f["File"])
                st.session_state["open_health"] = True
                st.rerun()


# Sidebar: controls -----------------------------------------------------------


def date_controls(last_day: date, first_day: date) -> tuple[date, date, str, str]:
    st.sidebar.header("View")
    default = (last_day.replace(day=1), last_day)
    picked = st.sidebar.date_input(
        "Date range", value=default, min_value=first_day, max_value=last_day, format="DD/MM/YYYY"
    )
    if isinstance(picked, (tuple, list)) and len(picked) == 2:
        start, end = picked
    else:
        start = end = picked[0] if isinstance(picked, (tuple, list)) else picked
    granularity = st.sidebar.radio("Granularity", ["Day", "Week", "Month"], index=0, horizontal=True)
    compare = st.sidebar.selectbox("Compare with", ["Previous period", "Same days last month", "Nothing"])
    return start, end, granularity, compare


def filter_controls(model: pipeline.Model) -> dict:
    st.sidebar.header("Filters")
    selection: dict = {}
    for spec in FILTERS:
        values = set(model.leads[spec.key].unique()) if spec.key in model.leads else set()
        if spec.filters_starts and spec.key in model.starts:
            values |= set(model.starts[spec.key].unique())
        options = sorted(values)
        with st.sidebar.expander(spec.label):
            everything = st.checkbox("All", value=True, key=f"all-{spec.key}")
            if everything:
                selection[spec.key] = None
                continue
            picked = st.multiselect(spec.label, options, default=options, key=f"ms-{spec.key}",
                                    label_visibility="collapsed")
            selection[spec.key] = None if set(picked) == set(options) else set(picked)
            if not spec.filters_starts:
                st.caption("Form starts can't be filtered on this field.")
    return selection


# Main panels -----------------------------------------------------------------


def definitions_panel() -> None:
    with st.expander("What these figures mean"):
        st.table(pd.DataFrame(DEFINITIONS, columns=["Term", "Meaning"]).set_index("Term"))


def health_panel(model: pipeline.Model) -> None:
    h = model.health
    unmapped_ids, unmapped_types = h["unmapped_form_ids"], h["unmapped_form_types"]
    needs_mapping = len(unmapped_ids) or len(unmapped_types)
    label = "Data health" + (" (mapping needs updating)" if needs_mapping else "")
    with st.expander(label, expanded=bool(st.session_state.pop("open_health", False) or needs_mapping)):
        first, last = h["coverage"]
        c = st.columns(4)
        c[0].metric("Files loaded", fmt.count(len(h["files"])))
        c[1].metric("Date coverage", f"{first:%d %b} to {last:%d %b %Y}" if first is not None else "None")
        c[2].metric("Rows after dedupe", fmt.count(h["rows_after_dedupe"]))
        c[3].metric("Duplicates removed", fmt.count(h["duplicates_removed"]))
        c = st.columns(4)
        c[0].metric("Unmapped form ids", fmt.count(len(unmapped_ids)))
        c[1].metric("Unmapped form types", fmt.count(len(unmapped_types)))
        c[2].metric("Partial rows carrying commission", fmt.count(h["partial_with_commission"]),
                    help=f"{fmt.money(h['partial_commission_total'])} in total. Excluded with the other partials.")
        c[3].metric("Completed leads", fmt.count(h["completed_leads"]))
        if len(unmapped_ids):
            st.warning("These form ids aren't in config.toml. Their leads fall back to the channel they landed as. "
                       "Add them to the right channel's form_ids.")
            st.dataframe(unmapped_ids, hide_index=True)
        if len(unmapped_types):
            st.warning("These form types aren't in config.toml, so their leads show as Unmapped. "
                       "Add them to the right channel's form_types.")
            st.dataframe(unmapped_types, hide_index=True)
        if h["rows_before_start"]:
            st.caption(f"{h['rows_before_start']:,} rows from before the start date were ignored.")
        if h["unreadable_dates"]:
            st.error(f"{h['unreadable_dates']:,} rows had a created_at date that couldn't be read and were skipped.")
        st.dataframe(model.health["files"], hide_index=True)


def routing_line(detail: dict, channel: str) -> str:
    parts = []
    for landed, r in detail["out"].iterrows():
        n, addons = int(r["leads"]), int(r["addons"])
        if addons == 0:
            parts.append(f"Includes {n:,} enquiries that landed as {landed}")
        elif addons == n:
            parts.append(f"Includes {n:,} add-on leads that landed as {landed}")
        else:
            parts.append(f"Includes {n:,} leads that landed as {landed} ({addons:,} add-ons)")
    for started, n in detail["in"].items():
        parts.append(f"Excludes {int(n):,} leads that started in {started}")
    return "; ".join(parts) if parts else "No leads moved in or out of this journey in this period"


CARD_METRICS = [
    # (label, key, formatter, change type). Spend and site visits drop in here later.
    ("Revenue", "blended_revenue", fmt.money_k, "pct"),
    ("Enquiries", "blended_enquiries", fmt.count, "pct"),
    ("RPE", "rpe", fmt.rpe, "pct"),
    ("Form starts", "form_starts", fmt.count, "pct"),
    ("Form conversion", "form_conversion", fmt.percent, "pp"),
]


def channel_card(channel, current, previous, detail, starts_ok, table) -> None:
    with st.container(border=True):
        st.subheader(channel)
        cols = st.columns(len(CARD_METRICS))
        for col, (label, key, formatter, change) in zip(cols, CARD_METRICS):
            value = current.get(key)
            if key in ("form_starts", "form_conversion") and not starts_ok:
                col.metric(label, "n/a", help=fmt.NOT_AVAILABLE)
                col.caption(fmt.NOT_AVAILABLE)
                continue
            delta = None
            if previous is not None:
                prev = previous.get(key)
                delta = fmt.pp_delta(pp_change(value, prev)) if change == "pp" else fmt.pct_delta(pct_change(value, prev))
            col.metric(
                label,
                formatter(value),
                delta=delta,
                delta_arrow="off" if delta in ("0%", "0.0pp") else "auto",
            )
        st.caption(routing_line(detail, channel))
        with st.expander(f"{channel} breakdown"):
            st.dataframe(display_breakdown(table, starts_ok), hide_index=True, width="stretch")


BREAKDOWN_COLUMNS = [
    ("Reported leads", "reported_leads", fmt.count),
    ("Reported revenue", "reported_revenue", fmt.money),
    ("Reported RPE", "reported_rpe", fmt.rpe),
    ("Flattened leads", "flattened_leads", fmt.count),
    ("Flattened revenue", "flattened_revenue", fmt.money),
    ("Flattened RPE", "flattened_rpe", fmt.rpe),
    ("Blended enquiries", "blended_enquiries", fmt.count),
    ("Blended revenue", "blended_revenue", fmt.money),
    ("Blended RPE", "rpe", fmt.rpe),
    ("Routed in leads", "routed_in_leads", fmt.count),
    ("Routed in revenue", "routed_in_revenue", fmt.money),
    ("Routed out leads", "routed_out_leads", fmt.count),
    ("Routed out revenue", "routed_out_revenue", fmt.money),
    ("Form starts", "form_starts", fmt.count),
    ("Form conversion", "form_conversion", fmt.percent),
]


def display_breakdown(table: pd.DataFrame, starts_ok: bool) -> pd.DataFrame:
    out = pd.DataFrame({"Period": table["label"]})
    for label, key, formatter in BREAKDOWN_COLUMNS:
        if key in ("form_starts", "form_conversion") and not starts_ok:
            out[label] = fmt.NOT_AVAILABLE
        else:
            out[label] = table[key].map(formatter)
    return out


def export_breakdown(tables: dict[str, pd.DataFrame], starts_ok: bool) -> bytes:
    frames = []
    for channel, table in tables.items():
        out = pd.DataFrame({"Channel": channel, "Period": table["label"], "Period start": table["period"].dt.date})
        for label, key, _ in BREAKDOWN_COLUMNS:
            if key in ("form_starts", "form_conversion") and not starts_ok:
                out[label] = fmt.NOT_AVAILABLE
            else:
                values = table[key]
                out[label] = values.round(4) if key == "form_conversion" else values.round(2)
        frames.append(out)
    return pd.concat(frames).to_csv(index=False).encode()


def trend_charts(tables, rolling, granularity, events, start, end, colours) -> None:
    st.header("Trend")
    long = pd.concat(
        [t.assign(Channel=c) for c, t in tables.items()], ignore_index=True
    )
    long["Part period"] = long["is_part"].map({True: "Part period", False: "Full period"})
    long["Revenue"] = long["blended_revenue"]
    long["RPE"] = long["rpe"]
    long["Revenue text"] = long["Revenue"].map(fmt.money)
    long["RPE text"] = long["RPE"].map(fmt.rpe)
    long["Enquiries"] = long["blended_enquiries"]
    color = alt.Color("Channel:N", scale=alt.Scale(domain=list(colours), range=list(colours.values())),
                      legend=alt.Legend(orient="top", title=None))
    x = alt.X("period:T", title=None, axis=alt.Axis(format="%d %b", grid=False))
    tooltip = [alt.Tooltip("Channel:N"), alt.Tooltip("label:N", title="Period"),
               alt.Tooltip("Revenue text:N", title="Revenue"), alt.Tooltip("Enquiries:Q", format=","),
               alt.Tooltip("RPE text:N", title="RPE"), alt.Tooltip("Part period:N")]

    visible_events = pd.DataFrame(
        [{"date": pd.Timestamp(e.date), "label": e.label} for e in events if start <= e.date <= end]
    )

    def with_events(chart):
        if visible_events.empty:
            return chart
        rules = alt.Chart(visible_events).mark_rule(strokeDash=[4, 4], color="#888888", strokeWidth=1.5).encode(
            x="date:T", tooltip=[alt.Tooltip("date:T", format="%d %b %Y", title="Date"), alt.Tooltip("label:N", title="Event")]
        )
        return alt.layer(chart, rules)

    shape = alt.Shape("Part period:N", scale=alt.Scale(domain=["Full period", "Part period"], range=["circle", "diamond"]),
                      legend=alt.Legend(orient="top", title=None))

    def series(field: str, title: str, axis_format: str):
        base = alt.Chart(long).encode(x=x, color=color)
        y = alt.Y(f"{field}:Q", title=title, axis=alt.Axis(format=axis_format, gridOpacity=0.3))
        line = base.mark_line(strokeWidth=2).encode(y=y)
        points = base.mark_point(size=70, filled=True, opacity=0.6 if granularity == "Day" else 1).encode(
            y=y, shape=shape, tooltip=tooltip
        )
        if granularity == "Day":
            line = line.encode(opacity=alt.value(0.25))
        return line + points

    st.subheader("Blended revenue")
    st.altair_chart(with_events(series("Revenue", "Revenue (£)", ",.0f")).properties(height=280),
                    width="stretch")

    st.subheader("Blended RPE")
    rpe_chart = series("RPE", "RPE (£)", ",.0f")
    if granularity == "Day":
        roll = pd.concat([r.assign(Channel=c) for c, r in rolling.items()], ignore_index=True)
        roll["Rolling text"] = roll["rolling_rpe"].map(lambda v: fmt.rpe(None if pd.isna(v) else v))
        rpe_chart = rpe_chart + alt.Chart(roll).mark_line(strokeWidth=2.5).encode(
            x="date:T", y="rolling_rpe:Q", color=color,
            tooltip=[alt.Tooltip("Channel:N"), alt.Tooltip("date:T", format="%a %d %b", title="Day"),
                     alt.Tooltip("Rolling text:N", title="7 day rolling RPE")],
        )
        st.caption("Points are daily RPE. Lines are the 7 day rolling RPE, which smooths small daily volumes.")
    st.altair_chart(with_events(rpe_chart).properties(height=280), width="stretch")

    part = long.loc[long["is_part"], "label"].unique()
    notes = []
    if len(part):
        notes.append("Part periods (diamonds): " + ", ".join(part) + ". Compare these with care.")
    if not visible_events.empty:
        notes.append("Dashed lines mark events: " + "; ".join(
            f"{d:%d %b} {l}" for d, l in zip(visible_events["date"], visible_events["label"])))
    for note in notes:
        st.caption(note)


# Page ------------------------------------------------------------------------


def main() -> None:
    settings = load_settings()
    upload_section()
    model = current_model()
    manage_files_section(model)

    st.title("Blended RPE")

    if model.leads.empty:
        st.info("Upload one or more lead exports (enquiries-master-anon-*.csv) in the sidebar to get started. "
                "They're kept in data/exports on this machine, so next time only new exports need uploading.")
        health_panel(model)
        return

    first_ts, last_ts = model.health["coverage"]
    last_day = last_full_day(last_ts)
    start, end, granularity, compare = date_controls(last_day, first_ts.date())
    all_channels = sorted(set(model.leads["landed"]) | set(model.leads["started_in"]))
    channel_options = list(dict.fromkeys(settings.dashboard_channels + all_channels))
    channels = st.sidebar.multiselect("Channels", channel_options, default=settings.dashboard_channels)
    follow_ons = st.sidebar.toggle("Include same-session follow-ons", value=False,
                                   help="Later leads in a session with no cross sell link take the journey of the "
                                        "session's first lead and count as add-ons.")
    selection = filter_controls(model)

    starts_ok = starts_available(selection)
    leads = apply_filters(model.leads_for(follow_ons), selection)
    starts = apply_filters(model.starts, {k: v for k, v in selection.items()
                                          if next(f for f in FILTERS if f.key == k).filters_starts})

    compare_range = comparison_range(start, end, compare)
    compare_label = None
    if compare_range:
        compare_label = f"vs {compare_range[0]:%d %b} to {compare_range[1]:%d %b %Y}"

    summary_bits = [f"{start:%d %b %Y} to {end:%d %b %Y}", f"by {granularity.lower()}"]
    if compare_label:
        summary_bits.append(compare_label)
    if follow_ons:
        summary_bits.append("same-session follow-ons included")
    st.caption(" · ".join(summary_bits))
    filters_text = describe_filters(selection)
    if filters_text:
        st.markdown(f"**Filtered:** {filters_text}")
    else:
        st.caption("No filters applied")

    definitions_panel()
    health_panel(model)

    if not channels:
        st.warning("Pick at least one channel in the sidebar.")
        return

    colours = {c: CHANNEL_COLOURS[i % len(CHANNEL_COLOURS)] for i, c in enumerate(channel_options) if c in channels}
    tables, rolling = {}, {}
    for channel in channels:
        current = summarise(leads, starts, channel, start, end, starts_ok)
        previous = summarise(leads, starts, channel, *compare_range, starts_ok) if compare_range else None
        tables[channel] = breakdown(leads, starts, channel, start, end, granularity, starts_ok)
        if granularity == "Day":
            rolling[channel] = rolling_rpe(leads, channel, start, end)
        detail = routing_detail(leads, channel, start, end)
        channel_card(channel, current, previous, detail, starts_ok, tables[channel])

    st.download_button(
        "Download breakdown (CSV)",
        data=export_breakdown(tables, starts_ok),
        file_name=f"blended-rpe-breakdown-{start:%Y%m%d}-{end:%Y%m%d}-{granularity.lower()}.csv",
        mime="text/csv",
    )

    trend_charts(tables, rolling, granularity, settings.events, start, end, colours)


main()
