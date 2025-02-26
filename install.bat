@echo off
REM Create a virtual environment named "venv" if it doesn't already exist
if not exist venv (
    python -m venv venv
)

REM Activate the virtual environment
call venv\Scripts\activate

REM Upgrade pip to the latest version
pip install --upgrade pip

REM Install all required packages from requirements.txt
pip install -r requirements.txt

echo.
echo Installation complete.
pause
