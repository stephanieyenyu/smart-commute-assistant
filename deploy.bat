@echo off
echo ===== Applying Alembic Migration =====
cd /d C:\Users\steph\smart-commute-assistant\backend
python -m alembic upgrade head
echo Migration exit code: %ERRORLEVEL%

echo.
echo ===== Git Add + Commit + Push =====
cd /d C:\Users\steph\smart-commute-assistant
git add -A
git commit -m "feat: 5-point core fix - Flex summary, voice WebSocket, family dashboard, sleep/wake, 1s updates"
git push
echo Push exit code: %ERRORLEVEL%
echo.
echo ===== DONE =====
pause
