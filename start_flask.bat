@echo off
call venv\Scripts\activate
python app.py
echo Opening Force Sync in browser...
start "" http://127.0.0.1:5000/force_sync
