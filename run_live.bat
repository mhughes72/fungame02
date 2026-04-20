@echo off
for /f "tokens=*" %%i in ('python -c "import socket; s=socket.socket(); s.connect((\"8.8.8.8\",80)); print(s.getsockname()[0]); s.close()"') do set LOCAL_IP=%%i

echo.
echo  =========================================
echo   AI Driven Haunted Mansion - Web Server
echo  =========================================
echo.
echo   Local:    http://localhost:8765
echo   Network:  http://%LOCAL_IP%:8765
echo.
echo   Press Ctrl+C to stop
echo.

set PYTHONUTF8=1
uvicorn server:fastapi_app --port 8765
