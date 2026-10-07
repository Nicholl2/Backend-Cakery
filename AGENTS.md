# Repository Guidelines

## Project Structure & Module Organization

This repository contains the Toti Cakery FastAPI backend. Application code lives in `app/`: `api/routes/` defines HTTP endpoints, while `core/`, `models/`, `schemas/`, `repositories/`, and `services/` hold configuration/security, database entities, request/response models, persistence access, and business logic. Shared helpers are in `utils/`. Database migrations are under `alembic/versions/`; static uploads/assets are in `static/`; automated tests are in `tests/`. The `api/` package provides deployment entry points. Keep new code in the matching layer and add or update tests alongside behavior changes.

## Build, Test, and Development Commands

- `python3 -m venv venv && source venv/bin/activate`: create and activate a local environment.
- `pip install -r requirements.txt`: install pinned dependencies.
- `uvicorn app.main:app --reload`: run the API locally at `http://127.0.0.1:8000`.
- `pytest`: run the full suite; `pytest tests/test_auth_security.py` runs one module.
- `alembic upgrade head`: apply database migrations; `python -m app.seed_data` seeds initial master data when needed.

Configure local settings in `.env` using `.env.example` as a reference. Do not commit credentials or production secrets.

## Coding Style & Naming Conventions

Use Python 3.12+ conventions, four spaces for indentation, and clear type hints for public functions and async boundaries. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and uppercase names for constants. Keep route handlers focused on HTTP concerns and put reusable business rules in services. Match surrounding SQLAlchemy async and Pydantic patterns. No formatter or linter configuration is present, so keep changes consistent with nearby files.

## Testing Guidelines

Tests use pytest and pytest-asyncio, configured in `pytest.ini` with automatic asyncio mode. Name files `test_<area>.py` and test functions `test_<behavior>`. Cover success paths, validation, and relevant authorization or failure cases. Run `pytest` before submitting changes; database or external service behavior should use the existing fixtures/mocks in `tests/conftest.py`.

## Commit & Pull Request Guidelines

Recent history uses short, imperative Conventional Commit-style subjects, such as `feat: ...`, `feat(order,payment): ...`, and `Merge pull request ...`. Follow the `type(scope): summary` pattern where useful. Pull requests should explain the behavior change, note migration or configuration impacts, link related work, and include test results. Add API examples or screenshots when they clarify externally visible changes.

## Security & Configuration

Keep `.env` local and use `.env.example` for documenting required variables. Never place API keys, passwords, tokens, or real customer data in source, tests, commits, or logs. Review authentication, RBAC, and input validation when changing API routes.
