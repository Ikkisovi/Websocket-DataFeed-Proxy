$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$dockerfile = Join-Path $scriptRoot "Dockerfile.alpaca-proxy"

docker build -t alpaca-proxy-agent:livefix -f $dockerfile $scriptRoot
if ($LASTEXITCODE -ne 0) {
    throw "docker build failed."
}

Write-Host "Build complete."
Write-Host ""
Write-Host "Enhanced proxy with WebSocket (8765) + HTTP history (8766) support."
Write-Host ""
Write-Host "Run (live + SIP):"
Write-Host "docker run --rm -it -p 8765:8765 -p 8766:8766 -e IS_LIVE=true -e IS_PRO=true alpaca-proxy-agent:livefix"
Write-Host "Run (paper + IEX):"
Write-Host "docker run --rm -it -p 8765:8765 -p 8766:8766 -e IS_LIVE=false -e IS_PRO=false alpaca-proxy-agent:livefix"
Write-Host ""
Write-Host "Run (relay mode -> cloud):"
Write-Host "docker run --rm -it -p 8765:8765 -p 8766:8766 -e MODE=relay -e CLOUD_PROXY_URL=wss://your-cloud/stream -e CLOUD_HISTORY_URL=https://your-cloud/v1/history/bars -e ALPACA_PROXY_TOKEN=YOUR_TOKEN alpaca-proxy-agent:livefix"
