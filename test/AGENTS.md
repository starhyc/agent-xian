# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python contest-agent workspace. The runtime entrypoint is `source/main.py`, launched by `start.sh`. Contestant-editable code lives under `source/solution/`, especially `contestant_agent.py`, `mcp/contestant_tools.py`, `skills/<skill_name>/`, and `agents/<agent_name>/`. Shared runtime helpers are in `source/runtime/`, while sample inputs are in `source/examples/`. Root-level `question.json`, `question_disc.json`, and `customer_date_messages.txt` are local task data. Generated answer files are written under `source/outputs/`.

## Build, Test, and Development Commands

- `python3 -m pip install -r requirements.txt`: install declared Python dependencies.
- `bash start.sh source/examples/questions.json source/outputs/result.json`: run the demo batch locally.
- `python3 -m source.main --question source/examples/questions.json --output source/outputs/result.json`: run the Python entrypoint directly.
- `python3 -m compileall source`: quick syntax check for all repository Python modules.

`start.sh` accepts `question_path`, `result_path`, and optional `package_id`; keep that interface stable for platform execution.

## Coding Style & Naming Conventions

Use Python 3 with 4-space indentation, type hints where they clarify interfaces, and `from __future__ import annotations` in new Python modules when forward references are useful. Follow existing naming: snake_case for modules, functions, variables, and skill or agent package directories; PascalCase for classes such as `ContestantAgent`. Keep public entrypoints and JSON schema keys stable.

## Testing Guidelines

There is no dedicated test suite in this repo. Before submitting changes, run `python3 -m compileall source` and at least one representative local run through `start.sh` when model credentials and environment variables are configured. Add focused sample questions under `source/examples/` only when they are useful for repeatable validation.

## Commit & Pull Request Guidelines

Recent history uses short, direct commit subjects, including Chinese summaries such as `任务书` and concise English subjects like `init`. Keep commits focused and avoid bundling unrelated agent, skill, and runtime changes. Pull requests should describe the behavior change, list validation commands run, note any environment variables required, and include sample output or screenshots only when they help reviewers verify the result.

## Security & Configuration Tips

Do not commit secrets. Keep credentials in `.env` or the execution environment, and avoid printing API keys, package IDs, or model gateway tokens in logs. When adding tools, skills, or sub-agents, validate file access against the question-provided `files` scope.
