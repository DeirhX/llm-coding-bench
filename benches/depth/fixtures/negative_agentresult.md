# Code Smell and Duplication Improvements Plan

## Context
The codebase currently exhibits several "code smells" related to type safety, configuration management, and duplication of result normalization across different LLM agent implementations (Ollama and Cursor). 

Key issues identified:
1. **Lack of Type Safety for Agent Results**: Agents return plain dictionaries with keys like `"content"`, `"thinking"`, and `"wall_s"`. This leads to "magic string" usage throughout the codebase, making it prone to typos and harder to maintain.
2. **Scattered Configuration**: Environment variables are accessed via `os.environ.get` in over 100 locations across multiple files (`ollama_chat.py`, `ollama_think.py`, `cursor_cli.py`, `task_timeout.py`, etc.). This makes it difficult to track all available configuration options and their defaults.
3. **Duplicated Result Normalization**: Each agent implementation manually constructs the result dictionary, leading to duplication of logic for calculating metrics like `toks_per_s`.

The goal is to introduce a structured type system for agent results and a centralized configuration management system to improve maintainability and robustness.

## Proposed Approach

### 1. Introduce Type Safety with `AgentResult`
I will create a new file `bench_lib/types.py` to house shared data structures.
- Define an `AgentResult` dataclass that explicitly defines all fields returned by agents (e.g., `content`, `thinking`, `wall_s`, `prompt_tokens`, `eval_tokens`, `done_reason`, etc.).
- This will replace the use of dictionaries for agent responses across the entire project.

### 2. Centralize Configuration Management
I will introduce a centralized configuration system in `bench_lib/config.py`.
- Create a `Config` class (or a set of typed getters) that encapsulates all `os.environ.get` calls.
- Provide clear defaults and type conversion (e.g., converting `"1"` to `True`, or `"600"` to `float`) in one place.
- This will eliminate the scattered use of `os.environ.get` throughout the library.

### 3. Refactor Agent Implementations
I will update the agent backends to use these new systems:
- **`bench_lib/ollama_chat.py`**: Update `chat()` to return an `AgentResult` object and use `Config` for environment variables.
- **`bench_lib/cursor_cli.py`**: Update `chat()` to return an `AgentResult` object and use `Config` for environment variables.

### 4. Update Callers and Consumers
I will update all code that consumes agent results to use attribute access instead of dictionary keys:
- **Benchmark files**: Update `benches/repohard/bench.py`, `benches/audittrap/bench.py`, `benches/arch/bench.py`, and `benches/claim/bench.py`.
- **Scripts**: Update `scripts/run_agent_benches_openai.py` (if applicable).
- **Runner**: Update `bench_lib/bench_runner.py` if it interacts with the result structure directly.

## Critical Files to Modify
- `bench_lib/types.py` (New file)
- `bench_lib/config.py` (New file)
- `bench_lib/ollama_chat.py`
- `bench_lib/ollama_think.py`
- `bench_lib/cursor_cli.py`
- `bench_lib/task_timeout.py`
- `benches/repohard/bench.py`
- `benches/audittrap/bench.py`
- `benches/arch/bench.py`
- `benches/claim/bench.py`

## Verification Plan
1. **Static Analysis**: Ensure no remaining `os.environ.get` calls exist in the core library (except perhaps in the new `config.py`).
2. **Smoke Tests**: Run a small subset of tasks for both Ollama and Cursor agents to ensure that results are still being captured and reported correctly.
3. **Report Verification**: Run `report.py` to verify that summaries and leaderboards are still generated accurately from the updated result format (since they read from JSON files, I must ensure the serialization of `AgentResult` remains compatible with existing JSON schemas).
