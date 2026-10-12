@echo off
rem Build the Windows worker package.  Run on a Windows PC that has Python 3.10-3.12 installed.
rem Result: dist\court_worker\   (zip that WHOLE folder and give it to the operators)
rem NOTE: untested packaging recipe. SeleniumBase + PyInstaller can need tweaks - send me any error text.
rem We build a FOLDER (--onedir), not one single .exe, so the downloaded Chrome driver persists between runs.

python -m venv .venv-build
call .venv-build\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm --clean --onedir --name court_worker ^
  --collect-all seleniumbase ^
  --collect-all selenium ^
  worker_main.py

copy .env.worker.example dist\court_worker\.env.example
copy WORKER_README.md dist\court_worker\README.md
echo.
echo Built: dist\court_worker\court_worker.exe
echo Test it on a clean PC (with Google Chrome installed) before handing it out.
pause
