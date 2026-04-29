@echo off
echo Starting ngrok with static domain: leggings-jolliness-riding.ngrok-free.dev
echo Forwarding to localhost:8000
.\ngrok http --domain=leggings-jolliness-riding.ngrok-free.dev 8000
pause
