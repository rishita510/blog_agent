from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple
from uuid import uuid4

import pandas as pd
import streamlit as st
from langgraph.types import Command

from blog_backend import app, latest_verdicts


def safe_slug(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9 _-]+", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s or "blog"


def try_stream(graph_app, graph_input, config) -> Iterator[Tuple[str, Any]]:
    last_values = None
    for mode, chunk in graph_app.stream(
        graph_input, config, stream_mode=["updates", "values"]
    ):
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
            return line[2:].strip() or fallback
    return fallback


def extract_title_from_file(p: Path) -> str:
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("# "):
                return line[2:].strip() or p.stem
    return p.stem


def log(msg: str):
    st.session_state["logs"].append(msg)


def count_tasks(plan) -> Any:
    if plan is None:
        return None
    if hasattr(plan, "tasks"):
        return len(plan.tasks)
    if isinstance(plan, dict):
        return len(plan.get("tasks", []))
    return None


def run_graph(graph_input, config) -> None:
    status = st.status("Running graph…", expanded=True)
    progress_area = st.empty()
    last_node = None
    last_values = None

    try:
        for kind, payload in try_stream(app, graph_input, config):
            if kind == "updates":
                if isinstance(payload, dict) and len(payload) == 1:
                    node_name = next(iter(payload))
                    if node_name != last_node:
                        status.write(f"➡️ Node: `{node_name}`")
                        last_node = node_name
                log("[updates] " + json.dumps(payload, default=str)[:1200])

            elif kind == "values":
                last_values = payload
                queries = payload.get("queries")
                latest = latest_verdicts(payload.get("verdict_log") or [])
                progress_area.json(
                    {
                        "mode": payload.get("mode"),
                        "needs_research": payload.get("needs_research"),
                        "queries": queries[:5] if isinstance(queries, list) else [],
                        "evidence_count": len(payload.get("evidence") or []),
                        "tasks": count_tasks(payload.get("plan")),
                        "sections_done": len(payload.get("sections") or {}),
                        "sections_passing_fact_check": sum(
                            1 for v in latest.values() if v["passed"]
                        ),
                        "revision_rounds": payload.get("rounds", 0),
                    }
                )

    except Exception as e:
        status.update(label="❌ Generation failed", state="error", expanded=True)
        st.exception(e)
        return

    snap = app.get_state(config)
    interrupts = [i for t in snap.tasks for i in getattr(t, "interrupts", ())]

    if interrupts:
        st.session_state["awaiting"] = interrupts[0].value["plan"]
        status.update(
            label="⏸️ Outline ready: review it below",
            state="complete",
            expanded=False,
        )
        log("[interrupt] waiting for outline approval")
        return

    st.session_state["awaiting"] = None
    st.session_state["last_out"] = last_values
    status.update(
        label="✅ Blog generated successfully",
        state="complete",
        expanded=False,
    )
    log("[final] received final state")


def render_plan_editor(config) -> None:
    plan = st.session_state["awaiting"]
    k = st.session_state["thread_id"]

    st.subheader("✋ Review the outline")
    st.caption(
        "Edit anything, untick sections you don't want, then approve to start writing."
    )

    title = st.text_input("Blog title", value=plan["blog_title"], key=f"{k}_title")

    edited = []

    for t in sorted(plan["tasks"], key=lambda x: x["id"]):
        tid = t["id"]
        with st.expander(f"{tid}. {t['title']}"):
            include = st.checkbox("Include this section", value=True, key=f"{k}_inc_{tid}")
            new_title = st.text_input("Title", value=t["title"], key=f"{k}_t_{tid}")
            goal = st.text_area("Goal", value=t["goal"], height=70, key=f"{k}_g_{tid}")
            bullets = st.text_area(
                "Bullets (one per line)",
                value="\n".join(t["bullets"]),
                height=140,
                key=f"{k}_b_{tid}",
            )
            words = st.number_input(
                "Target words",
                min_value=80,
                max_value=800,
                value=int(t["target_words"]),
                step=10,
                key=f"{k}_w_{tid}",
            )

        if include:
            edited.append(
                {
                    **t,
                    "title": new_title.strip() or t["title"],
                    "goal": goal.strip() or t["goal"],
                    "bullets": [b.strip() for b in bullets.splitlines() if b.strip()]
                    or t["bullets"],
                    "target_words": int(words),
                }
            )

    c1, c2 = st.columns(2)
    approve = c1.button("✅ Approve & write", type="primary", width="stretch")
    cancel = c2.button("✖ Cancel", width="stretch")

    if cancel:
        st.session_state["awaiting"] = None
        st.rerun()

    if approve:
        if not edited:
            st.warning("Keep at least one section.")
            return

        new_plan = {**plan, "blog_title": title.strip() or plan["blog_title"], "tasks": edited}
        st.session_state["awaiting"] = None
        return new_plan

    return None


st.set_page_config(page_title="LangGraph Blog Writer", layout="wide")

st.title("Blog Writing Agent")

for key, default in (
    ("last_out", None),
    ("logs", []),
    ("awaiting", None),
    ("thread_id", uuid4().hex),
):
    if key not in st.session_state:
        st.session_state[key] = default

with st.sidebar:
    st.header("Generate New Blog")

    topic = st.text_area(
        "Topic",
        height=120,
        placeholder="Enter the topic you want to write about...",
    )

    as_of = st.date_input("As-of date", value=date.today())

    review_outline = st.checkbox("Review outline before writing", value=True)

    run_btn = st.button("🚀 Generate Blog", type="primary", width="stretch")

    st.divider()

    st.subheader("Past Blogs")

    past_files = list_past_blogs()

    if not past_files:
        st.caption("No saved blogs found (*.md in current folder).")
    else:
        options: List[str] = []
        file_by_label: Dict[str, Path] = {}

        for p in past_files[:50]:
            try:
                title = extract_title_from_file(p)
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
                st.session_state["awaiting"] = None
                st.rerun()


def make_config(resume: bool = False) -> dict:
    return {
        "configurable": {
            "thread_id": st.session_state["thread_id"],
            "auto_approve": (not review_outline) and not resume,
        },
        "recursion_limit": 60,
    }


if run_btn:
    if not topic.strip():
        st.warning("Please enter a topic.")
    else:
        st.session_state["thread_id"] = uuid4().hex
        st.session_state["awaiting"] = None

        inputs: Dict[str, Any] = {
            "topic": topic.strip(),
            "mode": "",
            "needs_research": False,
            "queries": [],
            "evidence": [],
            "plan": None,
            "as_of": as_of.isoformat(),
            "recency_days": 7,
            "sections": {},
            "verdict_log": [],
            "pending": [],
            "rounds": 0,
            "cover_image": "",
            "report": {},
            "final": "",
        }

        run_graph(inputs, make_config())

editor_slot = st.empty()

if st.session_state["awaiting"]:
    with editor_slot.container():
        approved_plan = render_plan_editor(make_config())

    if approved_plan:
        editor_slot.empty()
        run_graph(Command(resume={"plan": approved_plan}), make_config(resume=True))

tab_plan, tab_evidence, tab_check, tab_preview, tab_logs = st.tabs(
    ["🧩 Plan", "🔎 Evidence", "✅ Fact-check", "📝 Markdown Preview", "🧾 Logs"]
)

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

    with tab_check:
        st.subheader("Fact-check")

        report = out.get("report") or {}
        verdict_log = out.get("verdict_log") or []

        if not report:
            st.info("No fact-check data (this blog was loaded from a saved file).")
        else:
            n = report.get("sections", 0)
            m = st.columns(4)
            m[0].metric("Sections", n)
            m[1].metric("Passed first try", f"{report.get('first_try_pass', 0)}/{n}")
            m[2].metric("Passed after revision", f"{report.get('final_pass', 0)}/{n}")
            m[3].metric("Revision rounds", report.get("revision_rounds", 0))

            m2 = st.columns(2)
            m2[0].metric("Invalid URLs (first draft)", report.get("invalid_urls_first", 0))
            m2[1].metric("Invalid URLs (final)", report.get("invalid_urls_final", 0))

            if verdict_log:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "section": e["id"],
                                "title": e["title"],
                                "round": e["round"],
                                "passed": e["passed"],
                                "issues": " | ".join(e["issues"]),
                            }
                            for e in sorted(
                                verdict_log, key=lambda x: (x["id"], x["round"])
                            )
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )

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

        st.text_area(
            "Event Log",
            value="\n\n".join(st.session_state["logs"][-80:]),
            height=520,
        )

elif not st.session_state["awaiting"]:
    st.info("Enter a topic and click **Generate Blog**.")