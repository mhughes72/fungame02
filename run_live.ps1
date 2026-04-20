$localIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' } | Select-Object -First 1).IPAddress

Write-Host ""
Write-Host " =========================================" -ForegroundColor DarkYellow
Write-Host "  AI Driven Haunted Mansion - Web Server" -ForegroundColor DarkYellow
Write-Host " =========================================" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Local:    http://localhost:8765" -ForegroundColor Cyan
Write-Host "  Network:  http://${localIp}:8765" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Press Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

$env:PYTHONUTF8 = "1"
uvicorn server:fastapi_app --port 8765
