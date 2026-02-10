param(
    [string]$Image = "alpaca-proxy-agent:livefix",
    [string]$CloudProxyUrl = $env:CLOUD_PROXY_URL,
    [string]$CloudHistoryUrl = $env:CLOUD_HISTORY_URL,
    [string]$ProxyToken = $env:ALPACA_PROXY_TOKEN,
    [int]$Port = 8765
)

function Get-HistoryUrlFromProxy {
    param([string]$ProxyUrl)
    if (-not $ProxyUrl) {
        return $null
    }
    try {
        $uri = [Uri]$ProxyUrl
        $scheme = if ($uri.Scheme -eq "wss") { "https" } else { "http" }
        $port = if ($uri.Port -eq 8765) { 8766 } else { $uri.Port }
        $builder = New-Object System.UriBuilder($uri)
        $builder.Scheme = $scheme
        $builder.Port = $port
        $builder.Path = "/v1/history/bars"
        $builder.Query = ""
        return $builder.Uri.AbsoluteUri
    }
    catch {
        return $null
    }
}

if (-not $CloudProxyUrl) {
    Write-Error "CLOUD_PROXY_URL is required."
    exit 1
}

if (-not $CloudHistoryUrl) {
    $CloudHistoryUrl = Get-HistoryUrlFromProxy -ProxyUrl $CloudProxyUrl
}

if (-not $ProxyToken) {
    $ProxyToken = Read-Host "ALPACA_PROXY_TOKEN"
}

$name = "alpaca-local-relay"
$httpPort = $Port + 1

$existing = docker ps -a --filter "name=^${name}$" --format "{{.ID}}"
if ($existing) {
    docker rm -f $name | Out-Null
}

Write-Host "Starting local relay $name -> $CloudProxyUrl"
docker run -d --name $name `
    -p "${Port}:8765" -p "${httpPort}:8766" `
    -e MODE=relay `
    -e CLOUD_PROXY_URL="$CloudProxyUrl" `
    -e CLOUD_HISTORY_URL="$CloudHistoryUrl" `
    -e ALPACA_PROXY_TOKEN="$ProxyToken" `
    $Image | Out-Null

Write-Host "Relay running. ALPACA_PROXY_URL=ws://localhost:${Port}/stream"
Write-Host "History URL: http://localhost:${httpPort}/v1/history/bars"
