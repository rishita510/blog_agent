# import argparse
# import os
# import re
# import time
# from datetime import date
# from uuid import uuid4

# os.environ["SAVE_BLOGS"] = "0"
# os.environ["COVER_IMAGE"] = "0"

# import pandas as pd
# from pydantic import BaseModel, Field
# from langchain_core.messages import SystemMessage, HumanMessage
# from langchain_openai import ChatOpenAI

# from blog_backend import app

# DEFAULT_TOPICS = [
#     "How Redis caching works",
#     "Retrieval-augmented generation explained",
#     "Python asyncio explained",
#     "Designing a rate limiter",
#     "Kubernetes vs Docker Swarm",
#     "Vector databases comparison",
#     "LangGraph vs CrewAI for building agents",
#     "Latest open-source LLM releases",
#     "Weekly AI news roundup",
#     "Current state of AI coding assistants",
# ]

# JUDGE_SYSTEM = """
# You are a demanding reviewer of technical blog posts.
# Score each criterion from 1 (poor) to 5 (excellent).

# - relevance: stays on the topic and serves the stated audience
# - depth: concrete, specific and technically useful, not generic
# - clarity: well organised and easy to follow
# - groundedness: external claims are backed by the provided Evidence; unsupported claims are flagged or avoided

# Be strict. Use 5 rarely.
# """


# class JudgeScores(BaseModel):
#     relevance: int = Field(..., ge=1, le=5)
#     depth: int = Field(..., ge=1, le=5)
#     clarity: int = Field(..., ge=1, le=5)
#     groundedness: int = Field(..., ge=1, le=5)


# _judge = None


# def get_judge():
#     global _judge
#     if _judge is None:
#         _judge = ChatOpenAI(model=os.getenv("JUDGE_MODEL", "gpt-4.1-mini"), temperature=0)
#     return _judge


# def judge_blog(topic: str, md: str, evidence: list) -> JudgeScores:
#     ev = "\n".join(f"- {e.title} | {e.url}" for e in evidence[:20]) or "(none)"
#     return get_judge().with_structured_output(JudgeScores).invoke(
#         [
#             SystemMessage(content=JUDGE_SYSTEM),
#             HumanMessage(content=f"Topic: {topic}\n\nEvidence:\n{ev}\n\nBlog:\n{md[:24000]}"),
#         ]
#     )


# def citation_validity(md: str, evidence: list) -> float:
#     allowed = {e.url.rstrip("/") for e in evidence}
#     links = re.findall(r"\]\((https?://[^)\s]+)\)", md)
#     if not links:
#         return 1.0
#     return sum(1 for u in links if u.rstrip("/") in allowed) / len(links)


# def word_accuracy(plan, sections: dict, tolerance: float = 0.25) -> float:
#     ok = total = 0
#     for t in plan.tasks:
#         md = sections.get(t.id)
#         if md is None:
#             continue
#         total += 1
#         if abs(len(md.split()) - t.target_words) <= tolerance * t.target_words:
#             ok += 1
#     return ok / total if total else 0.0


# def run_one(topic: str, max_revisions: int) -> dict:
#     inputs = {
#         "topic": topic,
#         "mode": "",
#         "needs_research": False,
#         "queries": [],
#         "evidence": [],
#         "plan": None,
#         "as_of": date.today().isoformat(),
#         "recency_days": 7,
#         "sections": {},
#         "verdict_log": [],
#         "pending": [],
#         "rounds": 0,
#         "cover_image": "",
#         "report": {},
#         "final": "",
#     }

#     config = {
#         "configurable": {
#             "thread_id": uuid4().hex,
#             "auto_approve": True,
#             "max_revisions": max_revisions,
#         },
#         "recursion_limit": 80,
#     }

#     start = time.time()
#     out = app.invoke(inputs, config)
#     latency = time.time() - start

#     report = out["report"]
#     n = max(report["sections"], 1)
#     md = out["final"]
#     evidence = out.get("evidence") or []
#     scores = judge_blog(topic, md, evidence)

#     return {
#         "topic": topic,
#         "mode": out["mode"],
#         "sections": report["sections"],
#         "evidence_items": len(evidence),
#         "first_try_pass_rate": report["first_try_pass"] / n,
#         "final_pass_rate": report["final_pass"] / n,
#         "revision_rounds": report["revision_rounds"],
#         "invalid_urls_first": report["invalid_urls_first"],
#         "invalid_urls_final": report["invalid_urls_final"],
#         "citation_validity": citation_validity(md, evidence),
#         "unsupported_markers": md.count("Not found in provided sources"),
#         "word_accuracy": word_accuracy(out["plan"], out["sections"]),
#         "relevance": scores.relevance,
#         "depth": scores.depth,
#         "clarity": scores.clarity,
#         "groundedness": scores.groundedness,
#         "latency_s": round(latency, 1),
#     }


# def summarize(df: pd.DataFrame) -> str:
#     ok = df[df["error"].isna()] if "error" in df else df
#     if ok.empty:
#         return "No successful runs."

#     m = ok.mean(numeric_only=True)

#     lines = [
#         f"Runs: {len(ok)} successful of {len(df)}",
#         "",
#         "| Metric | Before critic (first draft) | After critic loop |",
#         "|---|---|---|",
#         f"| Sections passing fact-check | {m['first_try_pass_rate']:.0%} | {m['final_pass_rate']:.0%} |",
#         f"| Invalid citation URLs per blog | {ok['invalid_urls_first'].mean():.2f} | {ok['invalid_urls_final'].mean():.2f} |",
#         "",
#         "| Other metric (final blog) | Value |",
#         "|---|---|",
#         f"| Citation validity | {m['citation_validity']:.0%} |",
#         f"| Sections within +/-25% of target words | {m['word_accuracy']:.0%} |",
#         f"| Avg 'Not found in provided sources' per blog | {m['unsupported_markers']:.2f} |",
#         f"| Avg revision rounds | {m['revision_rounds']:.2f} |",
#         f"| Judge relevance (1-5) | {m['relevance']:.2f} |",
#         f"| Judge depth (1-5) | {m['depth']:.2f} |",
#         f"| Judge clarity (1-5) | {m['clarity']:.2f} |",
#         f"| Judge groundedness (1-5) | {m['groundedness']:.2f} |",
#         f"| Avg latency (s) | {m['latency_s']:.0f} |",
#     ]
#     return "\n".join(lines)


# def main():
#     ap = argparse.ArgumentParser()
#     ap.add_argument("--n", type=int, default=len(DEFAULT_TOPICS))
#     ap.add_argument("--topics-file", default=None)
#     ap.add_argument("--max-revisions", type=int, default=2)
#     ap.add_argument("--out", default="eval_results.csv")
#     args = ap.parse_args()

#     if args.topics_file:
#         with open(args.topics_file, encoding="utf-8") as f:
#             topics = [line.strip() for line in f if line.strip()]
#     else:
#         topics = DEFAULT_TOPICS

#     topics = topics[: args.n]

#     rows = []

#     for i, topic in enumerate(topics, 1):
#         print(f"[{i}/{len(topics)}] {topic}")
#         try:
#             row = run_one(topic, args.max_revisions)
#         except Exception as e:
#             print(f"  failed: {e}")
#             row = {"topic": topic, "error": str(e)}
#         rows.append(row)
#         pd.DataFrame(rows).to_csv(args.out, index=False)

#     df = pd.DataFrame(rows)
#     if "error" not in df:
#         df["error"] = None

#     summary = summarize(df)
#     print("\n" + summary)

#     with open("eval_summary.md", "w", encoding="utf-8") as f:
#         f.write(summary + "\n")

#     print(f"\nSaved {args.out} and eval_summary.md")


# if __name__ == "__main__":
#     main()


import argparse
import os
import re
import time
from datetime import date
from uuid import uuid4

os.environ["SAVE_BLOGS"] = "0"
os.environ["COVER_IMAGE"] = "0"

import pandas as pd
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from blog_backend import app

DEFAULT_TOPICS = [
    "How Redis caching works",
    "Retrieval-augmented generation explained",
    "Python asyncio explained",
    "Designing a rate limiter",
    "Kubernetes vs Docker Swarm",
    "Vector databases comparison",
    "LangGraph vs CrewAI for building agents",
    "Latest open-source LLM releases",
    "Weekly AI news roundup",
    "Current state of AI coding assistants",
]

JUDGE_SYSTEM = """
You are a demanding reviewer of technical blog posts.
Score each criterion from 1 (poor) to 5 (excellent).

- relevance: stays on the topic and serves the stated audience
- depth: concrete, specific and technically useful, not generic
- clarity: well organised and easy to follow
- groundedness: external claims are backed by the provided Evidence; unsupported claims are flagged or avoided

Be strict. Use 5 rarely.
"""


class JudgeScores(BaseModel):
    relevance: int = Field(..., ge=1, le=5)
    depth: int = Field(..., ge=1, le=5)
    clarity: int = Field(..., ge=1, le=5)
    groundedness: int = Field(..., ge=1, le=5)


_judge = None


def get_judge():
    global _judge
    if _judge is None:
        _judge = ChatOpenAI(model=os.getenv("JUDGE_MODEL", "gpt-4.1-mini"), temperature=0)
    return _judge


def judge_blog(topic: str, md: str, evidence: list) -> JudgeScores:
    ev = "\n".join(f"- {e.title} | {e.url}" for e in evidence[:20]) or "(none)"
    return get_judge().with_structured_output(JudgeScores).invoke(
        [
            SystemMessage(content=JUDGE_SYSTEM),
            HumanMessage(content=f"Topic: {topic}\n\nEvidence:\n{ev}\n\nBlog:\n{md[:24000]}"),
        ]
    )


def citation_validity(md: str, evidence: list) -> float:
    allowed = {e.url.rstrip("/") for e in evidence}
    links = re.findall(r"\]\((https?://[^)\s]+)\)", md)
    if not links:
        return 1.0
    return sum(1 for u in links if u.rstrip("/") in allowed) / len(links)


def word_accuracy(plan, sections: dict, tolerance: float = 0.25) -> float:
    ok = total = 0
    for t in plan.tasks:
        md = sections.get(t.id)
        if md is None:
            continue
        total += 1
        if abs(len(md.split()) - t.target_words) <= tolerance * t.target_words:
            ok += 1
    return ok / total if total else 0.0


def run_one(topic: str, max_revisions: int) -> dict:
    inputs = {
        "topic": topic,
        "mode": "",
        "needs_research": False,
        "queries": [],
        "evidence": [],
        "plan": None,
        "as_of": date.today().isoformat(),
        "recency_days": 7,
        "sections": {},
        "verdict_log": [],
        "pending": [],
        "rounds": 0,
        "cover_image": "",
        "report": {},
        "final": "",
    }

    config = {
        "configurable": {
            "thread_id": uuid4().hex,
            "auto_approve": True,
            "max_revisions": max_revisions,
        },
        "recursion_limit": 80,
    }

    start = time.time()
    out = app.invoke(inputs, config)
    latency = time.time() - start

    report = out["report"]
    n = max(report["sections"], 1)
    md = out["final"]
    evidence = out.get("evidence") or []
    scores = judge_blog(topic, md, evidence)

    ratios = [
        len(out["sections"][t.id].split()) / t.target_words
        for t in out["plan"].tasks
        if t.id in out["sections"] and t.target_words
    ]

    return {
        "topic": topic,
        "mode": out["mode"],
        "sections": report["sections"],
        "evidence_items": len(evidence),
        "first_try_pass_rate": report["first_try_pass"] / n,
        "final_pass_rate": report["final_pass"] / n,
        "revision_rounds": report["revision_rounds"],
        "invalid_urls_first": report["invalid_urls_first"],
        "invalid_urls_final": report["invalid_urls_final"],
        "citation_validity": citation_validity(md, evidence),
        "unsupported_markers": md.count("Not found in provided sources"),
        "word_accuracy": word_accuracy(out["plan"], out["sections"]),
        "avg_word_ratio": sum(ratios) / len(ratios) if ratios else 0.0,
        "relevance": scores.relevance,
        "depth": scores.depth,
        "clarity": scores.clarity,
        "groundedness": scores.groundedness if evidence else None,
        "latency_s": round(latency, 1),
    }


def summarize(df: pd.DataFrame) -> str:
    ok = df[df["error"].isna()] if "error" in df else df
    if ok.empty:
        return "No successful runs."

    ok = ok.copy()
    ok["groundedness"] = pd.to_numeric(ok["groundedness"], errors="coerce")

    m = ok.mean(numeric_only=True)

    lines = [
        f"Runs: {len(ok)} successful of {len(df)}",
        "",
        "| Metric | Before critic (first draft) | After critic loop |",
        "|---|---|---|",
        f"| Sections passing fact-check | {m['first_try_pass_rate']:.0%} | {m['final_pass_rate']:.0%} |",
        f"| Invalid citation URLs per blog | {ok['invalid_urls_first'].mean():.2f} | {ok['invalid_urls_final'].mean():.2f} |",
        "",
        "| Other metric (final blog) | Value |",
        "|---|---|",
        f"| Citation validity | {m['citation_validity']:.0%} |",
        f"| Sections within +/-25% of target words | {m['word_accuracy']:.0%} |",
        f"| Avg 'Not found in provided sources' per blog | {m['unsupported_markers']:.2f} |",
        f"| Avg revision rounds | {m['revision_rounds']:.2f} |",
        f"| Judge relevance (1-5) | {m['relevance']:.2f} |",
        f"| Judge depth (1-5) | {m['depth']:.2f} |",
        f"| Judge clarity (1-5) | {m['clarity']:.2f} |",
        f"| Judge groundedness (1-5, runs with evidence only) | {m['groundedness']:.2f} |",
        f"| Avg words / target words (1.0 = on target) | {m['avg_word_ratio']:.2f} |",
        f"| Avg latency (s) | {m['latency_s']:.0f} |",
        "",
        "| Mode | Runs | Avg evidence items | Pass (first draft) | Pass (after loop) | Invalid URLs first | Invalid URLs final |",
        "|---|---|---|---|---|---|---|",
    ]

    for mode, g in ok.groupby("mode"):
        lines.append(
            f"| {mode} | {len(g)} | {g['evidence_items'].mean():.1f} "
            f"| {g['first_try_pass_rate'].mean():.0%} | {g['final_pass_rate'].mean():.0%} "
            f"| {g['invalid_urls_first'].mean():.2f} | {g['invalid_urls_final'].mean():.2f} |"
        )

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=len(DEFAULT_TOPICS))
    ap.add_argument("--topics-file", default=None)
    ap.add_argument("--max-revisions", type=int, default=2)
    ap.add_argument("--out", default="eval_results.csv")
    args = ap.parse_args()

    if args.topics_file:
        with open(args.topics_file, encoding="utf-8") as f:
            topics = [line.strip() for line in f if line.strip()]
    else:
        topics = DEFAULT_TOPICS

    topics = topics[: args.n]

    rows = []

    for i, topic in enumerate(topics, 1):
        print(f"[{i}/{len(topics)}] {topic}")
        try:
            row = run_one(topic, args.max_revisions)
        except Exception as e:
            print(f"  failed: {e}")
            row = {"topic": topic, "error": str(e)}
        rows.append(row)
        pd.DataFrame(rows).to_csv(args.out, index=False)

    df = pd.DataFrame(rows)
    if "error" not in df:
        df["error"] = None

    summary = summarize(df)
    print("\n" + summary)

    with open("eval_summary.md", "w", encoding="utf-8") as f:
        f.write(summary + "\n")

    print(f"\nSaved {args.out} and eval_summary.md")


if __name__ == "__main__":
    main()