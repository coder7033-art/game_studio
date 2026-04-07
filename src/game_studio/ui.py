from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from game_studio.crew import GameStudio
from game_studio.tools import ValidateDatabaseConnectionTool

load_dotenv()

# Silence CrewAI event-pairing mismatch warnings at module level.
# These occur in multi-kickoff UIs and are harmless; silencing them keeps
# the console clean for real errors.
try:
    from crewai.events import event_context as _ec
    from crewai.events.event_context import EventContextConfig, MismatchBehavior

    _ec._event_context_config.set(  # type: ignore[attr-defined]
        EventContextConfig(
            mismatch_behavior=MismatchBehavior.SILENT,
            empty_pop_behavior=MismatchBehavior.SILENT,
        )
    )
except Exception:  # noqa: BLE001
    pass

DEFAULT_SCHEMA_FILE = "output/schema_metadata.json"
DEFAULT_DATA_FILE = "output/table_data_dump.json"
CONFIG_FILE = Path(".db_studio_config.json")

_CONFIG_KEYS = [
    "connection_uri",
    "database_type",
    "schema_output_file",
    "data_output_file",
    "max_rows_per_table",
    "report_format",
]

# ── agent pipeline stage markers ──────────────────────────────────────────────

_AGENT_STAGES: list[dict[str, str]] = [
    {
        "id": "analyze",
        "icon": "🔍",
        "title": "Data Analyst",
        "active_text": "Reading schema & executing queries",
        "done_text": "Analysis complete",
        "marker": "output/data_analysis.md",
    },
    {
        "id": "synthesize",
        "icon": "✍️",
        "title": "Response Synthesizer",
        "active_text": "Composing a clear answer",
        "done_text": "Response ready",
        "marker": "output/final_response.md",
    },
    {
        "id": "visualize",
        "icon": "📊",
        "title": "Visual Reporter",
        "active_text": "Building charts & KPI cards",
        "done_text": "Visuals generated",
        "marker": "output/visual_report.md",
    },
]

_STAGE_MARKER_FILES = [s["marker"] for s in _AGENT_STAGES]

DASHBOARD_TEMPLATES: list[dict] = [
    {
        "id": "revenue_overview",
        "title": "Revenue & Subscriptions Overview",
        "description": "Monitor total revenue, transaction trends, and key financial KPIs from your database.",
        "category": "Revenue",
        "accent": "#6366f1",
        "bg": "#eef2ff",
        "hints": ["Total Revenue", "Total Transactions", "Avg Order Value", "New Customers"],
        "prompt": (
            "Create a revenue and subscriptions overview dashboard. Include: "
            "total revenue, total transactions or orders, average order value, "
            "top revenue-generating categories or segments, revenue by category distribution, "
            "and revenue trend over time. Design exactly 10 metric KPIs and 6 charts (bar, line, pie)."
        ),
    },
    {
        "id": "churn_retention",
        "title": "Churn & Retention Overview",
        "description": "Analyze customer retention rates, churn patterns, and subscription lifecycle metrics.",
        "category": "Revenue",
        "accent": "#e11d48",
        "bg": "#1c1c2e",
        "hints": ["Active Customers", "Inactive Customers", "Repeat Rate", "Avg Lifetime"],
        "prompt": (
            "Build a churn and retention dashboard. Include: "
            "active vs inactive customer counts, repeat customer rate, customer retention rate, "
            "customers at risk of churn, top retained segments, and retention trend over time. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "saas_key_metrics",
        "title": "SaaS Key Revenue Metrics",
        "description": "Elevate decision-making with core business metrics: revenue, users, value, and growth.",
        "category": "Revenue",
        "accent": "#f97316",
        "bg": "#fff7ed",
        "hints": ["Total Revenue", "Total Customers", "Avg Revenue per User", "Top Category"],
        "prompt": (
            "Design a SaaS key metrics dashboard. Include: "
            "total revenue, total customers, average revenue per user, customer lifetime value, "
            "top product or service categories, net revenue trends, and user growth patterns. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "growth_dashboard",
        "title": "Growth Metrics Dashboard",
        "description": "Track new customer acquisition, growth rates, and period-over-period performance.",
        "category": "Growth",
        "accent": "#16a34a",
        "bg": "#f0fdf4",
        "hints": ["New Customers", "Growth Rate", "Returning Customers", "Top Segment"],
        "prompt": (
            "Generate a growth metrics dashboard. Include: "
            "new customers this period, growth rate percentage, top acquiring segments, "
            "cumulative growth, new vs returning ratio, and weekly or monthly growth trend. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "product_performance",
        "title": "Product Performance Overview",
        "description": "Analyze top products, category distributions, inventory, and sales velocity.",
        "category": "Product",
        "accent": "#0ea5e9",
        "bg": "#0f172a",
        "hints": ["Total Products", "Top Product", "Category Count", "Avg Rating"],
        "prompt": (
            "Build a product performance dashboard. Include: "
            "total products or items, top products by sales volume, category distribution, "
            "inventory or stock count, average rating or return rate, and sales velocity trend. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "customer_insights",
        "title": "Customer Insights Overview",
        "description": "Deep-dive into segmentation, lifetime value, geographic spread, and behavior patterns.",
        "category": "Customers",
        "accent": "#a855f7",
        "bg": "#fdf4ff",
        "hints": ["Total Customers", "Top Customer", "Countries", "Avg Value"],
        "prompt": (
            "Design a customer insights dashboard. Include: "
            "total customers, top customers by value, geographic distribution, customer segments, "
            "average customer value, customer activity frequency, and customer growth trend. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "sales_performance",
        "title": "Sales Performance Overview",
        "description": "Comprehensive sales analytics with top performers, pipeline, and revenue trends.",
        "category": "Sales",
        "accent": "#ea580c",
        "bg": "#fff7ed",
        "hints": ["Total Sales", "Transactions", "Best Seller", "Top Region"],
        "prompt": (
            "Create a sales performance dashboard. Include: "
            "total sales amount, number of transactions, best performing item or category, "
            "sales by region or geography, average transaction size, and daily or monthly sales trend. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "operational_health",
        "title": "Operational Health Dashboard",
        "description": "Monitor throughput, efficiency metrics, peak periods, and process performance.",
        "category": "Operations",
        "accent": "#0284c7",
        "bg": "#f0f9ff",
        "hints": ["Transaction Volume", "Peak Day", "Efficiency Rate", "Total Records"],
        "prompt": (
            "Build an operational health dashboard. Include: "
            "total transaction volume, processing rate, peak activity hours or days, "
            "throughput by period, status distribution, and operational performance trend. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "geographic_distribution",
        "title": "Geographic Distribution",
        "description": "Visualize data across regions, countries, and cities with geographic breakdowns.",
        "category": "Analytics",
        "accent": "#059669",
        "bg": "#ecfdf5",
        "hints": ["Top Country", "Top City", "Region Count", "Geographic Spread"],
        "prompt": (
            "Create a geographic distribution dashboard. Include: "
            "top countries by activity or revenue, top cities, regional share breakdown, "
            "geographic concentration metrics, underperforming regions, and regional trend over time. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "time_series_analysis",
        "title": "Time Series & Trend Analysis",
        "description": "Explore trends, seasonality, peak periods, and temporal patterns in your data.",
        "category": "Analytics",
        "accent": "#ca8a04",
        "bg": "#fefce8",
        "hints": ["Daily Avg", "Peak Month", "Weekly Volume", "Total Records"],
        "prompt": (
            "Design a time series and trend analysis dashboard. Include: "
            "daily activity volume, weekly patterns, monthly totals, peak period identification, "
            "year-over-year or period-over-period comparison, and trend direction analysis. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "engagement_overview",
        "title": "Engagement Overview",
        "description": "Measure engagement depth, activity frequency, interaction patterns, and retention.",
        "category": "Engagement",
        "accent": "#db2777",
        "bg": "#fdf2f8",
        "hints": ["Active Entities", "Engagement Rate", "Top Segment", "Total Interactions"],
        "prompt": (
            "Build an engagement overview dashboard. Include: "
            "total active entities, engagement frequency, top engaged segments, "
            "high-value interaction count, engagement depth score, and engagement trend over time. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
    {
        "id": "executive_summary",
        "title": "Executive Summary Dashboard",
        "description": "High-level overview with the most critical KPIs for executive decision-makers.",
        "category": "Executive",
        "accent": "#818cf8",
        "bg": "#1e1b4b",
        "hints": ["Total Revenue", "Total Customers", "Transactions", "Top Category"],
        "prompt": (
            "Generate a comprehensive executive summary dashboard with the top business KPIs. Include: "
            "total revenue, total customers, total transactions, top performing category, "
            "growth rate, and overall performance summary with the most important trends. "
            "Design exactly 10 metric KPIs and 6 charts."
        ),
    },
]


# ── persistence ───────────────────────────────────────────────────────────────

def _save_config() -> None:
    data = {k: st.session_state.get(k) for k in _CONFIG_KEYS}
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_config() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def _ensure_state() -> None:
    saved = _load_config()
    st.session_state.setdefault("connection_uri", saved.get("connection_uri", ""))
    st.session_state.setdefault("database_type", saved.get("database_type", "postgresql"))
    st.session_state.setdefault("schema_output_file", saved.get("schema_output_file", DEFAULT_SCHEMA_FILE))
    st.session_state.setdefault("data_output_file", saved.get("data_output_file", DEFAULT_DATA_FILE))
    st.session_state.setdefault("max_rows_per_table", saved.get("max_rows_per_table", 1000))
    st.session_state.setdefault("report_format", saved.get("report_format", "report"))
    st.session_state.setdefault("chat_history", [])


# ── helpers ───────────────────────────────────────────────────────────────────

def _mask_uri(uri: str) -> str:
    if "://" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    if "@" not in rest:
        return f"{scheme}://{rest}"
    creds, host = rest.split("@", 1)
    user = creds.split(":", 1)[0] if ":" in creds else creds
    return f"{scheme}://{user}:***@{host}"


def _safe_json(value: str) -> dict[str, Any]:
    try:
        return json.loads(value)
    except Exception:  # noqa: BLE001
        return {"raw": value}


def _strip_final_answer_prefix(text: str) -> str:
    """Remove 'Final Answer:' / '# Final Answer' prefixes the model sometimes adds."""
    import re
    text = text.strip()
    text = re.sub(r"^#+\s*final\s+answer[:\s]*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^final\s+answer[:\s]*", "", text, flags=re.IGNORECASE)
    return text.strip()


def _load_json_file(path: str) -> Any:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return None


def _extract_json_block(text: str, key: str) -> Any:
    """Extract a JSON code block from markdown text and return value at key."""
    import re  # noqa: PLC0415
    matches = re.findall(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    for raw in matches:
        try:
            obj = json.loads(raw)
            if key in obj:
                return obj[key]
        except Exception:  # noqa: BLE001
            continue
    return None


# ── agents ────────────────────────────────────────────────────────────────────

def _run_connection_test(database_type: str, connection_uri: str) -> dict[str, Any]:
    tool = ValidateDatabaseConnectionTool()
    return _safe_json(tool.run(database_type=database_type, connection_uri=connection_uri))


def _reset_crewai_event_context() -> None:
    """Reset CrewAI internal event context to avoid stack accumulation across many kickoffs."""
    try:
        from crewai.events import event_context as _ec  # noqa: PLC0415
        from crewai.events.event_context import (  # noqa: PLC0415
            EventContextConfig,
            MismatchBehavior,
        )

        # Silence pairing-mismatch warnings that are harmless in multi-kickoff UIs
        silent_cfg = EventContextConfig(
            mismatch_behavior=MismatchBehavior.SILENT,
            empty_pop_behavior=MismatchBehavior.SILENT,
        )
        _ec._event_context_config.set(silent_cfg)  # type: ignore[attr-defined]

        # Clear accumulated event stack
        _ec._event_id_stack.set(())  # type: ignore[attr-defined]
        _ec.reset_last_event_id()
        _ec.set_triggering_event_id(None)
    except Exception:  # noqa: BLE001
        pass


def _build_crew_inputs(user_question: str) -> dict[str, Any]:
    """Build the inputs dict from session state (must be called from main thread)."""
    return {
        "database_type": st.session_state.database_type,
        "connection_uri": st.session_state.connection_uri,
        "schema_output_file": st.session_state.schema_output_file,
        "data_output_file": st.session_state.data_output_file,
        "max_rows_per_table": int(st.session_state.max_rows_per_table),
        "user_question": user_question,
        "report_format": st.session_state.report_format,
    }


def _clean_stage_outputs() -> None:
    """Remove all output files so polling detects fresh creations."""
    charts_dir = Path("output/charts")
    if charts_dir.exists():
        for f in charts_dir.glob("*.json"):
            f.unlink()
    for f in [*_STAGE_MARKER_FILES, "output/metrics.json"]:
        p = Path(f)
        if p.exists():
            p.unlink()


def _run_agents_core(inputs: dict[str, Any]) -> dict[str, Any]:
    """Execute the analysis crew. Thread-safe — no Streamlit calls."""
    studio = GameStudio()

    _reset_crewai_event_context()
    result = studio.crew().kickoff(inputs=inputs)
    _reset_crewai_event_context()

    tasks = [
        {
            "name": getattr(t, "name", "task"),
            "agent": getattr(t, "agent", "agent"),
            "summary": getattr(t, "summary", ""),
            "raw": getattr(t, "raw", ""),
            "description": getattr(t, "description", ""),
        }
        for t in (getattr(result, "tasks_output", []) or [])
    ]

    final_response_path = Path("output/final_response.md")
    if final_response_path.exists():
        raw_answer = final_response_path.read_text(encoding="utf-8")
    else:
        raw_answer = getattr(result, "raw", str(result))
    answer = _strip_final_answer_prefix(raw_answer)

    charts = _load_all_charts()
    metrics = _load_metrics()

    return {
        "final_answer": answer,
        "tasks": tasks,
        "metrics": metrics,
        "charts": charts,
    }


def _run_agents(user_question: str) -> dict[str, Any]:
    """High-level agent runner (uses Streamlit UI for setup). Used by dashboard."""
    inputs = _build_crew_inputs(user_question)

    schema_exists = Path(st.session_state.schema_output_file).exists()
    data_exists = Path(st.session_state.data_output_file).exists()

    if not (schema_exists and data_exists):
        with st.status("Initializing database context (one-time setup)…"):
            st.write("Extracting schema and data…")
            _reset_crewai_event_context()
            GameStudio().setup_crew().kickoff(inputs=inputs)
            st.write("Done.")

    _clean_stage_outputs()
    return _run_agents_core(inputs)



def _run_dashboard_planner(user_question: str) -> list[dict]:
    """Plan the dashboard widgets using the architect crew."""
    inputs = {
        "schema_output_file": st.session_state.schema_output_file,
        "user_question": user_question,
    }
    studio = GameStudio()
    result = studio.dashboard_planner_crew().kickoff(inputs=inputs)
    raw = getattr(result, "raw", "").strip()
    import re
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return []


# ── suggestions ───────────────────────────────────────────────────────────────

def _run_suggestions(user_question: str, schema_file: str, chat_history: list) -> list[str]:
    """Run suggestions crew and return 4 suggested questions."""
    try:
        history_summary = "; ".join(h["question"] for h in chat_history[-5:]) or "none"
        inputs = {
            "schema_output_file": schema_file,
            "user_question": user_question,
            "chat_history": history_summary,
        }
        studio = GameStudio()
        _reset_crewai_event_context()
        result = studio.suggestions_crew().kickoff(inputs=inputs)
        _reset_crewai_event_context()
        raw = getattr(result, "raw", str(result)).strip()
        import re
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            suggestions = json.loads(match.group())
            if isinstance(suggestions, list):
                return [str(s) for s in suggestions[:4]]
    except Exception:  # noqa: BLE001
        pass
    return []


def _render_suggestions() -> None:
    """Render suggestion buttons; clicking one queues it as the next question."""
    suggestions = st.session_state.get("suggestions", [])
    if not suggestions:
        return
    st.markdown("**Suggested questions:**")
    cols = st.columns(len(suggestions))
    for i, (col, suggestion) in enumerate(zip(cols, suggestions)):
        if col.button(suggestion, key=f"sugg_{i}_{suggestion[:20]}", width="stretch"):
            st.session_state["pending_question"] = suggestion
            st.session_state["suggestions"] = []
            st.rerun()


# ── sidebar ───────────────────────────────────────────────────────────────────

def _render_sidebar() -> None:
    with st.sidebar:
        st.title("DB Agents Studio")
        st.caption("Connect your database to start chatting.")
        st.divider()

        st.subheader("Connection")
        st.text_input(
            "Connection URI",
            key="connection_uri",
            type="password",
            placeholder="postgresql://user:pass@host:5432/db",
        )
        st.selectbox(
            "Database Type",
            ["postgresql", "mysql", "sqlite", "mssql", "other"],
            key="database_type",
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Test", width="stretch"):
                if not st.session_state.connection_uri.strip():
                    st.error("Enter a URI first.")
                else:
                    with st.spinner("Testing…"):
                        r = _run_connection_test(
                            st.session_state.database_type,
                            st.session_state.connection_uri.strip(),
                        )
                    if r.get("status") == "success":
                        st.success("Connected!")
                    else:
                        st.error("Failed.")
                        st.json(r)
        with c2:
            if st.button("Save", width="stretch"):
                _save_config()
                st.success("Saved.")

        if st.session_state.connection_uri.strip():
            st.caption(f"`{_mask_uri(st.session_state.connection_uri.strip())}`")

        st.divider()
        with st.expander("Advanced Settings", expanded=False):
            st.text_input("Schema Output File", key="schema_output_file")
            st.text_input("Data Output File", key="data_output_file")
            st.number_input("Max Rows per Table", min_value=0, step=100, key="max_rows_per_table")
            st.selectbox("Output Format", ["report", "data_answer"], key="report_format")

        st.divider()
        if st.button("Clear Chat", width="stretch"):
            st.session_state.chat_history = []
            st.rerun()


# ── templates ─────────────────────────────────────────────────────────────────

def _template_thumb_html(template: dict) -> str:
    """Return an HTML/SVG mini thumbnail for a template card."""
    import hashlib  # noqa: PLC0415
    accent = template["accent"]
    bg = template["bg"]
    dark_bg = bg.startswith(("#0", "#1", "#2"))
    text_col = "#f1f5f9" if dark_bg else "#1e293b"
    muted_col = "#94a3b8" if dark_bg else "#64748b"
    border_col = "rgba(255,255,255,0.12)" if dark_bg else "#e2e8f0"

    seed = hashlib.md5(template["id"].encode()).digest()
    heights = [20 + (seed[i] % 36) for i in range(8)]
    bars_svg = "".join(
        f'<rect x="{8 + i * 15}" y="{62 - h}" width="11" height="{h}" rx="2.5" fill="{accent}" opacity="0.82"/>'
        for i, h in enumerate(heights)
    )
    line_ys = [14 + (seed[(i + 2) % 16] % 32) for i in range(8)]
    line_path = "M " + " L ".join(f"{8 + i * 15 + 5},{line_ys[i]}" for i in range(8))

    m1 = f"${seed[0] * 38 + 150:,}"
    m2 = str(seed[1] * 4 + 60)
    hints = template.get("hints", ["Metric A", "Metric B"])
    lbl1 = hints[0].upper()[:13]
    lbl2 = hints[1].upper()[:13] if len(hints) > 1 else "METRIC B"

    return (
        f'<div style="background:{bg};border-radius:10px;padding:12px 12px 6px;height:150px;'
        f'overflow:hidden;position:relative;border:1px solid {border_col}">'
        f'<div style="position:absolute;top:8px;left:8px;background:#f97316;color:#fff;'
        f'font-size:7px;font-weight:900;padding:2px 5px;border-radius:3px;letter-spacing:0.1em">TEMPLATE</div>'
        f'<div style="display:flex;gap:14px;margin-top:26px;margin-bottom:3px;align-items:flex-end">'
        f'<div>'
        f'<div style="font-size:7px;color:{muted_col};font-weight:700;letter-spacing:0.07em">{lbl1}</div>'
        f'<div style="font-size:14px;font-weight:900;color:{accent};line-height:1.1">{m1}</div>'
        f'</div>'
        f'<div>'
        f'<div style="font-size:7px;color:{muted_col};font-weight:700;letter-spacing:0.07em">{lbl2}</div>'
        f'<div style="font-size:11px;font-weight:700;color:{text_col};line-height:1.1">{m2}</div>'
        f'</div>'
        f'</div>'
        f'<svg width="134" height="66" viewBox="0 0 134 66" xmlns="http://www.w3.org/2000/svg" style="display:block">'
        f'{bars_svg}'
        f'<path d="{line_path}" fill="none" stroke="{accent}" stroke-width="1.8" opacity="0.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
        f'</div>'
    )


@st.dialog("Template Preview", width="large")
def _show_template_preview(template: dict) -> None:
    """Modal dialog showing template layout and included widget types."""
    accent = template["accent"]
    hints = template.get("hints", [])

    st.markdown(
        f'<div style="display:flex;align-items:center;gap:14px;margin-bottom:12px">'
        f'<div style="width:44px;height:44px;background:{accent}22;border-radius:12px;'
        f'display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0">'
        f'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="{accent}" stroke-width="2.5">'
        f'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>'
        f'<rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>'
        f'</svg></div>'
        f'<div><div style="font-weight:800;font-size:1.15rem">{template["title"]}</div>'
        f'<div style="font-size:0.82rem;color:var(--text-muted);margin-top:2px">'
        f'{template["category"]} Template</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.write(template["description"])
    st.divider()

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("**KPI Metric Cards — 10 cards**")
        for h in hints[:4]:
            st.markdown(f"- {h}")
        st.markdown("- *+ more adapted to your schema*")
    with col_r:
        st.markdown("**Charts & Visualizations — 6 charts**")
        st.markdown("- Bar chart breakdowns")
        st.markdown("- Line trend analysis")
        st.markdown("- Pie distribution chart")
        st.markdown("- *Adapted to your real data*")

    st.divider()
    st.info("All values are computed live from your connected database.")

    if st.button("Use This Template", type="primary", width="stretch"):
        st.session_state["pending_template"] = template
        st.session_state.pop("current_dashboard", None)
        st.rerun()


def _execute_dashboard_template(template: dict) -> None:
    """Plan and execute a full dashboard from a template prompt."""
    with st.status(f"Building '{template['title']}'…") as status:
        st.write("Planning dashboard structure from your schema…")
        widgets = _run_dashboard_planner(template["prompt"])
        if not widgets:
            status.update(label="Failed to plan dashboard structure.", state="error")
            return
        st.session_state.current_dashboard = {
            "question": template["title"],
            "widgets": widgets,
            "results": {},
            "source": "template",
            "template_id": template["id"],
        }
        status.update(
            label=f"Structure ready: {len(widgets)} components. Computing data…",
            state="running",
        )
        failed_widgets = 0
        for idx, widget in enumerate(widgets):
            st.write(f"Computing: {widget['title']}…")
            try:
                res = _run_agents(widget["question"])
            except Exception as exc:  # noqa: BLE001
                failed_widgets += 1
                res = {
                    "final_answer": f"Widget failed: {exc}",
                    "tasks": [],
                    "metrics": [],
                    "charts": [],
                }
            st.session_state.current_dashboard["results"][idx] = res
        if failed_widgets:
            status.update(
                label=f"Dashboard partially ready ({len(widgets) - failed_widgets}/{len(widgets)} succeeded).",
                state="error",
            )
        else:
            status.update(label="Dashboard ready!", state="complete")


def _render_current_dashboard() -> None:
    """Render the active session dashboard (metric cards + charts)."""
    db = st.session_state.current_dashboard
    widgets = db["widgets"]

    metrics_w = [(i, w) for i, w in enumerate(widgets) if w["type"] == "metric"]
    charts_w  = [(i, w) for i, w in enumerate(widgets) if w["type"] != "metric"]

    # ── KPI cards ──
    if metrics_w:
        st.markdown("### Key Performance Indicators")
        cols_per_row = 5
        for row_start in range(0, len(metrics_w), cols_per_row):
            m_cols = st.columns(cols_per_row)
            for col_idx in range(cols_per_row):
                pos = row_start + col_idx
                if pos < len(metrics_w):
                    idx, w = metrics_w[pos]
                    r = db["results"].get(idx)
                    with m_cols[col_idx]:
                        with st.container(border=True):
                            st.markdown(
                                f'<div class="metric-card-title">{w["title"]}</div>',
                                unsafe_allow_html=True,
                            )
                            if r and r.get("metrics"):
                                mv = r["metrics"][0]
                                ck = mv.get("color") if mv.get("color") in _COLOR_MAP else "blue"
                                ck = ck or "blue"
                                vc = _COLOR_MAP[ck]["value"]
                                sub = mv.get("sub_value") or ""
                                dl = mv.get("delta") or ""
                                spark = _sparkline_svg(mv.get("sparkline") or [], color=_COLOR_MAP[ck]["spark"])
                                delta_up = mv.get("delta_up") if mv.get("delta_up") is not None else True
                                delta_html = ""
                                if dl:
                                    arrow = "▲" if delta_up else "▼"
                                    dc = "#16a34a" if delta_up else "#dc2626"
                                    delta_html = f'<span style="color:{dc};font-size:0.75rem;font-weight:700">{arrow} {dl}</span>'
                                st.markdown(
                                    f'<div class="metric-card-value" style="color:{vc}">{mv["value"]}</div>'
                                    + (f'<div class="metric-card-label">{sub}</div>' if sub else "")
                                    + (f'<div style="margin-top:4px">{spark}</div>' if spark else "")
                                    + (f'<div style="margin-top:2px">{delta_html}</div>' if delta_html else ""),
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.caption("Waiting for data…")
        st.divider()

    # ── Charts ──
    if charts_w:
        st.markdown("### Analytical Breakdowns")
        for row_start in range(0, len(charts_w), 2):
            c_cols = st.columns(2)
            for col_idx in range(2):
                pos = row_start + col_idx
                if pos < len(charts_w):
                    idx, w = charts_w[pos]
                    r = db["results"].get(idx)
                    with c_cols[col_idx]:
                        with st.container(border=True):
                            st.markdown(
                                f'<div class="chart-card-header">'
                                f'<span class="chart-card-title">{w["title"]}</span>'
                                f'<span class="chart-card-badge">{w["type"].upper()}</span>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                            if r:
                                if r.get("charts"):
                                    for ci, cd in enumerate(r["charts"]):
                                        _render_chart(cd, chart_idx=hash(f"db_c_{idx}_{ci}"))
                                with st.expander("Analysis Summary"):
                                    _render_answer(r["final_answer"])
                            else:
                                st.info("Loading analysis…")


def _render_template_gallery() -> None:
    """Render the searchable template gallery grid."""
    fc1, fc2 = st.columns([3, 1])
    with fc1:
        search = st.text_input(
            "", placeholder="Search templates…",
            label_visibility="collapsed", key="tmpl_search",
        )
    with fc2:
        cats = ["All"] + sorted({t["category"] for t in DASHBOARD_TEMPLATES})
        cat = st.selectbox("", cats, label_visibility="collapsed", key="tmpl_cat")

    filtered = [
        t for t in DASHBOARD_TEMPLATES
        if (not search or search.lower() in t["title"].lower() or search.lower() in t["description"].lower())
        and (cat == "All" or t["category"] == cat)
    ]

    if not filtered:
        st.info("No templates match your search.")
        return

    COLS = 4
    for row_start in range(0, len(filtered), COLS):
        cols = st.columns(COLS)
        for ci in range(COLS):
            pos = row_start + ci
            if pos < len(filtered):
                tmpl = filtered[pos]
                with cols[ci]:
                    st.markdown(
                        f'<div class="tmpl-thumb-wrap">'
                        f'{_template_thumb_html(tmpl)}'
                        f'<div class="tmpl-thumb-overlay">'
                        f'<div class="tmpl-overlay-hint">Click Preview to explore</div>'
                        f'</div></div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"**{tmpl['title']}**")
                    st.caption(
                        tmpl["description"][:88] + ("…" if len(tmpl["description"]) > 88 else "")
                    )
                    st.markdown(
                        f'<span class="tmpl-badge" style="background:{tmpl["accent"]}18;'
                        f'color:{tmpl["accent"]}">{tmpl["category"]}</span>',
                        unsafe_allow_html=True,
                    )
                    b1, b2 = st.columns(2)
                    with b1:
                        if st.button("Preview", key=f"prev_{tmpl['id']}", width="stretch"):
                            _show_template_preview(tmpl)
                    with b2:
                        if st.button(
                            "Use Template", key=f"use_{tmpl['id']}",
                            type="primary", width="stretch",
                        ):
                            st.session_state["pending_template"] = tmpl
                            st.session_state.pop("current_dashboard", None)
                            st.rerun()
        st.write("")


# ── dashboard ───────────────────────────────────────────────────────────────

def _render_dashboard_generator() -> None:
    st.markdown("""
        <div class="dashboard-header">
            <div>
                <h1 class="dashboard-title">Performance Overview</h1>
                <p class="dashboard-subtitle">A comprehensive snapshot of key metrics and analytical trends.</p>
            </div>
            <div style="display: flex; gap: 8px;">
                <div class="header-btn">+ New</div>
                <div class="header-btn">Share</div>
                <div class="header-btn">Present</div>
                <div class="header-btn">Refresh</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    if not st.session_state.connection_uri.strip():
        st.info("Enter your database connection in the sidebar first.")
        return

    # Execute pending template (triggered by "Use Template" or dialog button)
    if "pending_template" in st.session_state and "current_dashboard" not in st.session_state:
        _execute_dashboard_template(st.session_state.pop("pending_template"))
        st.rerun()

    # Show active dashboard (shared between AI and template flows)
    if "current_dashboard" in st.session_state:
        _render_current_dashboard()
        if st.button("Generate New Dashboard", width="stretch"):
            del st.session_state.current_dashboard
            st.rerun()
        return

    # Generation options
    tab_ai, tab_tmpl = st.tabs(["Generate with AI", "Template Gallery"])

    with tab_ai:
        prompt = st.text_input(
            "What would you like to analyze today?",
            placeholder="e.g. Overview of annual financial health and customer acquisition",
            label_visibility="collapsed",
        )
        if st.button("Generate Dashboard", type="primary", disabled=not prompt):
            with st.status("Designing your dashboard…") as status:
                widgets = _run_dashboard_planner(prompt)
                if not widgets:
                    status.update(label="Failed to plan dashboard structure.", state="error")
                else:
                    st.session_state.current_dashboard = {
                        "question": prompt,
                        "widgets": widgets,
                        "results": {},
                        "source": "ai",
                    }
                    status.update(
                        label=f"Design complete: {len(widgets)} components. Executing…",
                        state="running",
                    )
                    failed_widgets = 0
                    for idx, widget in enumerate(widgets):
                        st.write(f"Computing: {widget['title']}…")
                        try:
                            res = _run_agents(widget["question"])
                        except Exception as exc:  # noqa: BLE001
                            failed_widgets += 1
                            res = {
                                "final_answer": f"Widget failed: {exc}",
                                "tasks": [],
                                "metrics": [],
                                "charts": [],
                            }
                        st.session_state.current_dashboard["results"][idx] = res
                    if failed_widgets:
                        status.update(
                            label=f"Dashboard partially ready ({len(widgets) - failed_widgets}/{len(widgets)} succeeded).",
                            state="error",
                        )
                    else:
                        status.update(label="Dashboard ready!", state="complete")
            st.rerun()

    with tab_tmpl:
        _render_template_gallery()


# ── result rendering ──────────────────────────────────────────────────────────

def _load_metrics() -> list[dict]:
    """Load KPI metrics saved by the agent via SaveMetricsTool."""
    p = Path("output/metrics.json")
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:  # noqa: BLE001
            pass
    return []


def _sparkline_svg(values: list[float], width: int = 80, height: int = 28, color: str = "#3b82f6") -> str:
    """Render a tiny inline SVG sparkline from a list of floats."""
    if len(values) < 2:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn or 1
    step = width / (len(values) - 1)
    pts = [(i * step, height - ((v - mn) / rng) * (height - 4) - 2) for i, v in enumerate(values)]
    path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="display:block">'
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def _load_all_charts() -> list[dict]:
    """Load all saved chart JSON files from output/charts/, sorted by filename."""
    charts_dir = Path("output/charts")
    if not charts_dir.exists():
        return []
    charts = []
    for f in sorted(charts_dir.glob("*.json")):
        try:
            charts.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return charts


_COLOR_MAP = {
    "green":  {"value": "#16a34a", "spark": "#16a34a", "delta": "#16a34a"},
    "red":    {"value": "#dc2626", "spark": "#dc2626", "delta": "#dc2626"},
    "orange": {"value": "#d97706", "spark": "#d97706", "delta": "#d97706"},
    "blue":   {"value": "#2563eb", "spark": "#3b82f6", "delta": "#2563eb"},
}


def _render_metrics(metrics: list[dict]) -> None:
    """Render rich KPI metric cards generated by the agent."""
    if not metrics:
        return
    cols_per_row = min(len(metrics), 4)
    cols = st.columns(cols_per_row)
    for i, m in enumerate(metrics):
        label = m.get("label", "")
        value = m.get("value", "")
        if not (label and value):
            continue

        color_key = m.get("color") if m.get("color") in _COLOR_MAP else "blue"
        colors = _COLOR_MAP[color_key or "blue"]

        sub_value = m.get("sub_value") or ""
        target = m.get("target") or ""
        delta = m.get("delta") or ""
        delta_up = m.get("delta_up") if m.get("delta_up") is not None else True
        sparkline_data = m.get("sparkline") or []

        # Delta arrow + color
        if delta:
            arrow = "▲" if delta_up else "▼"
            delta_color = "#16a34a" if delta_up else "#dc2626"
            delta_html = (
                f'<span style="color:{delta_color};font-size:0.78rem;font-weight:700">'
                f'{arrow} {delta}</span>'
            )
        else:
            delta_html = ""

        # Footer row: sub_value left, target right
        footer_parts = []
        if sub_value:
            footer_parts.append(f'<span style="font-size:0.8rem;color:var(--text-muted)">{sub_value}</span>')
        if target:
            footer_parts.append(
                f'<span style="font-size:0.75rem;color:var(--text-muted);margin-left:auto">'
                f'&#127919; {target}</span>'
            )
        footer_html = (
            f'<div style="display:flex;align-items:center;gap:6px;margin-top:4px">'
            f'{"".join(footer_parts)}</div>'
            if footer_parts else ""
        )

        # Sparkline
        spark_html = _sparkline_svg(sparkline_data, color=colors["spark"]) if sparkline_data else ""

        with cols[i % cols_per_row]:
            with st.container(border=True):
                st.markdown(
                    f'<div class="metric-card-title">{label}</div>'
                    f'<div class="metric-card-value" style="color:{colors["value"]}">{value}</div>'
                    f'{footer_html}'
                    f'<div style="margin-top:6px">{spark_html}</div>'
                    f'<div style="margin-top:4px">{delta_html}</div>',
                    unsafe_allow_html=True,
                )


def _render_chart(chart_data: dict, chart_idx: int = 0) -> None:
    """Render an interactive Plotly chart with filters from saved JSON data."""
    import plotly.graph_objects as go  # noqa: PLC0415
    from plotly.subplots import make_subplots

    labels_orig: list[str] = chart_data.get("labels", [])
    series_orig: list[dict] = chart_data.get("series", [])
    
    # Backwards compatibility for old charts with single 'values' list
    if not series_orig and "values" in chart_data and chart_data["values"]:
        series_orig = [{"name": chart_data.get("ylabel") or "Value", "values": chart_data["values"], "type": chart_data.get("chart_type", "bar")}]

    chart_type: str = chart_data.get("chart_type", "bar")
    title: str = chart_data.get("title", "")
    xlabel: str = chart_data.get("xlabel", "")
    ylabel: str = chart_data.get("ylabel", "")

    if not labels_orig or not series_orig:
        st.warning("Chart data is empty.")
        return

    # ── filters ──
    with st.expander("Chart Filters", expanded=False):
        fcol1, fcol2, fcol3 = st.columns([3, 1, 1])
        with fcol1:
            selected = st.multiselect(
                "Show categories",
                options=labels_orig,
                default=labels_orig,
                key=f"chart_filter_{chart_idx}_{title[:20]}",
            )
        with fcol2:
            sort_order = st.radio(
                "Sort",
                ["None", "Asc", "Desc"],
                index=0,
                key=f"chart_sort_{chart_idx}_{title[:20]}",
                horizontal=True,
            )
        with fcol3:
            chart_type_ui = st.radio(
                "Global Type",
                ["auto", "bar", "pie", "line"],
                index=0,
                key=f"chart_type_{chart_idx}_{title[:20]}",
                horizontal=True,
            )

    # Apply filters
    filtered_indices = [i for i, l in enumerate(labels_orig) if l in selected]
    if not filtered_indices:
        st.info("No categories selected.")
        return

    # Sort based on the first series values
    sort_vals = [series_orig[0]["values"][i] for i in filtered_indices]
    combined = list(zip(filtered_indices, sort_vals))
    
    if sort_order == "Asc":
        combined.sort(key=lambda x: x[1])
    elif sort_order == "Desc":
        combined.sort(key=lambda x: x[1], reverse=True)
        
    final_indices = [x[0] for x in combined]
    labels_final = [labels_orig[i] for i in final_indices]

    colors = ["#3498db", "#e74c3c", "#2ecc71", "#f1c40f", "#9b59b6", "#e67e22"]
    force_type = chart_type_ui if chart_type_ui != "auto" else chart_type

    if force_type == "pie":
        # Pie charts only show a single series
        s = series_orig[0]
        vals = [s["values"][i] for i in final_indices]
        fig = go.Figure()
        fig.add_trace(go.Pie(labels=labels_final, values=vals, hole=0.3, textinfo="label+percent+value", name=s["name"]))
        fig.update_layout(showlegend=True)
    else:
        # Combo, line, or bar (multi-series support)
        fig = make_subplots(specs=[[{"secondary_y": len(series_orig) > 1}]])
        
        for s_idx, s in enumerate(series_orig):
            s_name = s.get("name") or f"Series {s_idx+1}"
            s_type = force_type if chart_type_ui != "auto" else s.get("type", "bar")
            s_vals = [s["values"][i] for i in final_indices]
            color = colors[s_idx % len(colors)]
            text_labels = [f"{v:,.0f}" if isinstance(v, (int, float)) and v == int(v) else f"{v:,.2f}" for v in s_vals]
            
            is_secondary = (s_idx > 0)
            
            if s_type == "line":
                fig.add_trace(
                    go.Scatter(
                        x=labels_final, y=s_vals, mode="lines+markers+text", name=s_name,
                        text=text_labels, textposition="top center",
                        line=dict(color=color, width=3), marker=dict(size=8, color=color)
                    ),
                    secondary_y=is_secondary,
                )
            else:
                fig.add_trace(
                    go.Bar(
                        x=labels_final, y=s_vals, name=s_name,
                        text=text_labels, textposition="outside",
                        marker_color=color, opacity=0.85
                    ),
                    secondary_y=is_secondary,
                )
        
        fig.update_layout(
            xaxis_title=xlabel or "Category",
            yaxis_title=ylabel or series_orig[0].get("name", "Value"),
            showlegend=len(series_orig) > 1,
            barmode="group"
        )
        if len(series_orig) > 1:
            fig.update_yaxes(title_text=series_orig[1].get("name", "Secondary Value"), secondary_y=True)

    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        height=450,
        margin=dict(t=60, b=40, l=40, r=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    st.plotly_chart(fig, width="stretch")


def _parse_md_table(text: str) -> "pd.DataFrame | None":
    """Parse a markdown table string into a DataFrame, cleaning markdown bold markers."""
    import re
    import pandas as pd

    lines = [l for l in text.splitlines() if l.strip()]
    lines = [l for l in lines if not re.fullmatch(r'[\|:\- ]+', l.strip())]
    if len(lines) < 2:
        return None
    rows = [[re.sub(r'\*+', '', c).strip() for c in l.strip().strip('|').split('|')] for l in lines]
    headers = rows[0]
    data = rows[1:]
    if not data:
        return None
    df = pd.DataFrame(data, columns=headers)
    # Try to coerce numeric columns
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col].str.replace(',', ''))
        except Exception:
            pass
    return df


def _try_pivot(df: "pd.DataFrame") -> "pd.DataFrame | None":
    """Return a pivot table if the DataFrame looks like a multi-group flat table, else None."""
    import pandas as pd

    if len(df.columns) != 3:
        return None
    cat_cols = [c for c in df.columns if df[c].dtype == object]
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if len(cat_cols) != 2 or len(num_cols) != 1:
        return None
    # Pivot-worthy if first cat col has repeated values
    if df[cat_cols[0]].nunique() >= len(df) * 0.8:
        return None
    try:
        pivot = df.pivot_table(index=cat_cols[0], columns=cat_cols[1], values=num_cols[0], aggfunc='sum')
        pivot.columns.name = None
        return pivot.reset_index()
    except Exception:
        return None


def _render_answer(text: str) -> None:
    """Render answer text, extracting markdown tables as interactive dataframes or pivot tables."""
    import re

    table_re = re.compile(
        r'(\|[^\n]+\|\n\|[-| :]+\|\n(?:\|[^\n]+\|\n?)+)',
        re.MULTILINE,
    )
    parts = table_re.split(text)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if table_re.fullmatch(part + "\n") or table_re.fullmatch(part):
            df = _parse_md_table(part)
            if df is not None:
                pivot = _try_pivot(df)
                if pivot is not None:
                    st.dataframe(pivot, width="stretch", hide_index=True)
                else:
                    st.dataframe(df, width="stretch", hide_index=True)
            else:
                st.markdown(part)
        else:
            st.markdown(part)


# ── chat ──────────────────────────────────────────────────────────────────────

def _process_question(prompt: str) -> None:
    """Run agents for a question with a live animated pipeline progress indicator."""
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        # ── ensure database context (runs in main thread — uses st.status) ──
        inputs = _build_crew_inputs(prompt)
        schema_exists = Path(st.session_state.schema_output_file).exists()
        data_exists = Path(st.session_state.data_output_file).exists()

        if not (schema_exists and data_exists):
            with st.status("Initializing database context (one-time setup)…"):
                st.write("Extracting schema and data…")
                _reset_crewai_event_context()
                GameStudio().setup_crew().kickoff(inputs=inputs)
                st.write("Done.")

        # ── clean output files so we can poll for stage completion ──
        _clean_stage_outputs()

        # ── agent reasoning (collapsible, lines accumulate) ──
        result_holder: dict[str, Any] = {}
        error_holder: dict[str, Exception] = {}

        def _work() -> None:
            try:
                result_holder["data"] = _run_agents_core(inputs)
            except Exception as exc:  # noqa: BLE001
                error_holder["exc"] = exc

        thread = threading.Thread(target=_work, daemon=True)

        with st.status("Agent reasoning…", expanded=True) as status:
            # Show first stage IMMEDIATELY
            st.write(f"{_AGENT_STAGES[0]['icon']}  {_AGENT_STAGES[0]['active_text']}")
            thread.start()

            completed: set[str] = set()
            shown: set[int] = {0}

            while thread.is_alive():
                for stage in _AGENT_STAGES:
                    if stage["id"] not in completed and Path(stage["marker"]).exists():
                        completed.add(stage["id"])
                # When a stage finishes, show the NEXT stage starting
                next_idx = len(completed)
                if next_idx < len(_AGENT_STAGES) and next_idx not in shown:
                    shown.add(next_idx)
                    s = _AGENT_STAGES[next_idx]
                    st.write(f"{s['icon']}  {s['active_text']}")
                time.sleep(0.4)

            if "exc" in error_holder:
                status.update(label="Agent reasoning — error", state="error")
                st.error(f"Error: {error_holder['exc']}")
            else:
                status.update(label="Agent reasoning", state="complete", expanded=False)

        if "exc" in error_holder:
            return

        run_result = result_holder["data"]

        answer = run_result["final_answer"]
        metrics = run_result.get("metrics", [])
        charts = run_result.get("charts", [])
        tasks = run_result["tasks"]

        _render_answer(answer)
        _render_metrics(metrics)
        for ci, cd in enumerate(charts):
            _render_chart(cd, chart_idx=ci)



        st.session_state.chat_history.append({
            "question": prompt,
            "answer": answer,
            "metrics": metrics,
            "charts": charts,
            "tasks": tasks,
        })

    # Generate suggestions after answer
    with st.spinner("Generating follow-up suggestions…"):
        st.session_state["suggestions"] = _run_suggestions(
            prompt,
            st.session_state.schema_output_file,
            st.session_state.chat_history,
        )
    st.rerun()


def _render_chat() -> None:
    if not st.session_state.connection_uri.strip():
        st.info("Enter your database connection in the sidebar to start chatting.")
        return

    # Render existing history
    for item in st.session_state.chat_history:
        with st.chat_message("user"):
            st.markdown(item["question"])
        with st.chat_message("assistant"):
            _render_answer(item["answer"])
            _render_metrics(item.get("metrics", []))
            for ci, cd in enumerate(item.get("charts", [])):
                _render_chart(cd, chart_idx=hash(item["question"]) % 10000 + ci)


    # Handle question from suggestion button click
    if "pending_question" in st.session_state:
        pending = st.session_state.pop("pending_question")
        _process_question(pending)
        return

    # Suggestion buttons above input
    _render_suggestions()

    # New message from chat input
    prompt = st.chat_input("Ask a question about your database…")
    if prompt:
        _process_question(prompt)


# ── entry points ──────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv()
    st.set_page_config(page_title="DB Agents Studio", page_icon="🧠", layout="wide")
    _ensure_state()
    _render_sidebar()
    _render_chart_style()
    
    with st.sidebar:
        st.divider()
        page = st.radio("Navigation", ["Chat Studio", "Dashboard Generator", "Reports"], index=0)
        st.divider()

    if page == "Chat Studio":
        _render_chat()
    elif page == "Dashboard Generator":
        _render_dashboard_generator()
    else:
        _render_reports_page()


def _render_reports_page() -> None:
    if "report_creation_state" not in st.session_state:
        st.session_state.report_creation_state = "initial"

    if st.session_state.report_creation_state == "editor_blank":
        _render_blank_editor()
        return

    st.markdown('<h1 class="dashboard-title">Reports</h1>', unsafe_allow_html=True)
    st.markdown('<p class="dashboard-subtitle">Manage and schedule automated data reports.</p>', unsafe_allow_html=True)
    st.markdown("<br><br>", unsafe_allow_html=True)

    # Injecting CSS to style the specific buttons we use for the cards
    st.markdown("""
    <style>
    /* Default initial primary button */
    div[data-testid="stMain"] button[data-testid="baseButton-primary"] {
        border: 2px dashed var(--accent-color) !important;
        background-color: transparent !important;
        color: var(--accent-color) !important;
        height: 280px !important;
        border-radius: 12px !important;
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: center !important;
        font-weight: 500 !important;
        transition: all 0.2s ease !important;
        box-shadow: none !important;
    }
    div[data-testid="stMain"] button[data-testid="baseButton-primary"]:hover {
        background-color: var(--accent-bg) !important;
        border-color: var(--accent-color) !important;
        color: var(--accent-color) !important;
    }
    div[data-testid="stMain"] button[data-testid="baseButton-primary"] p {
        font-size: 1.15rem !important;
        color: var(--accent-color) !important;
        margin: 0 !important;
    }
    /* Add the huge plus icon using ::before pseudo-element on the paragraph */
    div[data-testid="stMain"] button[data-testid="baseButton-primary"] p::before {
        content: '+';
        display: block;
        font-size: 4rem;
        font-weight: 300;
        margin-bottom: 2px;
        line-height: 1;
    }

    /* Choosing state wrapper */
    div[data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] {
        border: 2px dashed var(--accent-color) !important;
        border-radius: 12px !important;
        padding: 0 !important;
        height: 280px !important;
        background-color: transparent !important;
        overflow: hidden;
    }

    /* Tertiary buttons inside the choosing wrapper */
    div[data-testid="stMain"] button[data-testid="baseButton-tertiary"] {
        border: none !important;
        background: transparent !important;
        color: var(--accent-color) !important;
        height: 140px !important; /* Half of 280px */
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: center !important;
        transition: background 0.2s ease !important;
        box-shadow: none !important;
        margin: 0 !important;
        border-radius: 0 !important;
        padding: 0 !important;
    }
    div[data-testid="stMain"] button[data-testid="baseButton-tertiary"]:hover {
        background-color: var(--accent-bg) !important;
    }
    div[data-testid="stMain"] button[data-testid="baseButton-tertiary"] p {
        font-size: 1.05rem !important;
        margin: 0 !important;
        color: var(--accent-color) !important;
    }
    /* Style Material Icons generated by Streamlit 1.37+ */
    div[data-testid="stMain"] button[data-testid="baseButton-tertiary"] span.material-symbols-rounded {
        font-size: 2.5rem !important;
        margin-bottom: 6px !important;
        display: block !important;
        margin-right: 0 !important;
        color: var(--accent-color) !important;
    }

    /* Styling the divider between the sub-buttons */
    div[data-testid="stVerticalBlockBorderWrapper"] hr {
        margin: 0 15% !important;
        border-color: var(--accent-color) !important;
        opacity: 0.3 !important;
    }
    
    /* Remove gaps between elements in the wrapper */
    div[data-testid="stVerticalBlockBorderWrapper"] > div > div {
        gap: 0 !important;
    }
    </style>
    """, unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.session_state.report_creation_state == "initial":
            if st.button("Create New Report", type="primary", use_container_width=True):
                st.session_state.report_creation_state = "choosing"
                st.rerun()
        elif st.session_state.report_creation_state == "choosing":
            with st.container(border=True):
                if st.button("Use Template", type="tertiary", icon=":material/folder:", use_container_width=True):
                    st.info("Template feature coming soon.")
                st.divider()
                if st.button("Start Blank", type="tertiary", icon=":material/design_services:", use_container_width=True):
                    st.session_state.report_creation_state = "editor_blank"
                    st.rerun()


def _render_blank_editor() -> None:
    # Inject CSS for the full screen editor takeover
    st.markdown("""
    <style>
    /* Take over full screen */
    [data-testid="stSidebar"] { display: none !important; }
    [data-testid="stHeader"] { display: none !important; }
    .main .block-container { 
        padding: 0 !important; 
        max-width: 100% !important; 
    }
    
    /* Background for the editor workspace */
    [data-testid="stAppViewContainer"] { 
        background: #ced4da !important; 
    }
    
    /* Top Toolbar */
    .editor-topbar {
        background: #ffffff;
        padding: 12px 24px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-bottom: 1px solid #dee2e6;
        height: 60px;
    }
    .editor-topbar-left {
        font-size: 1.25rem;
        font-weight: 500;
        color: #212529;
    }
    .editor-topbar-right {
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .editor-btn {
        background: transparent;
        border: 1px solid #dee2e6;
        padding: 6px 12px;
        border-radius: 6px;
        font-size: 0.85rem;
        font-weight: 500;
        color: #495057;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .editor-btn.primary {
        background: var(--accent-color);
        color: white;
        border-color: var(--accent-color);
    }
    .editor-tabs {
        display: flex;
        background: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 6px;
        overflow: hidden;
    }
    .editor-tab {
        padding: 6px 16px;
        font-size: 0.85rem;
        color: #495057;
        cursor: pointer;
    }
    .editor-tab.active {
        background: var(--accent-bg);
        color: var(--accent-color);
        font-weight: 600;
    }
    .auto-saved {
        font-size: 0.8rem;
        color: #adb5bd;
        display: flex;
        align-items: center;
        gap: 4px;
        margin-right: 8px;
    }
    
    /* Paper Workspace */
    .editor-workspace {
        display: flex;
        justify-content: center;
        padding: 40px;
        height: calc(100vh - 60px);
        overflow-y: auto;
    }
    .editor-paper {
        background: #ffffff;
        width: 100%;
        max-width: 850px;
        min-height: 1100px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
        padding: 80px;
    }
    .paper-title {
        font-size: 2.2rem;
        color: #adb5bd;
        margin: 0 0 24px 0;
        font-weight: 500;
    }
    .paper-placeholder {
        font-size: 1.1rem;
        color: #adb5bd;
    }
    
    /* Floating Streamlit Close Button Hack */
    .close-btn-wrapper {
        position: absolute;
        top: 10px;
        right: 16px;
        z-index: 9999;
    }
    .close-btn-wrapper button {
        background: transparent !important;
        border: none !important;
        font-size: 1.2rem !important;
        padding: 0 !important;
        color: #495057 !important;
        width: 32px !important;
        height: 32px !important;
    }
    .close-btn-wrapper button:hover {
        background: #f8f9fa !important;
        color: #212529 !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    html_ui = """
    <div class="editor-topbar">
        <div class="editor-topbar-left">
            New report title
        </div>
        <div class="editor-topbar-right" style="padding-right: 40px;">
            <div class="editor-tabs">
                <div class="editor-tab active"><span class="material-symbols-rounded" style="font-size:14px; vertical-align:middle;">article</span> Page</div>
                <div class="editor-tab"><span class="material-symbols-rounded" style="font-size:14px; vertical-align:middle;">width_normal</span> Slides</div>
            </div>
            <div class="auto-saved"><span class="material-symbols-rounded" style="font-size:14px; vertical-align:middle;">check_circle</span> Auto saved</div>
            <div class="editor-btn primary"><span class="material-symbols-rounded" style="font-size:16px; vertical-align:middle;">play_arrow</span> Present</div>
            <div class="editor-btn"><span class="material-symbols-rounded" style="font-size:16px; vertical-align:middle;">share</span> Share</div>
            <div class="editor-btn"><span class="material-symbols-rounded" style="font-size:16px; color:#ea4335; vertical-align:middle;">picture_as_pdf</span> PDF</div>
            <div class="editor-btn">... More</div>
        </div>
    </div>
    
    <div class="editor-workspace">
        <div class="editor-paper">
            <h1 class="paper-title">New report title</h1>
            <p class="paper-placeholder">Write something, or click '+' for additional options</p>
        </div>
    </div>
    """
    st.markdown(html_ui, unsafe_allow_html=True)
    
    st.markdown('<div class="close-btn-wrapper">', unsafe_allow_html=True)
    if st.button("✕", key="close_editor_btn"):
        st.session_state.report_creation_state = "initial"
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


def _render_chart_style() -> None:
    """Inject adaptive CSS variables and styles."""
    st.markdown(
        """
        <style>
        :root {
            --bg-color: #ffffff;
            --text-main: #1a1a1a;
            --text-muted: #666666;
            --card-bg: #f8f9fa;
            --card-border: #e9ecef;
            --accent-color: #0d6efd;
            --accent-bg: #e7f1ff;
        }

        @media (prefers-color-scheme: dark) {
            :root {
                --bg-color: #0d0d0d;
                --text-main: #f8f9fa;
                --text-muted: #888888;
                --card-bg: #1a1a1a;
                --card-border: #333333;
                --accent-color: #89b4fa;
                --accent-bg: #262626;
            }
        }

        [data-testid="stMetric"] {
            background: transparent !important;
            border: none !important;
            padding: 0 !important;
        }
        [data-testid="stMetricValue"] {
            font-size: 1.8rem !important;
            font-weight: 700;
            color: var(--text-main) !important;
        }
        [data-testid="stMetricLabel"] {
            font-size: 0.75rem !important;
            color: var(--text-muted) !important;
            text-transform: uppercase;
        }

        /* Dashboard Header */
        .dashboard-header {
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            margin-bottom: 32px;
        }
        .dashboard-title {
            margin: 0; 
            font-size: 2.2rem; 
            font-weight: 800; 
            color: var(--text-main);
        }
        .dashboard-subtitle {
            margin: 4px 0 0; 
            color: var(--text-muted); 
            font-size: 1rem;
        }

        /* Header Buttons */
        .header-btn {
            background: var(--card-bg);
            color: var(--text-main);
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            border: 1px solid var(--card-border);
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .header-btn:hover {
            border-color: var(--accent-color);
            color: var(--accent-color);
        }

        /* Metric Cards */
        .metric-card-title {
            font-size: 0.7rem;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }
        .metric-card-value {
            font-size: 1.9rem;
            font-weight: 800;
            line-height: 1.1;
            margin-bottom: 2px;
            word-break: break-word;
        }
        .metric-card-label {
            font-size: 0.7rem;
            color: var(--text-muted);
        }

        /* Chart Cards */
        .chart-card-header {
            display: flex; 
            justify-content: space-between; 
            align-items: baseline; 
            margin-bottom: 24px;
        }
        .chart-card-title {
            font-size: 1.25rem; 
            font-weight: 700; 
            color: var(--text-main);
        }
        .chart-card-badge {
            font-size: 0.65rem; 
            color: var(--accent-color); 
            font-weight: 800; 
            background: var(--accent-bg); 
            padding: 4px 10px; 
            border-radius: 6px;
            letter-spacing: 0.05em;
        }

        /* Template Gallery */
        .tmpl-thumb-wrap {
            position: relative;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 8px;
            cursor: pointer;
            transition: transform 0.15s ease, box-shadow 0.15s ease;
        }
        .tmpl-thumb-wrap:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(0,0,0,0.14);
        }
        .tmpl-thumb-overlay {
            position: absolute;
            inset: 0;
            background: rgba(0,0,0,0.58);
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transition: opacity 0.2s ease;
            border-radius: 10px;
        }
        .tmpl-thumb-wrap:hover .tmpl-thumb-overlay {
            opacity: 1;
        }
        .tmpl-overlay-hint {
            color: #fff;
            font-size: 0.78rem;
            font-weight: 700;
            background: rgba(255,255,255,0.16);
            padding: 7px 14px;
            border-radius: 6px;
            letter-spacing: 0.02em;
            backdrop-filter: blur(4px);
        }
        .tmpl-badge {
            display: inline-block;
            font-size: 0.6rem;
            font-weight: 800;
            padding: 2px 8px;
            border-radius: 20px;
            letter-spacing: 0.07em;
            text-transform: uppercase;
            margin-bottom: 6px;
        }

        /* Global Theme Sync */
        .stApp {
            background-color: var(--bg-color);
            color: var(--text-main);
        }
        [data-testid="stHeader"] {
            background-color: var(--bg-color);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def launch() -> None:
    script_path = Path(__file__).resolve()
    env = os.environ.copy()
    env.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    env.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    subprocess.run(
        [
            "streamlit", "run", str(script_path),
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        check=True,
        env=env,
    )


if __name__ == "__main__":
    main()
