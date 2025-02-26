@echo off
cd /d C:\Users\joelx\Desktop\Recipe_app
echo Starting Flask app...
start cmd /k "python app.py"
timeout /t 5
echo Opening Force Sync in browser...
start "" http://127.0.0.1:5000/force_sync
