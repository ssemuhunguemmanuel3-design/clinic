@echo off
cd /d "%~dp0"
if not exist venv (
  echo Creating virtual environment...
  python -m venv venv
)
call venv\Scripts\activate.bat
pip install -r requirements.txt
if not exist .env python setup_env.py
echo.
echo Open http://127.0.0.1:5000  (login: admin)
python app.py
pause
