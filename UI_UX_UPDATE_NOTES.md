# UI/UX refresh notes

This update refreshes the shared Flask/Jinja layout in `app/templates/base.html` while preserving the existing routes, role-based navigation, templates, and database models.

## Changes
- Refined the visual system with neutral surfaces, stronger hierarchy, quieter borders, and consistent spacing.
- Reworked the sidebar to a dark, professional navigation surface with clearer active states.
- Improved cards, KPI tiles, tables, forms, buttons, and focus indicators.
- Added dedicated dark-mode surface and control styling.
- Improved tablet and mobile layouts, including an accessible slide-in navigation drawer with a dismissible backdrop and Escape-key support.
- Added reduced-motion support and safer overflow behavior for narrow devices.
- Kept the existing theme toggle and application structure.

## Run locally (Windows PowerShell)
1. Open this folder in VS Code.
2. Create/activate a virtual environment if needed:
   `python -m venv .venv`
   `.venv\Scripts\Activate.ps1`
3. Install dependencies:
   `pip install -r requirements.txt`
4. Start the app:
   `python run.py`

The Python files passed compilation. Automated tests could not be executed in this environment because Flask is not installed in the runtime; install `requirements.txt` and run `python -m pytest` locally.
