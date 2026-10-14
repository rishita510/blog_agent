from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pandas as pd
import streamlit as st

from blog_backend import app


def safe_slug(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9 _-]+", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s or "blog"


def try_stream(graph_app, inputs: Dict[str, Any]) -> Iterator[Tuple[str, Any]]:
    last_values = None
    for mode, chunk in graph_app.stream(inputs, stream_mode=["updates", "values"]):
        if mode == "values":
            last_values = chunk
        yield (mode, chunk)
    yield ("final", last_values)


def list_past_blogs() -> List[Path]:
    files = [p for p in Path(".").glob("*.md") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def read_md_file(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def extract_title_from_md(md: str, fallback: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            return title or fallback
    return fallback


st.set_page_config(page_title="LangGraph Blog Writer", layout="wide")

st.title("Blog Writing Agent")

if "last_out" not in st.session_state:
    st.session_state["last_out"] = None

if "logs" not in st.session_state:
    st.session_state["logs"] = []

with st.sidebar:
    st.header("Generate New Blog")

    topic = st.text_area(
        "Topic",
        height=120,
        placeholder="Enter the topic you want to write about...",
    )

    as_of = st.date_input("As-of date", value=date.today())

    run_btn = st.button("🚀 Generate Blog", type="primary", width="stretch")

    st.divider()

    st.subheader("Past Blogs")

    past_files = list_past_blogs()

    if not past_files:
        st.caption("No saved blogs found (*.md in current folder).")
        selected_md_file = None
    else:
        options: List[str] = []
        file_by_label: Dict[str, Path] = {}

        for p in past_files[:50]:
            try:
                title = extract_title_from_md(read_md_file(p), p.stem)
            except Exception:
                title = p.stem

            label = f"{title} · {p.name}"
            options.append(label)
            file_by_label[label] = p

        selected_label = st.radio(
            "Select a blog to load",
            options=options,
            index=0,
            label_visibility="collapsed",
        )

        selected_md_file = file_by_label.get(selected_label)

        if st.button("📂 Load Selected Blog", width="stretch"):
            if selected_md_file:
                st.session_state["last_out"] = {
                    "plan": None,
                    "evidence": [],
                    "final": read_md_file(selected_md_file),
                }
                st.rerun()

tab_plan, tab_evidence, tab_preview, tab_logs = st.tabs(
    ["🧩 Plan", "🔎 Evidence", "📝 Markdown Preview", "🧾 Logs"]
)

logs: List[str] = []


def log(msg: str):
    logs.append(msg)


if run_btn:
    if not topic.strip():
        st.warning("Please enter a topic.")
        st.stop()

    inputs: Dict[str, Any] = {
        "topic": topic.strip(),
        "mode": "",
        "needs_research": False,
        "queries": [],
        "evidence": [],
        "plan": None,
        "as_of": as_of.isoformat(),
        "recency_days": 7,
        "sections": [],
        "final": "",
    }

    status = st.status("Running graph…", expanded=True)
    progress_area = st.empty()
    last_node = None

    try:
        for kind, payload in try_stream(app, inputs):
            if kind == "updates":
                if isinstance(payload, dict) and len(payload) == 1:
                    node_name = next(iter(payload))
                    if node_name != last_node:
                        status.write(f"➡️ Node: `{node_name}`")
                        last_node = node_name
                log("[updates] " + json.dumps(payload, default=str)[:1200])

            elif kind == "values":
                plan = payload.get("plan")
                queries = payload.get("queries")
                progress_area.json(
                    {
                        "mode": payload.get("mode"),
                        "needs_research": payload.get("needs_research"),
                        "queries": queries[:5] if isinstance(queries, list) else [],
                        "evidence_count": len(payload.get("evidence") or []),
                        "tasks": len(plan.tasks) if plan else None,
                        "sections_done": len(payload.get("sections") or []),
                    }
                )

            elif kind == "final":
                st.session_state["last_out"] = payload
                status.update(
                    label="✅ Blog generated successfully",
                    state="complete",
                    expanded=False,
                )
                log("[final] received final state")

    except Exception as e:
        status.update(label="❌ Generation failed", state="error", expanded=True)
        st.exception(e)
        st.stop()

out = st.session_state.get("last_out")

if out:
    with tab_plan:
        st.subheader("Plan")

        plan_obj = out.get("plan")

        if not plan_obj:
            st.info("No plan found in output.")
        else:
            if hasattr(plan_obj, "model_dump"):
                plan_dict = plan_obj.model_dump()
            elif isinstance(plan_obj, dict):
                plan_dict = plan_obj
            else:
                plan_dict = json.loads(json.dumps(plan_obj, default=str))

            st.write("**Title:**", plan_dict.get("blog_title"))

            cols = st.columns(3)
            cols[0].write("**Audience:** " + str(plan_dict.get("audience")))
            cols[1].write("**Tone:** " + str(plan_dict.get("tone")))
            cols[2].write("**Blog kind:** " + str(plan_dict.get("blog_kind", "")))

            tasks = plan_dict.get("tasks", [])

            if tasks:
                df = pd.DataFrame(
                    [
                        {
                            "id": t.get("id"),
                            "title": t.get("title"),
                            "target_words": t.get("target_words"),
                            "requires_research": t.get("requires_research"),
                            "requires_citations": t.get("requires_citations"),
                            "requires_code": t.get("requires_code"),
                            "tags": ", ".join(t.get("tags") or []),
                        }
                        for t in tasks
                    ]
                ).sort_values("id")

                st.dataframe(df, width="stretch", hide_index=True)

                with st.expander("Task Details"):
                    st.json(tasks)

    with tab_evidence:
        st.subheader("Research Evidence")

        evidence = out.get("evidence") or []

        if not evidence:
            st.info(
                "No evidence returned (possibly closed-book mode or no Tavily results)."
            )
        else:
            rows = []

            for e in evidence:
                if hasattr(e, "model_dump"):
                    e = e.model_dump()

                rows.append(
                    {
                        "title": e.get("title"),
                        "published_at": e.get("published_at"),
                        "source": e.get("source"),
                        "url": e.get("url"),
                    }
                )

            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    with tab_preview:
        st.subheader("Markdown Preview")

        final_md = out.get("final") or ""

        if not final_md:
            st.warning("No final markdown found.")
        else:
            st.markdown(final_md, unsafe_allow_html=False)

            st.divider()

            plan_obj = out.get("plan")

            if hasattr(plan_obj, "blog_title"):
                blog_title = plan_obj.blog_title
            elif isinstance(plan_obj, dict):
                blog_title = plan_obj.get("blog_title", "blog")
            else:
                blog_title = extract_title_from_md(final_md, "blog")

            st.download_button(
                "⬇️ Download Markdown",
                data=final_md.encode("utf-8"),
                file_name=f"{safe_slug(blog_title)}.md",
                mime="text/markdown",
                width="stretch",
            )

    with tab_logs:
        st.subheader("Logs")

        if logs:
            st.session_state["logs"].extend(logs)

        st.text_area(
            "Event Log",
            value="\n\n".join(st.session_state["logs"][-80:]),
            height=520,
        )

else:
    st.info("Enter a topic and click **Generate Blog**.")