@echo off
REM Start AvigilonDash on http://localhost:8000
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\pip install -q -r requirements.txt
)
echo AvigilonDash running at http://localhost:8000
start "" http://localhost:8000
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
