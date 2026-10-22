# Lycée de Muhura Smart School System

Refactored Flask/Python structure based on the existing working application.

## Run

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Default bootstrap admin (development only): `admin` / `admin1234`. Change it immediately.

## Architecture
- `app/models`: SQLAlchemy data models
- `app/routes`: HTTP/UI routes
- `app/services`: business-logic seams for RFID, attendance and notifications
- `app/templates`: Jinja templates
- `app/static`: static assets
- `tests`: pytest tests

This first refactor intentionally preserves the existing database schema. The next phase should introduce RFIDCard/RFIDScan/RFIDDevice through migrations rather than silently breaking existing data.
"# smart-management-school" 
