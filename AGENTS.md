# Repository Guidelines

## Project Structure

This is a Flask and SQLAlchemy school management system. `run.py` starts the app; `app/__init__.py` builds it. Put database models in `app/models/`, route and page logic in `app/routes/`, reusable business logic in `app/services/`, shared helpers and access control in `app/utils/`, Jinja templates in `app/templates/`, and styles, scripts, and images in `app/static/`. Schema migrations belong in `migrations/`. Tests and fixtures are in `tests/`.

## Setup and Development

Use a virtual environment and install dependencies before running locally:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Run the test suite with `python -m pytest`. Tests use an in-memory SQLite database through `tests/conftest.py`. There is no separate frontend build step or configured formatter/linter.

## Code Style and Organization

Follow the existing Python style: four-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and descriptive route/service names. Keep route handlers focused on HTTP input and responses; place reusable business rules in services or utilities. Use SQLAlchemy models for persistence and Jinja templates for HTML. Preserve existing database compatibility; make schema changes through migrations rather than editing a live database directly.

## Tests

Add pytest tests under `tests/`, naming files `test_*.py` and test functions `test_*`. Cover role permissions and data-access boundaries when changing routes, and use the existing fixtures instead of connecting to a local school database. No coverage threshold is configured.

## Commits and Pull Requests

Use concise, imperative commit subjects (for example, `Fix teacher class assignment scope`). Pull requests should explain the user-visible change, note any migration or configuration impact, list verification performed, and include screenshots for UI changes. Link related issues when available.

## Configuration and Security

Keep secrets and local settings in `.env`; use `.env.example` for documented placeholders. Never commit real credentials, local database files, or virtual environments. The README’s bootstrap admin credentials are for development only and must be changed immediately.
