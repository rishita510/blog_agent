from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pandas as pd
import streamlit as st

# -----------------------------
# Import your compiled LangGraph app
# -----------------------------
from blog_backend import app


# -----------------------------
# Helpers
# -----------------------------
def safe_slug(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9 _-]+", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s or "blog"


def try_stream(
    graph_app,
    inputs: Dict[str, Any],
) -> Iterator[Tuple[str, Any]]:
    """
    Stream graph progress if available; otherwise invoke.
    Yields:
        ("updates", payload)
        ("values", payload)
        ("final", final_output)
    """

    # Try updates mode
    try:
        for step in graph_app.stream(inputs, stream_mode="updates"):
            yield ("updates", step)

        out = graph_app.invoke(inputs)
        yield ("final", out)
        return

    except Exception:
        pass

    # Try values mode
    try:
        for step in graph_app.stream(inputs, stream_mode="values"):
            yield ("values", step)

        out = graph_app.invoke(inputs)
        yield ("final", out)
        return

    except Exception:
        pass

    # Final fallback
    out = graph_app.invoke(inputs)
    yield ("final", out)


def extract_latest_state(
    current_state: Dict[str, Any],
    step_payload: Any,
) -> Dict[str, Any]:

    if isinstance(step_payload, dict):

        # Example:
        # {"router": {"mode": "research"}}
        if (
            len(step_payload) == 1
            and isinstance(next(iter(step_payload.values())), dict)
        ):
            inner = next(iter(step_payload.values()))
            current_state.update(inner)

        else:
            current_state.update(step_payload)

    return current_state


# -----------------------------
# Past blogs helpers
# -----------------------------
def list_past_blogs() -> List[Path]:
    """
    Returns .md files from the current working directory,
    newest first.
    """

    cwd = Path(".")

    files = [
        p
        for p in cwd.glob("*.md")
        if p.is_file()
    ]

    files.sort(
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    return files


def read_md_file(p: Path) -> str:
    return p.read_text(
        encoding="utf-8",
        errors="replace",
    )


def extract_title_from_md(
    md: str,
    fallback: str,
) -> str:

    """
    Extract the first '# ' heading as the blog title.
    """

    for line in md.splitlines():

        if line.startswith("# "):

            title = line[2:].strip()

            return title or fallback

    return fallback


# -----------------------------
# Streamlit configuration
# -----------------------------
st.set_page_config(
    page_title="LangGraph Blog Writer",
    layout="wide",
)


# -----------------------------
# Main title
# -----------------------------
st.title("Blog Writing Agent")


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("Generate New Blog")

    topic = st.text_area(
        "Topic",
        height=120,
        placeholder="Enter the topic you want to write about...",
    )

    as_of = st.date_input(
        "As-of date",
        value=date.today(),
    )

    run_btn = st.button(
        "🚀 Generate Blog",
        type="primary",
        use_container_width=True,
    )

    # --------------------------------------------------------
    # Past blogs
    # --------------------------------------------------------

    st.divider()

    st.subheader("Past Blogs")

    past_files = list_past_blogs()

    if not past_files:

        st.caption(
            "No saved blogs found (*.md in current folder)."
        )

        selected_md_file = None

    else:

        options: List[str] = []

        file_by_label: Dict[str, Path] = {}

        for p in past_files[:50]:

            try:

                md_text = read_md_file(p)

                title = extract_title_from_md(
                    md_text,
                    p.stem,
                )

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

        selected_md_file = file_by_label.get(
            selected_label
        )

        if st.button(
            "📂 Load Selected Blog",
            use_container_width=True,
        ):

            if selected_md_file:

                md_text = read_md_file(
                    selected_md_file
                )

                # Load previous blog into session state
                st.session_state["last_out"] = {
                    "plan": None,
                    "evidence": [],
                    "final": md_text,
                }

                st.session_state[
                    "topic_prefill"
                ] = extract_title_from_md(
                    md_text,
                    selected_md_file.stem,
                )

                st.rerun()


# ============================================================
# SESSION STATE
# ============================================================

if "last_out" not in st.session_state:

    st.session_state["last_out"] = None


if "logs" not in st.session_state:

    st.session_state["logs"] = []


# ============================================================
# TABS
# ============================================================

tab_plan, tab_evidence, tab_preview, tab_logs = st.tabs(
    [
        "🧩 Plan",
        "🔎 Evidence",
        "📝 Markdown Preview",
        "🧾 Logs",
    ]
)


# ============================================================
# LOGGING
# ============================================================

logs: List[str] = []


def log(msg: str):

    logs.append(msg)


# ============================================================
# GENERATE BLOG
# ============================================================

if run_btn:

    if not topic.strip():

        st.warning(
            "Please enter a topic."
        )

        st.stop()

    # --------------------------------------------------------
    # Inputs matching the new backend
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    status = st.status(
        "Running graph…",
        expanded=True,
    )

    progress_area = st.empty()

    current_state: Dict[str, Any] = {}

    last_node = None

    # --------------------------------------------------------
    # Stream graph
    # --------------------------------------------------------

    for kind, payload in try_stream(
        app,
        inputs,
    ):

        # ====================================================
        # Intermediate updates
        # ====================================================

        if kind in (
            "updates",
            "values",
        ):

            node_name = None

            if (
                isinstance(payload, dict)
                and len(payload) == 1
                and isinstance(
                    next(iter(payload.values())),
                    dict,
                )
            ):

                node_name = next(
                    iter(payload.keys())
                )

            # ------------------------------------------------
            # Show current node
            # ------------------------------------------------

            if (
                node_name
                and node_name != last_node
            ):

                status.write(
                    f"➡️ Node: `{node_name}`"
                )

                last_node = node_name

            # ------------------------------------------------
            # Update current state
            # ------------------------------------------------

            current_state = extract_latest_state(
                current_state,
                payload,
            )

            # ------------------------------------------------
            # Progress summary
            # ------------------------------------------------

            plan = current_state.get(
                "plan"
            )

            tasks_count = None

            if plan:

                if hasattr(
                    plan,
                    "tasks",
                ):

                    tasks_count = len(
                        plan.tasks
                    )

                elif isinstance(
                    plan,
                    dict,
                ):

                    tasks_count = len(
                        plan.get(
                            "tasks",
                            [],
                        )
                    )

            summary = {

                "mode":
                    current_state.get(
                        "mode"
                    ),

                "needs_research":
                    current_state.get(
                        "needs_research"
                    ),

                "queries":
                    (
                        current_state.get(
                            "queries",
                            [],
                        )[:5]
                        if isinstance(
                            current_state.get(
                                "queries"
                            ),
                            list,
                        )
                        else []
                    ),

                "evidence_count":
                    len(
                        current_state.get(
                            "evidence",
                            [],
                        )
                        or []
                    ),

                "tasks":
                    tasks_count,

                "sections_done":
                    len(
                        current_state.get(
                            "sections",
                            [],
                        )
                        or []
                    ),
            }

            progress_area.json(
                summary
            )

            # ------------------------------------------------
            # Save logs
            # ------------------------------------------------

            log(
                f"[{kind}] "
                + json.dumps(
                    payload,
                    default=str,
                )[:1200]
            )

        # ====================================================
        # Final output
        # ====================================================

        elif kind == "final":

            out = payload

            st.session_state[
                "last_out"
            ] = out

            status.update(
                label="✅ Blog generated successfully",
                state="complete",
                expanded=False,
            )

            log(
                "[final] received final state"
            )


# ============================================================
# RENDER LAST RESULT
# ============================================================

out = st.session_state.get(
    "last_out"
)


if out:

    # ========================================================
    # PLAN TAB
    # ========================================================

    with tab_plan:

        st.subheader("Plan")

        plan_obj = out.get(
            "plan"
        )

        if not plan_obj:

            st.info(
                "No plan found in output."
            )

        else:

            # ------------------------------------------------
            # Convert Pydantic model to dictionary
            # ------------------------------------------------

            if hasattr(
                plan_obj,
                "model_dump",
            ):

                plan_dict = (
                    plan_obj.model_dump()
                )

            elif isinstance(
                plan_obj,
                dict,
            ):

                plan_dict = plan_obj

            else:

                plan_dict = json.loads(
                    json.dumps(
                        plan_obj,
                        default=str,
                    )
                )

            # ------------------------------------------------
            # Basic plan information
            # ------------------------------------------------

            st.write(
                "**Title:**",
                plan_dict.get(
                    "blog_title"
                ),
            )

            cols = st.columns(3)

            cols[0].write(
                "**Audience:** "
                + str(
                    plan_dict.get(
                        "audience"
                    )
                )
            )

            cols[1].write(
                "**Tone:** "
                + str(
                    plan_dict.get(
                        "tone"
                    )
                )
            )

            cols[2].write(
                "**Blog kind:** "
                + str(
                    plan_dict.get(
                        "blog_kind",
                        "",
                    )
                )
            )

            # ------------------------------------------------
            # Tasks
            # ------------------------------------------------

            tasks = plan_dict.get(
                "tasks",
                [],
            )

            if tasks:

                df = pd.DataFrame(
                    [
                        {
                            "id":
                                t.get(
                                    "id"
                                ),

                            "title":
                                t.get(
                                    "title"
                                ),

                            "target_words":
                                t.get(
                                    "target_words"
                                ),

                            "requires_research":
                                t.get(
                                    "requires_research"
                                ),

                            "requires_citations":
                                t.get(
                                    "requires_citations"
                                ),

                            "requires_code":
                                t.get(
                                    "requires_code"
                                ),

                            "tags":
                                ", ".join(
                                    t.get(
                                        "tags"
                                    )
                                    or []
                                ),
                        }
                        for t in tasks
                    ]
                ).sort_values(
                    "id"
                )

                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                )

                with st.expander(
                    "Task Details"
                ):

                    st.json(
                        tasks
                    )


    # ========================================================
    # EVIDENCE TAB
    # ========================================================

    with tab_evidence:

        st.subheader(
            "Research Evidence"
        )

        evidence = out.get(
            "evidence"
        ) or []

        if not evidence:

            st.info(
                "No evidence returned "
                "(possibly closed-book mode "
                "or no Tavily results)."
            )

        else:

            rows = []

            for e in evidence:

                if hasattr(
                    e,
                    "model_dump",
                ):

                    e = e.model_dump()

                rows.append(
                    {
                        "title":
                            e.get(
                                "title"
                            ),

                        "published_at":
                            e.get(
                                "published_at"
                            ),

                        "source":
                            e.get(
                                "source"
                            ),

                        "url":
                            e.get(
                                "url"
                            ),
                    }
                )

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )


    # ========================================================
    # MARKDOWN PREVIEW TAB
    # ========================================================

    with tab_preview:

        st.subheader(
            "Markdown Preview"
        )

        final_md = out.get(
            "final"
        ) or ""

        if not final_md:

            st.warning(
                "No final markdown found."
            )

        else:

            # ------------------------------------------------
            # Render markdown directly
            # ------------------------------------------------

            st.markdown(
                final_md,
                unsafe_allow_html=False,
            )

            st.divider()

            # ------------------------------------------------
            # Determine filename
            # ------------------------------------------------

            plan_obj = out.get(
                "plan"
            )

            if hasattr(
                plan_obj,
                "blog_title",
            ):

                blog_title = (
                    plan_obj.blog_title
                )

            elif isinstance(
                plan_obj,
                dict,
            ):

                blog_title = (
                    plan_obj.get(
                        "blog_title",
                        "blog",
                    )
                )

            else:

                blog_title = (
                    extract_title_from_md(
                        final_md,
                        "blog",
                    )
                )

            md_filename = (
                f"{safe_slug(blog_title)}.md"
            )

            # ------------------------------------------------
            # Download Markdown
            # ------------------------------------------------

            st.download_button(
                "⬇️ Download Markdown",
                data=final_md.encode(
                    "utf-8"
                ),
                file_name=md_filename,
                mime="text/markdown",
                use_container_width=True,
            )


    # ========================================================
    # LOGS TAB
    # ========================================================

    with tab_logs:

        st.subheader(
            "Logs"
        )

        if logs:

            st.session_state[
                "logs"
            ].extend(logs)

        log_text = "\n\n".join(
            st.session_state[
                "logs"
            ][-80:]
        )

        st.text_area(
            "Event Log",
            value=log_text,
            height=520,
        )


# ============================================================
# NO BLOG YET
# ============================================================

else:

    st.info(
        "Enter a topic and click "
        "**Generate Blog**."
    )