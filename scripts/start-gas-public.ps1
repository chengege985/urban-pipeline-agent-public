param(
    [ValidateSet('Backend','Frontend','Check')][string]$Mode = 'Backend',
    [string]$Python = 'python',
    [int]$ApiPort = 18096,
    [int]$WebPort = 5173
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $repo
$engineering = Join-Path $repo 'engineering'
$env:WATER_RUNTIME_MODE = 'local'
$env:WATER_BIND_HOST = '127.0.0.1'
$env:ALLOW_LOCAL_SYNTHETIC_ANONYMOUS = '1'
$env:GAS_PUBLIC_DATA_READONLY = '1'
$env:WATER_CORS_ORIGINS = "http://127.0.0.1:$WebPort"
$env:WATER_GAS_PORT = [string]$ApiPort
$env:VITE_WATERPULSE_PLATFORM_URL = "http://127.0.0.1:$ApiPort"

if ($Mode -eq 'Frontend') {
    Push-Location (Join-Path $repo 'web')
    try { npm.cmd run dev:project -- --port $WebPort --strictPort; exit $LASTEXITCODE }
    finally { Pop-Location }
}

function Set-LocalSource([string]$Name, [string]$Candidate, [string]$Required = '') {
    if ([Environment]::GetEnvironmentVariable($Name, 'Process')) { return }
    if (-not $Candidate) { return }
    if ($Required) { $probe = Join-Path $Candidate $Required } else { $probe = $Candidate }
    if (Test-Path -LiteralPath $probe -PathType Leaf) {
        [Environment]::SetEnvironmentVariable($Name, $Candidate, 'Process')
    }
}

if (-not $env:GASLIB_582_DATA_ROOT) {
    $pin = 'F76BA873F7E1EF9ECE34AED6B70CA619EB7E7B5F39EA9C29236DFD99544F5BF3'
    $file = 'GasLib-582-v2-20211129.net'
    $output = Join-Path $workspace 'outputs'
    foreach ($folder in (Get-ChildItem -LiteralPath $output -Directory -Filter 'public-gaslib-*' -ErrorAction SilentlyContinue)) {
        $candidate = Join-Path $folder.FullName $file
        if ((Test-Path -LiteralPath $candidate -PathType Leaf) -and (Get-FileHash -LiteralPath $candidate).Hash -eq $pin) {
            $env:GASLIB_582_DATA_ROOT = $folder.FullName
            $env:GASLIB_582_NETWORK_FILE = $file
            break
        }
    }
}
Set-LocalSource 'GAS_AER_DATA_ROOT' (Join-Path $workspace 'aer_data_20261119/data/processed/aer') 'manifest.json'
Set-LocalSource 'GAS_NGN_DATA_ROOT' (Join-Path $workspace 'outputs/ngn-the-north/snapshot') 'manifest.json'
Set-LocalSource 'SCHUTTERWALD_MODEL_FILE' (Join-Path $engineering 'outputs/research/model.json')
Set-LocalSource 'SCHUTTERWALD_SOLVER_PYTHON' (Join-Path $env:LOCALAPPDATA 'Temp/gas-schutterwald-012/Scripts/python.exe')
Set-LocalSource 'SCHUTTERWALD_SOLVER_PYTHON' (Join-Path $repo '.venv-schutterwald/Scripts/python.exe')
Set-LocalSource 'NGN_SAMPLE_DATA_ROOT' (Join-Path $engineering 'outputs/research/ngn-sample') 'revision.txt'
Set-LocalSource 'NGN_SAMPLE_SOLVER_PYTHON' $env:SCHUTTERWALD_SOLVER_PYTHON
Set-LocalSource 'GAS_SICILY_DATA_ROOT' (Join-Path $engineering 'outputs/research/sicily-snapshot') 'manifest.json'
if (-not $env:SICILY_SOLVER_PYTHON) {
    Set-LocalSource 'SICILY_SOLVER_PYTHON' (Join-Path $env:LOCALAPPDATA 'Temp/gas-sicily-venv-4c3bf/Scripts/python.exe')
    if ($env:SICILY_SOLVER_PYTHON) { $env:SICILY_SOLVER_REVISION = '6079ec1841f6167b22c774abb6df24a768f22541' }
}

Push-Location $engineering
try {
    if ($Mode -eq 'Check') {
        & $Python -c "from src.gas_application.public_datasets import dataset_registry; [print(x['dataset_id'], x['status'], x.get('data_version') or '-') for x in dataset_registry()['datasets']]"
    } else { & $Python -m src.gas_application }
    exit $LASTEXITCODE
} finally { Pop-Location }
