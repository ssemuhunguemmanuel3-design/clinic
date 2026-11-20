CLINIC APP (Python Flask + MongoDB Atlas)
1. Install Python from python.org (tick "Add Python to PATH").
2. Double-click run.bat (or in PowerShell: .\run.bat)
3. Paste your Atlas connection string when asked.
4. Open http://127.0.0.1:5000 and log in as admin.
Deploy: push to GitHub, create a Render Web Service,
Build: pip install -r requirements.txt   Start: gunicorn app:app
Env vars: MONGO_URI, DB_NAME, SECRET_KEY, ADMIN_PASSWORD, CLINIC_NAME
