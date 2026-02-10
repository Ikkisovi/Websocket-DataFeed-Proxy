param(
    [string]$SourceRoot = $PSScriptRoot,
    [string]$OutputDirName = "cloud-proxy",
    [string]$SecretsPath = "secrets.local.txt",
    [string]$ProxyToken = "test_proxy"
)

$scriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$repoRoot = Resolve-Path (Join-Path $scriptRoot "..\..")
$sourceRoot = (Resolve-Path -Path $SourceRoot).Path
$secretsFull = if ([System.IO.Path]::IsPathRooted($SecretsPath)) { $SecretsPath } else { Join-Path $repoRoot $SecretsPath }
$outputDir = if ([System.IO.Path]::IsPathRooted($OutputDirName)) { $OutputDirName } else { Join-Path $repoRoot $OutputDirName }

if (-not (Test-Path -Path $secretsFull)) {
    Write-Error "Missing secrets file: $secretsFull"
    exit 1
}

$requiredFiles = @(
    "Dockerfile.cloud-proxy",
    "docker-compose.cloud-proxy.yml",
    "alpaca_cloud_proxy.py"
)

foreach ($file in $requiredFiles) {
    $path = Join-Path $sourceRoot $file
    if (-not (Test-Path -Path $path)) {
        Write-Error "Missing required file: $path"
        exit 1
    }
}

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

foreach ($file in $requiredFiles) {
    Copy-Item -Path (Join-Path $sourceRoot $file) -Destination $outputDir -Force
}

$envMap = @{}
$lines = Get-Content -Path $secretsFull
foreach ($line in $lines) {
    $trimmed = $line.Trim()
    if (-not $trimmed) { continue }
    if ($trimmed.StartsWith("#")) { continue }

    if ($trimmed -match '^\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|''([^'']*)''|(.*))\s*$') {
        $name = $Matches[1]
        $value = if ($Matches[2]) { $Matches[2] } elseif ($Matches[3]) { $Matches[3] } else { $Matches[4] }
        $envMap[$name] = $value
        continue
    }

    if ($trimmed -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|''([^'']*)''|(.*))\s*$') {
        $name = $Matches[1]
        $value = if ($Matches[2]) { $Matches[2] } elseif ($Matches[3]) { $Matches[3] } else { $Matches[4] }
        $envMap[$name] = $value
        continue
    }
}

if (-not $envMap.ContainsKey("ALPACA_MASTER_KEY")) {
    if ($envMap.ContainsKey("ALPACA_API_KEY")) {
        $envMap["ALPACA_MASTER_KEY"] = $envMap["ALPACA_API_KEY"]
    } elseif ($envMap.ContainsKey("APCA_API_KEY_ID")) {
        $envMap["ALPACA_MASTER_KEY"] = $envMap["APCA_API_KEY_ID"]
    }
}

if (-not $envMap.ContainsKey("ALPACA_MASTER_SECRET")) {
    if ($envMap.ContainsKey("ALPACA_API_SECRET")) {
        $envMap["ALPACA_MASTER_SECRET"] = $envMap["ALPACA_API_SECRET"]
    } elseif ($envMap.ContainsKey("APCA_API_SECRET_KEY")) {
        $envMap["ALPACA_MASTER_SECRET"] = $envMap["APCA_API_SECRET_KEY"]
    }
}

if (-not $envMap.ContainsKey("ALPACA_PROXY_TOKEN") -and $ProxyToken) {
    $envMap["ALPACA_PROXY_TOKEN"] = $ProxyToken
}

$preferred = @("ALPACA_MASTER_KEY", "ALPACA_MASTER_SECRET", "ALPACA_PROXY_TOKEN")
$orderedKeys = @()
foreach ($key in $preferred) {
    if ($envMap.ContainsKey($key)) { $orderedKeys += $key }
}
foreach ($key in ($envMap.Keys | Sort-Object)) {
    if ($orderedKeys -notcontains $key) { $orderedKeys += $key }
}

$envLines = foreach ($key in $orderedKeys) {
    "$key=$($envMap[$key])"
}

$envPath = Join-Path $outputDir ".env"
Set-Content -Path $envPath -Value $envLines -Encoding ASCII

Write-Host "Prepared: $outputDir"
Write-Host "Wrote .env with keys: $($orderedKeys -join ', ')"
