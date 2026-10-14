# AI Blog Writing Agent

A research-driven AI blog generation system built with **LangGraph, LangChain, OpenAI, Tavily, and Streamlit**. The application uses a multi-stage workflow to determine whether research is required, generate a structured writing plan, write blog sections in parallel, and combine them into a Markdown document.

## Overview

Writing a high-quality technical blog requires more than generating text from a single prompt. It involves researching relevant information, organizing ideas, maintaining consistency, and grounding factual claims in credible sources.

This project explores an agentic approach to blog generation by separating these responsibilities into distinct stages orchestrated using LangGraph.

## Key Features

- **Adaptive Research Routing:** Classifies topics into closed-book, hybrid, and open-book modes to determine whether web research is needed.
- **Web Research:** Uses Tavily Search to retrieve relevant sources, URLs, snippets, and publication dates.
- **Structured Planning:** Generates a blog outline using Pydantic schemas, with individual tasks, goals, key points, target word counts, and writing requirements.
- **Parallel Section Generation:** Uses LangGraph's `Send` mechanism to delegate individual sections to worker nodes.
- **Evidence-Aware Generation:** Passes collected research evidence to the planner and writing agents to support factual claims and citations.
- **Automated Assembly:** Combines generated sections in their planned order into a single Markdown document.
- **Interactive Streamlit UI:** Provides dedicated tabs for the writing plan, research evidence, Markdown preview, and execution logs.
- **Markdown Export:** Allows users to download the generated blog.
- **Past Blog Loading:** Supports loading existing Markdown files from the application's working directory.

## Architecture

The application consists of two main components:

- `blog_backend.py` — Implements the LangGraph workflow, state management, research logic, planning, worker agents, and final document assembly.
- `app.py` — Represents the Streamlit frontend entry point. Use your actual frontend filename if it differs.

### Workflow

```text
User Input: Topic + As-of Date
             |
             v
       Router Agent
             |
      +------+------+
      |             |
 Closed-book     Research
      |             |
      |       Tavily Search
      |             |
      +------+------+
             |
             v
    Orchestrator / Planner
             |
             v
     Structured Blog Plan
             |
             v
     Parallel Worker Agents
       |       |       |
       v       v       v
    Section  Section  Section
       1       2       N
       |       |       |
       +-------+-------+
             |
             v
           Reducer
             |
             v
       Final Markdown
```

### Workflow Components

**1. Router Agent**

Uses an LLM with structured output to select a research strategy based on the topic and the specified date.

- `closed_book`: For evergreen concepts that do not require external research.
- `hybrid`: For evergreen topics that benefit from recent examples, tools, or developments.
- `open_book`: For time-sensitive topics such as news roundups and recent developments.

**2. Research Node**

Uses Tavily Search to retrieve relevant web results. The results are converted into structured evidence objects containing titles, URLs, snippets, sources, and publication dates where available.

In open-book mode, the workflow filters evidence using publication dates and the configured recency window.

**3. Orchestrator / Planner**

Creates a structured blog plan containing the title, target audience, tone, blog type, constraints, and individual writing tasks.

Each task specifies its goal, key points, target word count, and whether research, citations, or code are required.

**4. Worker Agents**

Each worker generates one section of the blog in Markdown. Workers receive the relevant task, overall plan, topic, research mode, and available evidence.

LangGraph's parallel execution mechanism allows independent sections to be generated concurrently.

**5. Reducer**

Collects the completed sections, sorts them by task ID, combines them into a single Markdown document, and saves the output as `blog.md`.

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python |
| Agent orchestration | LangGraph |
| LLM integration | LangChain |
| Language model | OpenAI GPT-4.1 mini |
| Structured outputs | Pydantic |
| Web research | Tavily Search |
| User interface | Streamlit |
| Data presentation | Pandas |
| Configuration | Python dotenv |
| Output format | Markdown |

## Project Structure

```text
project/
├── blog_backend.py       # LangGraph workflow and agents
├── app.py                # Streamlit frontend (rename as needed)
├── .env                  # API keys; do not commit
├── .gitignore
└── README.md             # Project documentation
```

Generated Markdown files are saved in the application's working directory. The backend currently uses `blog.md` as its output filename.

## Getting Started

### 1. Clone the repository

```bash
git clone <your-repository-url>
cd <your-repository-folder>
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Windows:

```bash
.venv\Scripts\activate
```

On macOS or Linux:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

Install the libraries used by the application:

```bash
pip install langgraph langchain langchain-openai langchain-community streamlit pandas pydantic python-dotenv
```

### 4. Configure environment variables

Create a `.env` file in the project directory:

```env
OPENAI_API_KEY=your_openai_api_key
TAVILY_API_KEY=your_tavily_api_key
```

The OpenAI API key is required for LLM operations. The Tavily API key is needed for web research; without it, the current research helper returns no search results.

**Important:** Never commit your `.env` file or expose API keys in your repository.

### 5. Run the application

Run the Streamlit frontend file:

```bash
streamlit run app.py
```

Replace `app.py` with the actual filename of your frontend if necessary.

### 6. Generate a blog

1. Enter a topic in the sidebar.
2. Select the as-of date.
3. Click **Generate Blog**.
4. Inspect the writing plan and collected research evidence.
5. View the generated Markdown.
6. Download the completed blog.

## Current Implementation Status

The current implementation includes the core graph architecture, structured planning, conditional research routing, parallel section generation, Markdown assembly, and the Streamlit interface.

The following areas remain opportunities for improvement:

- Automated evaluation of generated blog quality.
- Independent verification of factual claims and citations.
- More robust error handling and execution monitoring.
- Persistent storage and management of generated blogs.
- Automated tests for routing, research, parallel execution, and output quality.
- Deployment and performance evaluation.

These improvements are intended to make the system more reliable and easier to evaluate.

## Future Enhancements

- **Retrieval Evaluation:** Measure the relevance and usefulness of retrieved research sources.
- **Generation Evaluation:** Assess factual grounding, completeness, coherence, and adherence to writing requirements.
- **Citation Verification:** Check whether cited sources support the claims they accompany.
- **Quality-Control Stage:** Introduce a review step before the final blog is produced.
- **Persistent History:** Store blog runs, plans, and evidence in a database.
- **Performance Monitoring:** Track execution time, token usage, and estimated API cost.
- **Testing:** Build a benchmark of representative topics to compare output quality across iterations.

## Learning Objectives

This project explores practical applications of:

- Agentic workflows and graph-based orchestration.
- LLM structured outputs and schema validation.
- Conditional routing and state management.
- Parallel task execution and result aggregation.
- Research-grounded text generation.
- Building interactive interfaces for LLM applications.

## License

Add a license appropriate for your project before distributing or accepting contributions.

---

**Project status:** Under active development.

Built to explore research-driven content generation and multi-agent workflow orchestration with LangGraph.
