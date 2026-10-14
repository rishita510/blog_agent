from __future__ import annotations

import operator
import os
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict, List, Optional, Literal, Annotated

from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from dotenv import load_dotenv

load_dotenv()


# ============================================================
# 1) Schemas
# ============================================================

class Task(BaseModel):
    id: int
    title: str

    goal: str = Field(
        ...,
        description="One sentence describing what the reader should do/understand."
    )

    bullets: List[str] = Field(
        ...,
        min_length=3,
        max_length=6
    )

    target_words: int = Field(
        ...,
        description="Target words (120–550)."
    )

    tags: List[str] = Field(default_factory=list)

    requires_research: bool = False
    requires_citations: bool = False
    requires_code: bool = False


class Plan(BaseModel):
    blog_title: str
    audience: str
    tone: str

    blog_kind: Literal[
        "explainer",
        "tutorial",
        "news_roundup",
        "comparison",
        "system_design"
    ] = "explainer"

    constraints: List[str] = Field(default_factory=list)

    tasks: List[Task]


class EvidenceItem(BaseModel):
    title: str
    url: str

    published_at: Optional[str] = None
    snippet: Optional[str] = None
    source: Optional[str] = None


class RouterDecision(BaseModel):
    needs_research: bool

    mode: Literal[
        "closed_book",
        "hybrid",
        "open_book"
    ]

    reason: str

    queries: List[str] = Field(
        default_factory=list
    )

    max_results_per_query: int = Field(
        5
    )


class EvidencePack(BaseModel):
    evidence: List[EvidenceItem] = Field(
        default_factory=list
    )


# ============================================================
# 2) State
# ============================================================

class State(TypedDict):

    # User input
    topic: str

    # Routing / research
    mode: str
    needs_research: bool
    queries: List[str]

    evidence: List[EvidenceItem]

    plan: Optional[Plan]

    # Recency
    as_of: str
    recency_days: int

    # Workers
    sections: Annotated[
        List[tuple[int, str]],
        operator.add
    ]

    # Final output
    final: str


# ============================================================
# 3) LLM
# ============================================================

llm = ChatOpenAI(
    model="gpt-4.1-mini"
)


# ============================================================
# 4) Router
# ============================================================

ROUTER_SYSTEM = """
You are a routing module for a technical blog planner.

Decide whether web research is needed BEFORE planning.

Modes:

- closed_book:
  needs_research=false
  Use this for evergreen concepts.

- hybrid:
  needs_research=true
  Use this for evergreen topics that also need
  up-to-date examples, tools, models, etc.

- open_book:
  needs_research=true
  Use this for volatile topics such as:
  latest news, weekly news, pricing, policies, recent events.

If needs_research=true:

- Output 3–10 high-signal, scoped queries.
- For open_book weekly roundup, include queries
  reflecting the last 7 days.
"""


def router_node(state: State) -> dict:

    decider = llm.with_structured_output(
        RouterDecision
    )

    decision = decider.invoke(
        [
            SystemMessage(
                content=ROUTER_SYSTEM
            ),

            HumanMessage(
                content=(
                    f"Topic: {state['topic']}\n"
                    f"As-of date: {state['as_of']}"
                )
            ),
        ]
    )

    if decision.mode == "open_book":
        recency_days = 7

    elif decision.mode == "hybrid":
        recency_days = 45

    else:
        recency_days = 3650

    return {
        "needs_research": decision.needs_research,
        "mode": decision.mode,
        "queries": decision.queries,
        "recency_days": recency_days,
    }


def route_next(state: State) -> str:

    if state["needs_research"]:
        return "research"

    return "orchestrator"


# ============================================================
# 5) Research
# ============================================================

def _tavily_search(
    query: str,
    max_results: int = 5
) -> List[dict]:

    if not os.getenv("TAVILY_API_KEY"):
        return []

    try:

        from langchain_community.tools.tavily_search import (
            TavilySearchResults
        )

        tool = TavilySearchResults(
            max_results=max_results
        )

        results = tool.invoke(
            {
                "query": query
            }
        )

        out: List[dict] = []

        for r in results or []:

            out.append(
                {
                    "title": r.get("title") or "",
                    "url": r.get("url") or "",
                    "snippet": (
                        r.get("content")
                        or r.get("snippet")
                        or ""
                    ),
                    "published_at": (
                        r.get("published_date")
                        or r.get("published_at")
                    ),
                    "source": r.get("source"),
                }
            )

        return out

    except Exception:
        return []


def _iso_to_date(
    s: Optional[str]
) -> Optional[date]:

    if not s:
        return None

    try:
        return date.fromisoformat(
            s[:10]
        )

    except Exception:
        return None


RESEARCH_SYSTEM = """
You are a research synthesizer.

Given raw web search results, produce EvidenceItem objects.

Rules:

- Only include items with a non-empty url.
- Prefer relevant and authoritative sources.
- Normalize published_at to ISO YYYY-MM-DD
  if reliably inferable.
- Otherwise use null.
- Do NOT guess dates.
- Keep snippets short.
- Deduplicate by URL.
"""


def research_node(state: State) -> dict:

    queries = (
        state.get("queries") or []
    )[:10]

    raw: List[dict] = []

    for q in queries:

        raw.extend(
            _tavily_search(
                q,
                max_results=6
            )
        )

    if not raw:
        return {
            "evidence": []
        }

    extractor = llm.with_structured_output(
        EvidencePack
    )

    pack = extractor.invoke(
        [
            SystemMessage(
                content=RESEARCH_SYSTEM
            ),

            HumanMessage(
                content=(
                    f"As-of date: {state['as_of']}\n"
                    f"Recency days: {state['recency_days']}\n\n"
                    f"Raw results:\n{raw}"
                )
            ),
        ]
    )

    # Deduplicate URLs
    dedup = {}

    for e in pack.evidence:

        if e.url:
            dedup[e.url] = e

    evidence = list(
        dedup.values()
    )

    # Apply recency filtering for open_book
    if state.get("mode") == "open_book":

        as_of = date.fromisoformat(
            state["as_of"]
        )

        cutoff = (
            as_of
            - timedelta(
                days=int(
                    state["recency_days"]
                )
            )
        )

        evidence = [
            e
            for e in evidence
            if (
                (d := _iso_to_date(e.published_at))
                and d >= cutoff
            )
        ]

    return {
        "evidence": evidence
    }


# ============================================================
# 6) Orchestrator / Planner
# ============================================================

ORCH_SYSTEM = """
You are a senior technical writer and developer advocate.

Produce a highly actionable outline for a technical blog post.

Requirements:

- 5–9 tasks.
- Each task must have:
  - id
  - title
  - goal
  - 3–6 bullets
  - target_words

Tags are flexible.
Do not force a fixed taxonomy.

Grounding:

- closed_book:
  evergreen content with no evidence dependence.

- hybrid:
  use evidence for up-to-date examples.
  Mark those tasks:
    requires_research=True
    requires_citations=True

- open_book:
  weekly/news roundup:
    - Set blog_kind="news_roundup"
    - No tutorial content unless requested.
    - If evidence is weak, reflect that.
    - Do not invent events.

Output must match the Plan schema.
"""


def orchestrator_node(state: State) -> dict:

    planner = llm.with_structured_output(
        Plan
    )

    mode = state.get(
        "mode",
        "closed_book"
    )

    evidence = state.get(
        "evidence",
        []
    )

    forced_kind = (
        "news_roundup"
        if mode == "open_book"
        else None
    )

    plan = planner.invoke(
        [
            SystemMessage(
                content=ORCH_SYSTEM
            ),

            HumanMessage(
                content=(
                    f"Topic: {state['topic']}\n"
                    f"Mode: {mode}\n"
                    f"As-of: {state['as_of']} "
                    f"(recency_days={state['recency_days']})\n"
                    f"{'Force blog_kind=news_roundup' if forced_kind else ''}\n\n"
                    f"Evidence:\n"
                    f"{[e.model_dump() for e in evidence][:16]}"
                )
            ),
        ]
    )

    if forced_kind:

        plan.blog_kind = "news_roundup"

    return {
        "plan": plan
    }


# ============================================================
# 7) Fanout
# ============================================================

def fanout(state: State):

    assert state["plan"] is not None

    return [

        Send(
            "worker",
            {
                "task": task.model_dump(),

                "topic": state["topic"],

                "mode": state["mode"],

                "as_of": state["as_of"],

                "recency_days": (
                    state["recency_days"]
                ),

                "plan": (
                    state["plan"]
                    .model_dump()
                ),

                "evidence": [
                    e.model_dump()
                    for e in state.get(
                        "evidence",
                        []
                    )
                ],
            }
        )

        for task in state["plan"].tasks
    ]


# ============================================================
# 8) Worker
# ============================================================

WORKER_SYSTEM = """
You are a senior technical writer and developer advocate.

Write ONE section of a technical blog post in Markdown.

Constraints:

- Cover ALL bullets in order.
- Target words ±15%.
- Output only section markdown.
- Start with:
  ## <Section Title>

Scope guard:

- If blog_kind=="news_roundup":
  do NOT drift into tutorials.
  Focus on events and implications.

Grounding:

- If mode=="open_book":
  do not introduce any specific
  event/company/model/funding/policy claim
  unless supported by provided Evidence URLs.

  For each supported claim,
  attach a Markdown link:

  [Source](URL)

  If unsupported, write:
  "Not found in provided sources."

- If requires_citations==true:
  cite Evidence URLs for external claims.

Code:

- If requires_code==true,
  include at least one minimal snippet.
"""


def worker_node(
    payload: dict
) -> dict:

    task = Task(
        **payload["task"]
    )

    plan = Plan(
        **payload["plan"]
    )

    evidence = [
        EvidenceItem(**e)
        for e in payload.get(
            "evidence",
            []
        )
    ]

    bullets_text = (
        "\n- "
        + "\n- ".join(
            task.bullets
        )
    )

    evidence_text = "\n".join(

        f"- {e.title} | "
        f"{e.url} | "
        f"{e.published_at or 'date:unknown'}"

        for e in evidence[:20]
    )

    section_md = llm.invoke(

        [
            SystemMessage(
                content=WORKER_SYSTEM
            ),

            HumanMessage(
                content=(

                    f"Blog title: "
                    f"{plan.blog_title}\n"

                    f"Audience: "
                    f"{plan.audience}\n"

                    f"Tone: "
                    f"{plan.tone}\n"

                    f"Blog kind: "
                    f"{plan.blog_kind}\n"

                    f"Constraints: "
                    f"{plan.constraints}\n"

                    f"Topic: "
                    f"{payload['topic']}\n"

                    f"Mode: "
                    f"{payload.get('mode')}\n"

                    f"As-of: "
                    f"{payload.get('as_of')} "
                    f"(recency_days="
                    f"{payload.get('recency_days')})\n\n"

                    f"Section title: "
                    f"{task.title}\n"

                    f"Goal: "
                    f"{task.goal}\n"

                    f"Target words: "
                    f"{task.target_words}\n"

                    f"Tags: "
                    f"{task.tags}\n"

                    f"requires_research: "
                    f"{task.requires_research}\n"

                    f"requires_citations: "
                    f"{task.requires_citations}\n"

                    f"requires_code: "
                    f"{task.requires_code}\n"

                    f"Bullets:"
                    f"{bullets_text}\n\n"

                    f"Evidence "
                    f"(ONLY cite these URLs):\n"
                    f"{evidence_text}\n"
                )
            ),
        ]

    ).content.strip()

    return {
        "sections": [
            (
                task.id,
                section_md
            )
        ]
    }


# ============================================================
# 9) Reducer
# ============================================================

def reducer(
    state: State
) -> dict:

    plan = state["plan"]

    if plan is None:
        raise ValueError(
            "Reducer called without plan."
        )

    # Sort sections according to task ID
    ordered_sections = [

        md

        for _, md in sorted(
            state["sections"],
            key=lambda x: x[0]
        )
    ]

    # Join all sections
    body = "\n\n".join(
        ordered_sections
    ).strip()

    # Create final Markdown
    final_md = (
        f"# {plan.blog_title}\n\n"
        f"{body}\n"
    )

    # Save file
    filename = "blog.md"

    output_path = Path(
        filename
    )

    output_path.write_text(
        final_md,
        encoding="utf-8"
    )

    return {
        "final": final_md
    }


# ============================================================
# 10) Build Main Graph
# ============================================================

g = StateGraph(State)


# Add nodes
g.add_node(
    "router",
    router_node
)

g.add_node(
    "research",
    research_node
)

g.add_node(
    "orchestrator",
    orchestrator_node
)

g.add_node(
    "worker",
    worker_node
)

g.add_node(
    "reducer",
    reducer
)


# START → Router
g.add_edge(
    START,
    "router"
)


# Router → Research OR Orchestrator
g.add_conditional_edges(
    "router",
    route_next,
    {
        "research": "research",
        "orchestrator": "orchestrator"
    }
)


# Research → Orchestrator
g.add_edge(
    "research",
    "orchestrator"
)


# Orchestrator → Workers
g.add_conditional_edges(
    "orchestrator",
    fanout,
    ["worker"]
)


# Worker → Reducer
g.add_edge(
    "worker",
    "reducer"
)


# Reducer → END
g.add_edge(
    "reducer",
    END
)


# Compile graph
app = g.compile()


# The compiled LangGraph application
