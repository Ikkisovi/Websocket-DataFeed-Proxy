function Import-EnvFromFile {
    param([string]$Path)
    $lines = Get-Content -Path $Path
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith("#")) { continue }
        if ($trimmed -match '^\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["'']?(.*?)["'']?\s*$') {
            $name = $Matches[1]
            $value = $Matches[2]
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
            Write-Host "Loaded $name"
        }
    }
}

$scriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$repoRoot = Resolve-Path (Join-Path $scriptRoot "..\..")
$secretsPath = if ($env:SECRETS_PATH) { $env:SECRETS_PATH } else { Join-Path $repoRoot "secrets.local.txt" }
if (Test-Path $secretsPath) {
    Import-EnvFromFile -Path $secretsPath
} else {
    Write-Warning "secrets.local.txt not found!"
}

# Map API keys to MASTER keys if not set
if (-not $env:ALPACA_MASTER_KEY -and $env:ALPACA_API_KEY) {
    $env:ALPACA_MASTER_KEY = $env:ALPACA_API_KEY
    Write-Host "Mapped ALPACA_API_KEY to ALPACA_MASTER_KEY"
}
if (-not $env:ALPACA_MASTER_SECRET -and $env:ALPACA_API_SECRET) {
    $env:ALPACA_MASTER_SECRET = $env:ALPACA_API_SECRET
    Write-Host "Mapped ALPACA_API_SECRET to ALPACA_MASTER_SECRET"
}
# Force token to test_proxy (user requirement)
$env:ALPACA_PROXY_TOKEN = "test_proxy"
Write-Host "Set ALPACA_PROXY_TOKEN=test_proxy"

# Restart services
$projectName = "cloud-center"
if (Test-Path Env:COMPOSE_PROJECT_NAME) {
    Remove-Item Env:COMPOSE_PROJECT_NAME
}
$composeFile = Join-Path $scriptRoot "docker-compose.cloud-proxy.yml"
$dockerfile = Join-Path $scriptRoot "Dockerfile.cloud-proxy"
Set-Location -Path $scriptRoot
$composeCmd = Get-Command docker-compose -ErrorAction SilentlyContinue
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $composeCmd -and -not $dockerCmd) {
    Write-Error "Docker is not installed or not on PATH."
    exit 1
}
if ($composeCmd) {
    & docker-compose -f $composeFile down
} else {
    & docker compose -f $composeFile down
}
$env:COMPOSE_PROJECT_NAME = $projectName
Write-Host "Using COMPOSE_PROJECT_NAME=$projectName"
$imageName = "$projectName-alpaca-cloud-proxy"
& docker build -f $dockerfile -t $imageName $scriptRoot
if ($composeCmd) {
    & docker-compose -f $composeFile up -d --no-build
} else {
    & docker compose -f $composeFile up -d --no-build
}
