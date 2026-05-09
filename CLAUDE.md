# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
# Setup
make setup                          # create venv, install dev deps, copy .env.example

# Run
make run                            # python -m quantgpt --transport http (port 8003)
make dev                            # python -m quantgpt --transport http --port 8003
python -m quantgpt --transport stdio  # MCP stdio mode (for Claude Desktop)

# Test
make test                           # pytest tests/ -x -q
pytest tests/test_foo.py -x -q      # single test file
pytest tests/test_foo.py::test_bar -x -q  # single test

# Lint & type-check
make lint                           # ruff check + pyright
ruff check quantgpt/ tests/         # lint only
pyright quantgpt/                   # type check only
ruff check --fix .                  # auto-fix lint issues

# Frontend
make frontend                       # cd frontend && npm ci && npm run build

# Misc
make clean                          # remove venv, egg-info, cache, quantgpt.db
pip install -e ".[dev]"             # install after pulling new deps
```

## Architecture

QuantGPT is an **agent-driven factor research engine** for quantitative trading. An LLM agent (Claude) drives factor discovery autonomously via MCP tools, while a FastAPI REST API + React web UI provide monitoring.

### High-level layout

```
quantgpt/               # Python backend
  api_server.py         # FastAPI app (lifespan: DB init, APScheduler jobs, MCP session)
  mcp_server.py         # FastMCP server — 12+ tools for LLM agents
  backtest.py           # Group backtest engine (rank-based, async via ProcessPool)
  expression_parser.py  # Recursive-descent parser: cross-section vs time-series ops
  anti_overfit.py       # 4 anti-overfit tests (IC stability, sub-sample, placebo, half-life)
  rolling_validator.py  # Walk-forward validation
  mutation_engine.py    # 8 mutation directions for factor evolution
  crossover_engine.py   # High-score factor crossover recombination
  meta_evolution.py     # Adaptive strategy selection (EXPLOIT/EXPLORE/RECOMBINE/SIMPLIFY)
  trajectory_analyzer.py# Factor quality trajectory analysis
  iteration.py          # Factor scoring & iteration orchestration
  market_data.py        # Multi-source data: Parquet cache → baostock → akshare
  market_regime.py      # Market regime detection
  fundamental_data.py   # Fundamental data enrichment (detect_vars → fetch → merge)
  wq_brain_client.py    # WorldQuant BRAIN API: auth → simulate → submit
  wq_simulate.py        # Dollar-neutral simulation aligned with WQ BRAIN metrics
  neutralization.py     # Industry & market-cap neutralization
  paper_engine.py       # Paper trading (daily settlement, orders, PnL tracking)
  auth.py               # JWT auth, password hashing, verification codes
  db.py                 # SQLAlchemy async engine/session
  models.py             # ORM models (12 tables)
  schemas.py            # Pydantic validators for route parameters
  task_executor.py      # ProcessPoolExecutor for CPU-bound backtest tasks
  task_store.py         # In-memory task state + SSE streaming
  llm_service.py        # DeepSeek/OpenAI-compatible LLM client
  strategy_prompt.py    # LLM prompt templates for strategy generation
  strategy_code_utils.py# LLM-generated code validation
  composite.py          # Multi-factor composite construction
  attribution.py        # Factor attribution analysis
  alpha_tracker.py      # Submitted alpha tracking + auto-correlation pre-check
  email_service.py      # Async SMTP email sender
  weekly_report.py      # Weekly research report generation
  daily_summary.py      # Daily market summary generation
  report.py             # QuantStats HTML report generation
  scheduler_registry.py # APScheduler job registry for observability
  mcp_tracking.py       # MCP tool call tracking & observability
  jq_automation.py      # Playwright-based JoinQuant automation
  factor_signals.py     # Factor signal generation
  industry_analysis.py  # Industry classification analysis
  rust_bridge.py        # Rust engine FFI bridge (optional, via PyO3)
  routes/               # FastAPI route modules (auth, backtest, wq_brain, paper, etc.)
  migrations/           # Alembic migration scripts (11 versions)
engine/                 # Rust implementation (PyO3, optional via QUANTGPT_RUST_ENGINE)
  src/expression/       # Expression parser + AST + eval in Rust
  src/operators/        # Cross-sectional & time-series operators in Rust
  src/backtest/         # Rust backtest engine (grouping, metrics)
frontend/               # React 18 + TypeScript + Vite + Tailwind CSS 4
  src/                  # Components: ResearchDashboard, BacktestForm, etc.
tests/                  # Pytest tests (asyncio, strict mode)
scripts/                # Utility scripts (factor_miner, fetch_wq_alphas, etc.)
docs/                   # Documentation (ARCHITECTURE, API_DOC, MCP_GUIDE, etc.)
```

### Data flow

1. **MCP tools** (`mcp_server.py`) are the primary interface for LLM agents. Each tool fetches market data → computes factor expression → runs backtest → validates → returns structured JSON.
2. **REST API** (`api_server.py` + `routes/`) mirrors key MCP functionality for programmatic access and SPA monitoring.
3. **Backtest engine** (`backtest.py`) is CPU-bound; runs in subprocesses via `ProcessPoolExecutor` to avoid blocking the async event loop.
4. **Optional Rust engine** (`engine/`) can replace the Python expression evaluator and backtest grouping for performance. Enabled via `QUANTGPT_RUST_ENGINE=1` env var; falls back to pure Python when disabled.
5. **Task system**: backtest → store result in DB + `task_store.py` → SSE stream to Web UI.

### Key design patterns

- **API context guard**: `backtest._require_api_context()` prevents direct calls to the backtest engine; all paths go through the task system.
- **Expression parser dual mode**: `mode="local"` for full local computation; `mode="wq"` for WorldQuant BRAIN validation (accepts WQ-only fields/operators but skips execution).
- **Process isolation**: CPU-bound backtest work runs in `ProcessPoolExecutor` via `task_executor.py`. Configurable backend: `process` (default), `thread` (dev/test), `celery` (distributed).
- **Database**: SQLAlchemy 2.0 async ORM with SQLite (default, zero-config) or PostgreSQL. 11 Alembic migrations.
- **3-tier validation**: anti-overfit (4 tests) → walk-forward (rolling windows) → WQ simulation (dollar-neutral alignment).
- **MCP tracking**: all MCP tool calls are instrumented via `track_mcp_result()` for observability.

### Environment

Copy `.env.example` to `.env`. Key variables:
- `AUTH_DISABLED=true` — dev mode (no login, no SMTP)
- `DEEPSEEK_API_KEY` — LLM for strategy generation
- `QUANTGPT_TASK_BACKEND=process` — execution backend
- `QUANTGPT_RUST_ENGINE=1` — enable Rust engine
- `WQ_BRAIN_EMAIL` / `WQ_BRAIN_PASSWORD` — WorldQuant BRAIN integration
- `DATABASE_URL` — leave empty for SQLite, or set PostgreSQL URL
