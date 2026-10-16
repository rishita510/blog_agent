# from __future__ import annotations

# import base64
# import operator
# import os
# import re
# from datetime import date, timedelta
# from pathlib import Path
# from typing import TypedDict, List, Dict, Optional, Literal, Annotated

# from pydantic import BaseModel, Field

# from langgraph.graph import StateGraph, START, END
# from langgraph.types import Send, interrupt
# from langgraph.checkpoint.memory import MemorySaver

# from langchain_openai import ChatOpenAI
# from langchain_core.messages import SystemMessage, HumanMessage
# from langchain_core.runnables import RunnableConfig

# from dotenv import load_dotenv

# load_dotenv()

# MAX_REVISIONS = 2


# class Task(BaseModel):
#     id: int
#     title: str
#     goal: str = Field(..., description="One sentence describing what the reader should do/understand.")
#     bullets: List[str] = Field(..., description="3 to 6 bullets.")
#     target_words: int = Field(..., description="Target words (120-550).")
#     tags: List[str] = Field(default_factory=list)
#     requires_research: bool = False
#     requires_citations: bool = False
#     requires_code: bool = False


# class Plan(BaseModel):
#     blog_title: str
#     audience: str
#     tone: str
#     blog_kind: Literal[
#         "explainer",
#         "tutorial",
#         "news_roundup",
#         "comparison",
#         "system_design",
#     ] = "explainer"
#     constraints: List[str] = Field(default_factory=list)
#     tasks: List[Task]


# class EvidenceItem(BaseModel):
#     title: str
#     url: str
#     published_at: Optional[str] = None
#     snippet: Optional[str] = None
#     source: Optional[str] = None


# class RouterDecision(BaseModel):
#     needs_research: bool
#     mode: Literal["closed_book", "hybrid", "open_book"]
#     reason: str
#     queries: List[str] = Field(default_factory=list)
#     max_results_per_query: int = Field(5)


# class EvidencePack(BaseModel):
#     evidence: List[EvidenceItem] = Field(default_factory=list)


# class SectionVerdict(BaseModel):
#     passed: bool
#     issues: List[str] = Field(
#         default_factory=list,
#         description="Concrete, fixable problems. Empty if the section passed.",
#     )


# def merge_dicts(a: Optional[dict], b: Optional[dict]) -> dict:
#     out = dict(a or {})
#     out.update(b or {})
#     return out


# class State(TypedDict):
#     topic: str
#     mode: str
#     needs_research: bool
#     queries: List[str]
#     evidence: List[EvidenceItem]
#     plan: Optional[Plan]
#     as_of: str
#     recency_days: int
#     sections: Annotated[Dict[int, str], merge_dicts]
#     verdict_log: Annotated[List[dict], operator.add]
#     pending: List[int]
#     rounds: int
#     cover_image: str
#     report: dict
#     final: str


# llm = ChatOpenAI(model="gpt-4.1-mini")


# def safe_slug(title: str) -> str:
#     s = title.strip().lower()
#     s = re.sub(r"[^a-z0-9 _-]+", "", s)
#     s = re.sub(r"\s+", "_", s).strip("_")
#     return s or "blog"


# def _cfg(config: Optional[RunnableConfig]) -> dict:
#     return (config or {}).get("configurable", {}) or {}


# def latest_verdicts(log: List[dict]) -> Dict[int, dict]:
#     out: Dict[int, dict] = {}
#     for e in log or []:
#         if e["id"] not in out or e["round"] >= out[e["id"]]["round"]:
#             out[e["id"]] = e
#     return out


# def format_evidence(evidence: List[EvidenceItem], limit: int = 20, snippet_chars: int = 300) -> str:
#     lines = []
#     for e in evidence[:limit]:
#         snippet = (e.snippet or "").replace("\n", " ")[:snippet_chars]
#         lines.append(
#             f"- {e.title} | {e.url} | {e.published_at or 'date:unknown'}"
#             + (f"\n  {snippet}" if snippet else "")
#         )
#     return "\n".join(lines)


# ROUTER_SYSTEM = """
# You are a routing module for a technical blog planner.

# Decide whether web research is needed BEFORE planning.

# Modes:

# - closed_book:
#   needs_research=false
#   Use this for evergreen concepts.

# - hybrid:
#   needs_research=true
#   Use this for evergreen topics that also need
#   up-to-date examples, tools, models, etc.

# - open_book:
#   needs_research=true
#   Use this for volatile topics such as:
#   latest news, weekly news, pricing, policies, recent events.

# If needs_research=true:

# - Output 3-10 high-signal, scoped queries.
# - For open_book weekly roundup, include queries
#   reflecting the last 7 days.
# """


# def router_node(state: State) -> dict:
#     decider = llm.with_structured_output(RouterDecision)

#     decision = decider.invoke(
#         [
#             SystemMessage(content=ROUTER_SYSTEM),
#             HumanMessage(
#                 content=(
#                     f"Topic: {state['topic']}\n"
#                     f"As-of date: {state['as_of']}"
#                 )
#             ),
#         ]
#     )

#     if decision.mode == "open_book":
#         recency_days = 7
#     elif decision.mode == "hybrid":
#         recency_days = 45
#     else:
#         recency_days = 3650

#     return {
#         "needs_research": decision.needs_research,
#         "mode": decision.mode,
#         "queries": decision.queries,
#         "recency_days": recency_days,
#     }


# def route_next(state: State) -> str:
#     if state["needs_research"]:
#         return "research"
#     return "orchestrator"


# def _tavily_search(query: str, max_results: int = 5) -> List[dict]:
#     if not os.getenv("TAVILY_API_KEY"):
#         return []

#     try:
#         from langchain_community.tools.tavily_search import TavilySearchResults

#         tool = TavilySearchResults(max_results=max_results)
#         results = tool.invoke({"query": query})

#         out: List[dict] = []

#         for r in results or []:
#             out.append(
#                 {
#                     "title": r.get("title") or "",
#                     "url": r.get("url") or "",
#                     "snippet": r.get("content") or r.get("snippet") or "",
#                     "published_at": r.get("published_date") or r.get("published_at"),
#                     "source": r.get("source"),
#                 }
#             )

#         return out

#     except Exception as e:
#         print(f"[tavily] search failed for '{query}': {e}")
#         return []


# def _iso_to_date(s: Optional[str]) -> Optional[date]:
#     if not s:
#         return None

#     try:
#         return date.fromisoformat(s[:10])
#     except Exception:
#         return None


# RESEARCH_SYSTEM = """
# You are a research synthesizer.

# Given raw web search results, produce EvidenceItem objects.

# Rules:

# - Only include items with a non-empty url.
# - Prefer relevant and authoritative sources.
# - Normalize published_at to ISO YYYY-MM-DD
#   if reliably inferable.
# - Otherwise use null.
# - Do NOT guess dates.
# - Keep snippets short.
# - Deduplicate by URL.
# """


# def research_node(state: State) -> dict:
#     queries = (state.get("queries") or [])[:10]

#     raw: List[dict] = []

#     for q in queries:
#         raw.extend(_tavily_search(q, max_results=6))

#     if not raw:
#         return {"evidence": []}

#     extractor = llm.with_structured_output(EvidencePack)

#     pack = extractor.invoke(
#         [
#             SystemMessage(content=RESEARCH_SYSTEM),
#             HumanMessage(
#                 content=(
#                     f"As-of date: {state['as_of']}\n"
#                     f"Recency days: {state['recency_days']}\n\n"
#                     f"Raw results:\n{raw}"
#                 )
#             ),
#         ]
#     )

#     dedup = {}

#     for e in pack.evidence:
#         if e.url:
#             dedup[e.url] = e

#     evidence = list(dedup.values())

#     if state.get("mode") == "open_book":
#         as_of = date.fromisoformat(state["as_of"])
#         cutoff = as_of - timedelta(days=int(state["recency_days"]))

#         evidence = [
#             e
#             for e in evidence
#             if (d := _iso_to_date(e.published_at)) and d >= cutoff
#         ]

#     return {"evidence": evidence}


# ORCH_SYSTEM = """
# You are a senior technical writer and developer advocate.

# Produce a highly actionable outline for a technical blog post.

# Requirements:

# - 5-9 tasks.
# - Each task must have:
#   - id
#   - title
#   - goal
#   - 3-6 bullets
#   - target_words

# Tags are flexible.
# Do not force a fixed taxonomy.

# Grounding:

# - closed_book:
#   evergreen content with no evidence dependence.

# - hybrid:
#   use evidence for up-to-date examples.
#   Mark those tasks:
#     requires_research=True
#     requires_citations=True

# - open_book:
#   weekly/news roundup:
#     - Set blog_kind="news_roundup"
#     - No tutorial content unless requested.
#     - If evidence is weak, reflect that.
#     - Do not invent events.

# Output must match the Plan schema.
# """


# def orchestrator_node(state: State) -> dict:
#     planner = llm.with_structured_output(Plan)

#     mode = state.get("mode", "closed_book")
#     evidence = state.get("evidence", [])

#     forced_kind = "news_roundup" if mode == "open_book" else None

#     plan = planner.invoke(
#         [
#             SystemMessage(content=ORCH_SYSTEM),
#             HumanMessage(
#                 content=(
#                     f"Topic: {state['topic']}\n"
#                     f"Mode: {mode}\n"
#                     f"As-of: {state['as_of']} "
#                     f"(recency_days={state['recency_days']})\n"
#                     f"{'Force blog_kind=news_roundup' if forced_kind else ''}\n\n"
#                     f"Evidence:\n"
#                     f"{[e.model_dump() for e in evidence][:16]}"
#                 )
#             ),
#         ]
#     )

#     if forced_kind:
#         plan.blog_kind = "news_roundup"

#     return {"plan": plan}


# def plan_review(state: State, config: RunnableConfig) -> dict:
#     plan = state["plan"]

#     if _cfg(config).get("auto_approve"):
#         return {"plan": plan}

#     decision = interrupt({"plan": plan.model_dump()})

#     if isinstance(decision, dict) and decision.get("plan"):
#         plan = Plan(**decision["plan"])

#     return {"plan": plan}


# def _payload(state: State, task: Task) -> dict:
#     return {
#         "task": task.model_dump(),
#         "topic": state["topic"],
#         "mode": state["mode"],
#         "as_of": state["as_of"],
#         "recency_days": state["recency_days"],
#         "plan": state["plan"].model_dump(),
#         "evidence": [e.model_dump() for e in state.get("evidence", [])],
#     }


# def fanout(state: State):
#     assert state["plan"] is not None

#     return [Send("worker", _payload(state, task)) for task in state["plan"].tasks]


# WORKER_SYSTEM = """
# You are a senior technical writer and developer advocate.

# Write ONE section of a technical blog post in Markdown.

# Constraints:

# - Cover ALL bullets in order.
# - Target words +/-15%.
# - Output only section markdown.
# - Start with:
#   ## <Section Title>

# Scope guard:

# - If blog_kind=="news_roundup":
#   do NOT drift into tutorials.
#   Focus on events and implications.

# Grounding:

# - If mode=="open_book":
#   do not introduce any specific
#   event/company/model/funding/policy claim
#   unless supported by provided Evidence URLs.

#   For each supported claim,
#   attach a Markdown link:

#   [Source](URL)

#   If unsupported, write:
#   "Not found in provided sources."

# - If requires_citations==true:
#   cite Evidence URLs for external claims.

# Code:

# - If requires_code==true,
#   include at least one minimal snippet.
# """


# REWRITE_SYSTEM = (
#     WORKER_SYSTEM
#     + """

# You are REVISING an existing section after a fact-check.

# - Fix every listed issue.
# - Remove or soften any claim the Evidence does not support. Never add new unsupported claims.
# - Cite only Evidence URLs.
# - Keep the same section heading and still cover ALL bullets.
# - Output only the revised section markdown.
# """
# )


# def _section_prompt(payload: dict, task: Task, plan: Plan, evidence: List[EvidenceItem]) -> str:
#     bullets_text = "\n- " + "\n- ".join(task.bullets)

#     return (
#         f"Blog title: {plan.blog_title}\n"
#         f"Audience: {plan.audience}\n"
#         f"Tone: {plan.tone}\n"
#         f"Blog kind: {plan.blog_kind}\n"
#         f"Constraints: {plan.constraints}\n"
#         f"Topic: {payload['topic']}\n"
#         f"Mode: {payload.get('mode')}\n"
#         f"As-of: {payload.get('as_of')} "
#         f"(recency_days={payload.get('recency_days')})\n\n"
#         f"Section title: {task.title}\n"
#         f"Goal: {task.goal}\n"
#         f"Target words: {task.target_words}\n"
#         f"Tags: {task.tags}\n"
#         f"requires_research: {task.requires_research}\n"
#         f"requires_citations: {task.requires_citations}\n"
#         f"requires_code: {task.requires_code}\n"
#         f"Bullets:{bullets_text}\n\n"
#         f"Evidence (ONLY cite these URLs):\n{format_evidence(evidence)}\n"
#     )


# def worker_node(payload: dict) -> dict:
#     task = Task(**payload["task"])
#     plan = Plan(**payload["plan"])
#     evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]

#     section_md = llm.invoke(
#         [
#             SystemMessage(content=WORKER_SYSTEM),
#             HumanMessage(content=_section_prompt(payload, task, plan, evidence)),
#         ]
#     ).content.strip()

#     return {"sections": {task.id: section_md}}


# IMAGE_PROMPT_SYSTEM = """
# Write ONE prompt for an image generation model to create a blog cover image.

# Rules:
# - Describe a clean, modern, abstract or illustrative scene related to the topic.
# - No text, letters, logos, or people's faces in the image.
# - Under 60 words.
# - Output only the prompt.
# """


# def image_node(state: State) -> dict:
#     plan = state["plan"]

#     if plan is None or os.getenv("COVER_IMAGE", "1") != "1":
#         return {"cover_image": ""}

#     try:
#         from openai import OpenAI

#         prompt = llm.invoke(
#             [
#                 SystemMessage(content=IMAGE_PROMPT_SYSTEM),
#                 HumanMessage(
#                     content=f"Blog title: {plan.blog_title}\nTopic: {state['topic']}"
#                 ),
#             ]
#         ).content.strip()

#         res = OpenAI().images.generate(
#             model="gpt-image-1",
#             prompt=prompt,
#             size="1536x1024",
#             quality="low",
#             output_format="jpeg",
#             output_compression=70,
#         )

#         b64 = res.data[0].b64_json

#         return {"cover_image": f"![{plan.blog_title}](data:image/jpeg;base64,{b64})"}

#     except Exception as e:
#         print(f"[image] generation failed: {e}")
#         return {"cover_image": ""}


# CRITIC_SYSTEM = """
# You are a strict but fair fact-checking editor for a technical blog section.

# You are given the section, its task spec, and the Evidence (titles, URLs, snippets).

# FAIL the section if any of these is true:
# 1. It states a specific external fact (a named release, date, number, benchmark,
#    funding, policy, quote) that the Evidence does not support and that is not
#    well-established general knowledge.
# 2. requires_citations is true but it cites no Evidence URL for its external claims.
# 3. It skips or contradicts one of the task bullets.

# PASS otherwise. Do not nitpick style, tone or length.
# Evergreen explanations of well-known concepts do not need citations.
# Each issue must be one concrete, fixable sentence that names the offending claim.
# """


# def critic_dispatch(state: State) -> dict:
#     if state.get("rounds", 0) == 0:
#         return {"pending": sorted(state["sections"].keys())}
#     return {}


# def critic_fanout(state: State):
#     tasks = {t.id: t for t in state["plan"].tasks}
#     ids = [
#         i
#         for i in (state.get("pending") or [])
#         if i in tasks and i in state["sections"]
#     ]

#     if not ids:
#         return "critic_gate"

#     return [
#         Send(
#             "critic_worker",
#             {
#                 "task": tasks[i].model_dump(),
#                 "section_md": state["sections"][i],
#                 "evidence": [e.model_dump() for e in state.get("evidence", [])],
#                 "mode": state.get("mode"),
#                 "round": state.get("rounds", 0),
#             },
#         )
#         for i in ids
#     ]


# def critic_worker(payload: dict) -> dict:
#     task = Task(**payload["task"])
#     md = payload["section_md"]
#     evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]

#     allowed = {e.url.rstrip("/") for e in evidence}
#     links = re.findall(r"\]\((https?://[^)\s]+)\)", md)
#     invalid = sorted({u for u in links if u.rstrip("/") not in allowed})

#     verdict = llm.with_structured_output(SectionVerdict).invoke(
#         [
#             SystemMessage(content=CRITIC_SYSTEM),
#             HumanMessage(
#                 content=(
#                     f"Mode: {payload.get('mode')}\n"
#                     f"requires_citations: {task.requires_citations}\n"
#                     f"Task goal: {task.goal}\n"
#                     f"Task bullets: {task.bullets}\n\n"
#                     f"Evidence:\n{format_evidence(evidence, limit=30)}\n\n"
#                     f"Section:\n{md}"
#                 )
#             ),
#         ]
#     )

#     issues = list(verdict.issues)

#     if invalid:
#         issues.append(f"Cites URLs that are not in the Evidence: {invalid}")

#     return {
#         "verdict_log": [
#             {
#                 "id": task.id,
#                 "title": task.title,
#                 "round": payload.get("round", 0),
#                 "passed": bool(verdict.passed) and not invalid,
#                 "issues": issues,
#                 "invalid_urls": invalid,
#                 "words": len(md.split()),
#                 "target_words": task.target_words,
#             }
#         ]
#     }


# def critic_gate(state: State, config: RunnableConfig) -> dict:
#     max_rev = int(_cfg(config).get("max_revisions", MAX_REVISIONS))

#     latest = latest_verdicts(state.get("verdict_log") or [])

#     failed = sorted(
#         i
#         for i in (state.get("pending") or [])
#         if i in latest and not latest[i]["passed"]
#     )

#     rounds = state.get("rounds", 0)

#     if failed and rounds < max_rev:
#         return {"pending": failed, "rounds": rounds + 1}

#     return {"pending": []}


# def after_gate(state: State):
#     pending = state.get("pending") or []

#     if not pending:
#         return "reducer"

#     latest = latest_verdicts(state.get("verdict_log") or [])
#     tasks = {t.id: t for t in state["plan"].tasks}

#     return [
#         Send(
#             "rewriter",
#             {
#                 **_payload(state, tasks[i]),
#                 "section_md": state["sections"][i],
#                 "issues": latest[i]["issues"],
#             },
#         )
#         for i in pending
#         if i in tasks and i in state["sections"] and i in latest
#     ]


# def rewriter_node(payload: dict) -> dict:
#     task = Task(**payload["task"])
#     plan = Plan(**payload["plan"])
#     evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]

#     issues_text = "\n- " + "\n- ".join(payload.get("issues") or ["(none listed)"])

#     revised = llm.invoke(
#         [
#             SystemMessage(content=REWRITE_SYSTEM),
#             HumanMessage(
#                 content=(
#                     _section_prompt(payload, task, plan, evidence)
#                     + f"\nCurrent draft:\n{payload['section_md']}\n\n"
#                     + f"Issues to fix:{issues_text}\n"
#                 )
#             ),
#         ]
#     ).content.strip()

#     return {"sections": {task.id: revised}}


# def reducer(state: State) -> dict:
#     plan = state["plan"]

#     if plan is None:
#         raise ValueError("Reducer called without plan.")

#     body = "\n\n".join(
#         md for _, md in sorted(state["sections"].items(), key=lambda x: x[0])
#     ).strip()

#     cover = state.get("cover_image") or ""
#     final_md = (
#         f"# {plan.blog_title}\n\n{cover}\n\n{body}\n"
#         if cover
#         else f"# {plan.blog_title}\n\n{body}\n"
#     )

#     log = state.get("verdict_log") or []
#     first = {e["id"]: e for e in log if e["round"] == 0}
#     latest = latest_verdicts(log)

#     report = {
#         "sections": len(first),
#         "first_try_pass": sum(1 for e in first.values() if e["passed"]),
#         "final_pass": sum(1 for e in latest.values() if e["passed"]),
#         "revision_rounds": state.get("rounds", 0),
#         "revised_sections": sorted(i for i, e in latest.items() if e["round"] > 0),
#         "invalid_urls_first": sum(len(e["invalid_urls"]) for e in first.values()),
#         "invalid_urls_final": sum(len(e["invalid_urls"]) for e in latest.values()),
#     }

#     if os.getenv("SAVE_BLOGS", "1") == "1":
#         Path(f"{safe_slug(plan.blog_title)}.md").write_text(final_md, encoding="utf-8")

#     return {"final": final_md, "report": report}


# g = StateGraph(State)

# g.add_node("router", router_node)
# g.add_node("research", research_node)
# g.add_node("orchestrator", orchestrator_node)
# g.add_node("plan_review", plan_review)
# g.add_node("worker", worker_node)
# g.add_node("images", image_node)
# g.add_node("critic_dispatch", critic_dispatch)
# g.add_node("critic_worker", critic_worker)
# g.add_node("critic_gate", critic_gate)
# g.add_node("rewriter", rewriter_node)
# g.add_node("reducer", reducer)

# g.add_edge(START, "router")

# g.add_conditional_edges(
#     "router",
#     route_next,
#     {"research": "research", "orchestrator": "orchestrator"},
# )

# g.add_edge("research", "orchestrator")
# g.add_edge("orchestrator", "plan_review")
# g.add_conditional_edges("plan_review", fanout, ["worker"])
# g.add_edge("worker", "images")
# g.add_edge("images", "critic_dispatch")
# g.add_conditional_edges("critic_dispatch", critic_fanout, ["critic_worker", "critic_gate"])
# g.add_edge("critic_worker", "critic_gate")
# g.add_conditional_edges("critic_gate", after_gate, ["rewriter", "reducer"])
# g.add_edge("rewriter", "critic_dispatch")
# g.add_edge("reducer", END)

# def _make_checkpointer():
#     try:
#         from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

#         serde = JsonPlusSerializer(
#             allowed_msgpack_modules=[
#                 (__name__, "Plan"),
#                 (__name__, "Task"),
#                 (__name__, "EvidenceItem"),
#             ]
#         )
#         return MemorySaver(serde=serde)
#     except Exception:
#         return MemorySaver()


# app = g.compile(checkpointer=_make_checkpointer())



from __future__ import annotations

import base64
import operator
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict, List, Dict, Optional, Literal, Annotated

from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt
from langgraph.checkpoint.memory import MemorySaver

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from dotenv import load_dotenv

load_dotenv()

MAX_REVISIONS = 2


class Task(BaseModel):
    id: int
    title: str
    goal: str = Field(..., description="One sentence describing what the reader should do/understand.")
    bullets: List[str] = Field(..., description="3 to 6 bullets.")
    target_words: int = Field(..., description="Target words (120-550).")
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
        "system_design",
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
    mode: Literal["closed_book", "hybrid", "open_book"]
    reason: str
    queries: List[str] = Field(default_factory=list)
    max_results_per_query: int = Field(5)


class EvidencePack(BaseModel):
    evidence: List[EvidenceItem] = Field(default_factory=list)


class SectionVerdict(BaseModel):
    passed: bool
    issues: List[str] = Field(
        default_factory=list,
        description="Concrete, fixable problems. Empty if the section passed.",
    )


def merge_dicts(a: Optional[dict], b: Optional[dict]) -> dict:
    out = dict(a or {})
    out.update(b or {})
    return out


class State(TypedDict):
    topic: str
    mode: str
    needs_research: bool
    queries: List[str]
    evidence: List[EvidenceItem]
    plan: Optional[Plan]
    as_of: str
    recency_days: int
    sections: Annotated[Dict[int, str], merge_dicts]
    verdict_log: Annotated[List[dict], operator.add]
    pending: List[int]
    rounds: int
    cover_image: str
    report: dict
    final: str


llm = ChatOpenAI(model="gpt-4.1-mini")


def safe_slug(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9 _-]+", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s or "blog"


def _cfg(config: Optional[RunnableConfig]) -> dict:
    return (config or {}).get("configurable", {}) or {}


def latest_verdicts(log: List[dict]) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    for e in log or []:
        if e["id"] not in out or e["round"] >= out[e["id"]]["round"]:
            out[e["id"]] = e
    return out


def format_evidence(evidence: List[EvidenceItem], limit: int = 20, snippet_chars: int = 300) -> str:
    lines = []
    for e in evidence[:limit]:
        snippet = (e.snippet or "").replace("\n", " ")[:snippet_chars]
        lines.append(
            f"- {e.title} | {e.url} | {e.published_at or 'date:unknown'}"
            + (f"\n  {snippet}" if snippet else "")
        )
    return "\n".join(lines)


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

- Output 3-10 high-signal, scoped queries.
- For open_book weekly roundup, include queries
  reflecting the last 7 days.
"""


def router_node(state: State) -> dict:
    decider = llm.with_structured_output(RouterDecision)

    decision = decider.invoke(
        [
            SystemMessage(content=ROUTER_SYSTEM),
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


def _tavily_search(query: str, max_results: int = 5) -> List[dict]:
    if not os.getenv("TAVILY_API_KEY"):
        return []

    try:
        from langchain_community.tools.tavily_search import TavilySearchResults

        tool = TavilySearchResults(max_results=max_results)
        results = tool.invoke({"query": query})

        out: List[dict] = []

        for r in results or []:
            out.append(
                {
                    "title": r.get("title") or "",
                    "url": r.get("url") or "",
                    "snippet": r.get("content") or r.get("snippet") or "",
                    "published_at": r.get("published_date") or r.get("published_at"),
                    "source": r.get("source"),
                }
            )

        return out

    except Exception as e:
        print(f"[tavily] search failed for '{query}': {e}")
        return []


def _iso_to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None

    try:
        return date.fromisoformat(s[:10])
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
    queries = (state.get("queries") or [])[:10]

    raw: List[dict] = []

    for q in queries:
        raw.extend(_tavily_search(q, max_results=6))

    if not raw:
        return {"evidence": []}

    extractor = llm.with_structured_output(EvidencePack)

    pack = extractor.invoke(
        [
            SystemMessage(content=RESEARCH_SYSTEM),
            HumanMessage(
                content=(
                    f"As-of date: {state['as_of']}\n"
                    f"Recency days: {state['recency_days']}\n\n"
                    f"Raw results:\n{raw}"
                )
            ),
        ]
    )

    dedup = {}

    for e in pack.evidence:
        if e.url:
            dedup[e.url] = e

    evidence = list(dedup.values())

    if state.get("mode") == "open_book":
        as_of = date.fromisoformat(state["as_of"])
        cutoff = as_of - timedelta(days=int(state["recency_days"]))

        evidence = [
            e
            for e in evidence
            if (d := _iso_to_date(e.published_at)) is None or d >= cutoff
        ]

    return {"evidence": evidence}


ORCH_SYSTEM = """
You are a senior technical writer and developer advocate.

Produce a highly actionable outline for a technical blog post.

Requirements:

- 5-9 tasks.
- Each task must have:
  - id
  - title
  - goal
  - 3-6 bullets
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
    planner = llm.with_structured_output(Plan)

    mode = state.get("mode", "closed_book")
    evidence = state.get("evidence", [])

    forced_kind = "news_roundup" if mode == "open_book" else None

    plan = planner.invoke(
        [
            SystemMessage(content=ORCH_SYSTEM),
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

    return {"plan": plan}


def plan_review(state: State, config: RunnableConfig) -> dict:
    plan = state["plan"]

    if _cfg(config).get("auto_approve"):
        return {"plan": plan}

    decision = interrupt({"plan": plan.model_dump()})

    if isinstance(decision, dict) and decision.get("plan"):
        plan = Plan(**decision["plan"])

    return {"plan": plan}


def _payload(state: State, task: Task) -> dict:
    return {
        "task": task.model_dump(),
        "topic": state["topic"],
        "mode": state["mode"],
        "as_of": state["as_of"],
        "recency_days": state["recency_days"],
        "plan": state["plan"].model_dump(),
        "evidence": [e.model_dump() for e in state.get("evidence", [])],
    }


def fanout(state: State):
    assert state["plan"] is not None

    return [Send("worker", _payload(state, task)) for task in state["plan"].tasks]


WORKER_SYSTEM = """
You are a senior technical writer and developer advocate.

Write ONE section of a technical blog post in Markdown.

Constraints:

- Cover ALL bullets in order.
- Target words +/-15%.
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


REWRITE_SYSTEM = (
    WORKER_SYSTEM
    + """

You are REVISING an existing section after a fact-check.

- Fix every listed issue.
- Remove or soften any claim the Evidence does not support. Never add new unsupported claims.
- If there is no Evidence, remove every link and every specific recent fact instead of adding sources.
- Cite only Evidence URLs.
- Keep the same section heading and still cover ALL bullets.
- Output only the revised section markdown.
"""
)


def _section_prompt(payload: dict, task: Task, plan: Plan, evidence: List[EvidenceItem]) -> str:
    bullets_text = "\n- " + "\n- ".join(task.bullets)

    no_evidence = ""
    if not evidence and payload.get("mode") != "closed_book":
        no_evidence = (
            "\nNO EVIDENCE IS AVAILABLE for this topic. Do NOT include any links or URLs, "
            "and do not state specific recent releases, dates, numbers or events. "
            "Write only what is well established, and write "
            "\"Not found in provided sources.\" for anything that would need a source.\n"
        )

    return (
        f"Blog title: {plan.blog_title}\n"
        f"Audience: {plan.audience}\n"
        f"Tone: {plan.tone}\n"
        f"Blog kind: {plan.blog_kind}\n"
        f"Constraints: {plan.constraints}\n"
        f"Topic: {payload['topic']}\n"
        f"Mode: {payload.get('mode')}\n"
        f"As-of: {payload.get('as_of')} "
        f"(recency_days={payload.get('recency_days')})\n\n"
        f"Section title: {task.title}\n"
        f"Goal: {task.goal}\n"
        f"Target words: {task.target_words}\n"
        f"Tags: {task.tags}\n"
        f"requires_research: {task.requires_research}\n"
        f"requires_citations: {task.requires_citations}\n"
        f"requires_code: {task.requires_code}\n"
        f"Bullets:{bullets_text}\n\n"
        f"Evidence (ONLY cite these URLs):\n{format_evidence(evidence)}\n"
        f"{no_evidence}"
    )


def worker_node(payload: dict) -> dict:
    task = Task(**payload["task"])
    plan = Plan(**payload["plan"])
    evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]

    section_md = llm.invoke(
        [
            SystemMessage(content=WORKER_SYSTEM),
            HumanMessage(content=_section_prompt(payload, task, plan, evidence)),
        ]
    ).content.strip()

    return {"sections": {task.id: section_md}}


IMAGE_PROMPT_SYSTEM = """
Write ONE prompt for an image generation model to create a blog cover image.

Rules:
- Describe a clean, modern, abstract or illustrative scene related to the topic.
- No text, letters, logos, or people's faces in the image.
- Under 60 words.
- Output only the prompt.
"""


def image_node(state: State) -> dict:
    plan = state["plan"]

    if plan is None or os.getenv("COVER_IMAGE", "1") != "1":
        return {"cover_image": ""}

    try:
        from openai import OpenAI

        prompt = llm.invoke(
            [
                SystemMessage(content=IMAGE_PROMPT_SYSTEM),
                HumanMessage(
                    content=f"Blog title: {plan.blog_title}\nTopic: {state['topic']}"
                ),
            ]
        ).content.strip()

        res = OpenAI().images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1536x1024",
            quality="low",
            output_format="jpeg",
            output_compression=70,
        )

        b64 = res.data[0].b64_json

        return {"cover_image": f"![{plan.blog_title}](data:image/jpeg;base64,{b64})"}

    except Exception as e:
        print(f"[image] generation failed: {e}")
        return {"cover_image": ""}


CRITIC_SYSTEM = """
You are a strict but fair fact-checking editor for a technical blog section.

You are given the section, its task spec, and the Evidence (titles, URLs, snippets).

FAIL the section if any of these is true:
1. It states a specific external fact (a named release, date, number, benchmark,
   funding, policy, quote) that the Evidence does not support and that is not
   well-established general knowledge.
2. Evidence is available and requires_citations is true, but it cites no Evidence URL
   for its external claims.
3. It skips or contradicts one of the task bullets.
4. Evidence is NOT available (and mode is not closed_book) and the section contains any
   link, or states specific recent facts. Rule 2 does not apply in that case: a section
   cannot cite sources that do not exist.

PASS otherwise. Do not nitpick style, tone or length.
Evergreen explanations of well-known concepts do not need citations.
Each issue must be one concrete, fixable sentence that names the offending claim.
"""


def critic_dispatch(state: State) -> dict:
    if state.get("rounds", 0) == 0:
        return {"pending": sorted(state["sections"].keys())}
    return {}


def critic_fanout(state: State):
    tasks = {t.id: t for t in state["plan"].tasks}
    ids = [
        i
        for i in (state.get("pending") or [])
        if i in tasks and i in state["sections"]
    ]

    if not ids:
        return "critic_gate"

    return [
        Send(
            "critic_worker",
            {
                "task": tasks[i].model_dump(),
                "section_md": state["sections"][i],
                "evidence": [e.model_dump() for e in state.get("evidence", [])],
                "mode": state.get("mode"),
                "round": state.get("rounds", 0),
            },
        )
        for i in ids
    ]


def critic_worker(payload: dict) -> dict:
    task = Task(**payload["task"])
    md = payload["section_md"]
    evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]

    allowed = {e.url.rstrip("/") for e in evidence}
    links = re.findall(r"\]\((https?://[^)\s]+)\)", md)
    invalid = sorted({u for u in links if u.rstrip("/") not in allowed})

    verdict = llm.with_structured_output(SectionVerdict).invoke(
        [
            SystemMessage(content=CRITIC_SYSTEM),
            HumanMessage(
                content=(
                    f"Mode: {payload.get('mode')}\n"
                    f"Evidence available: {bool(evidence)}\n"
                    f"requires_citations: {task.requires_citations}\n"
                    f"Task goal: {task.goal}\n"
                    f"Task bullets: {task.bullets}\n\n"
                    f"Evidence:\n{format_evidence(evidence, limit=30)}\n\n"
                    f"Section:\n{md}"
                )
            ),
        ]
    )

    issues = list(verdict.issues)

    if invalid:
        issues.append(f"Cites URLs that are not in the Evidence: {invalid}")

    return {
        "verdict_log": [
            {
                "id": task.id,
                "title": task.title,
                "round": payload.get("round", 0),
                "passed": bool(verdict.passed) and not invalid,
                "issues": issues,
                "invalid_urls": invalid,
                "words": len(md.split()),
                "target_words": task.target_words,
            }
        ]
    }


def critic_gate(state: State, config: RunnableConfig) -> dict:
    max_rev = int(_cfg(config).get("max_revisions", MAX_REVISIONS))

    latest = latest_verdicts(state.get("verdict_log") or [])

    failed = sorted(
        i
        for i in (state.get("pending") or [])
        if i in latest and not latest[i]["passed"]
    )

    rounds = state.get("rounds", 0)

    if failed and rounds < max_rev:
        return {"pending": failed, "rounds": rounds + 1}

    return {"pending": []}


def after_gate(state: State):
    pending = state.get("pending") or []

    if not pending:
        return "reducer"

    latest = latest_verdicts(state.get("verdict_log") or [])
    tasks = {t.id: t for t in state["plan"].tasks}

    return [
        Send(
            "rewriter",
            {
                **_payload(state, tasks[i]),
                "section_md": state["sections"][i],
                "issues": latest[i]["issues"],
            },
        )
        for i in pending
        if i in tasks and i in state["sections"] and i in latest
    ]


def rewriter_node(payload: dict) -> dict:
    task = Task(**payload["task"])
    plan = Plan(**payload["plan"])
    evidence = [EvidenceItem(**e) for e in payload.get("evidence", [])]

    issues_text = "\n- " + "\n- ".join(payload.get("issues") or ["(none listed)"])

    revised = llm.invoke(
        [
            SystemMessage(content=REWRITE_SYSTEM),
            HumanMessage(
                content=(
                    _section_prompt(payload, task, plan, evidence)
                    + f"\nCurrent draft:\n{payload['section_md']}\n\n"
                    + f"Issues to fix:{issues_text}\n"
                )
            ),
        ]
    ).content.strip()

    return {"sections": {task.id: revised}}


def reducer(state: State) -> dict:
    plan = state["plan"]

    if plan is None:
        raise ValueError("Reducer called without plan.")

    body = "\n\n".join(
        md for _, md in sorted(state["sections"].items(), key=lambda x: x[0])
    ).strip()

    cover = state.get("cover_image") or ""
    final_md = (
        f"# {plan.blog_title}\n\n{cover}\n\n{body}\n"
        if cover
        else f"# {plan.blog_title}\n\n{body}\n"
    )

    log = state.get("verdict_log") or []
    first = {e["id"]: e for e in log if e["round"] == 0}
    latest = latest_verdicts(log)

    report = {
        "sections": len(first),
        "first_try_pass": sum(1 for e in first.values() if e["passed"]),
        "final_pass": sum(1 for e in latest.values() if e["passed"]),
        "revision_rounds": state.get("rounds", 0),
        "revised_sections": sorted(i for i, e in latest.items() if e["round"] > 0),
        "invalid_urls_first": sum(len(e["invalid_urls"]) for e in first.values()),
        "invalid_urls_final": sum(len(e["invalid_urls"]) for e in latest.values()),
    }

    if os.getenv("SAVE_BLOGS", "1") == "1":
        Path(f"{safe_slug(plan.blog_title)}.md").write_text(final_md, encoding="utf-8")

    return {"final": final_md, "report": report}


g = StateGraph(State)

g.add_node("router", router_node)
g.add_node("research", research_node)
g.add_node("orchestrator", orchestrator_node)
g.add_node("plan_review", plan_review)
g.add_node("worker", worker_node)
g.add_node("images", image_node)
g.add_node("critic_dispatch", critic_dispatch)
g.add_node("critic_worker", critic_worker)
g.add_node("critic_gate", critic_gate)
g.add_node("rewriter", rewriter_node)
g.add_node("reducer", reducer)

g.add_edge(START, "router")

g.add_conditional_edges(
    "router",
    route_next,
    {"research": "research", "orchestrator": "orchestrator"},
)

g.add_edge("research", "orchestrator")
g.add_edge("orchestrator", "plan_review")
g.add_conditional_edges("plan_review", fanout, ["worker"])
g.add_edge("worker", "images")
g.add_edge("images", "critic_dispatch")
g.add_conditional_edges("critic_dispatch", critic_fanout, ["critic_worker", "critic_gate"])
g.add_edge("critic_worker", "critic_gate")
g.add_conditional_edges("critic_gate", after_gate, ["rewriter", "reducer"])
g.add_edge("rewriter", "critic_dispatch")
g.add_edge("reducer", END)

def _make_checkpointer():
    try:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        serde = JsonPlusSerializer(
            allowed_msgpack_modules=[
                (__name__, "Plan"),
                (__name__, "Task"),
                (__name__, "EvidenceItem"),
            ]
        )
        return MemorySaver(serde=serde)
    except Exception:
        return MemorySaver()


app = g.compile(checkpointer=_make_checkpointer())