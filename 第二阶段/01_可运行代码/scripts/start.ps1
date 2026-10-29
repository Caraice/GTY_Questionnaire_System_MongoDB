param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'First run: python -m venv .venv, then .venv\Scripts\python.exe -m pip install -r requirements-lock.txt. See README.md.'
}
if (-not $env:MONGODB_URI) { $env:MONGODB_URI = 'mongodb://127.0.0.1:27019' }
if (-not $env:MONGODB_DB) { $env:MONGODB_DB = 'survey_stage1' }
if (-not $env:HOST) { $env:HOST = '127.0.0.1' }
if (-not $env:PORT) { $env:PORT = '8000' }
& $pythonExe -c "import fastapi, uvicorn, pymongo; print('Python dependencies ready.')"
if ($LASTEXITCODE -ne 0) { throw 'Install requirements-lock.txt into this directory''s .venv first.' }
Push-Location -LiteralPath $projectRoot
try {
    & $pythonExe scripts/check_db.py
    if ($LASTEXITCODE -ne 0) {
        if ($env:MONGODB_URI -ne 'mongodb://127.0.0.1:27019') {
            throw 'Configured MongoDB URI is unavailable. Start that server first.'
        }
        $mongoExe = $env:MONGOD_EXE
        if (-not $mongoExe) {
            # The submitted copy can be nested more deeply than the development checkout.
            $candidateRoot = $projectRoot
            while ($candidateRoot) {
                $candidateExe = Join-Path $candidateRoot 'lab2\.lab2\mongodb-win32-x86_64-windows-8.0.17\bin\mongod.exe'
                if (Test-Path -LiteralPath $candidateExe) { $mongoExe = $candidateExe; break }
                $candidateRoot = Split-Path -Parent $candidateRoot
            }
        }
        if (-not $mongoExe -or -not (Test-Path -LiteralPath $mongoExe)) {
            throw 'MongoDB binary unavailable. Set MONGOD_EXE to your mongod.exe path.'
        }
        $runtimePath = Join-Path $projectRoot '.runtime'
        $dataPath = Join-Path $runtimePath 'mongo-data'
        $logPath = Join-Path $runtimePath 'mongod.log'
        New-Item -ItemType Directory -Force -Path $dataPath | Out-Null
        Start-Process -FilePath $mongoExe -ArgumentList @('--dbpath', ('"' + $dataPath + '"'), '--logpath', ('"' + $logPath + '"'), '--logappend', '--bind_ip', '127.0.0.1', '--port', '27019', '--wiredTigerCacheSizeGB', '0.25') -WindowStyle Hidden
        $ready = $false
        for ($attempt = 0; $attempt -lt 15; $attempt++) {
            Start-Sleep -Seconds 1
            & $pythonExe scripts/check_db.py
            if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        }
        if (-not $ready) { throw 'MongoDB failed to start. Inspect .runtime/mongod.log.' }
    }
    & $pythonExe -m scripts.init_db
    if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed.' }
    if ($CheckOnly) { return }
    $existingHealth = $null
    try {
        $existingHealth = Invoke-RestMethod -Uri "http://$($env:HOST):$($env:PORT)/health" -TimeoutSec 2
    } catch { }
    if ($existingHealth -and $existingHealth.stage -eq 'framework' -and $existingHealth.database -eq $env:MONGODB_DB) {
        if ($existingHealth.version -ne '0.11.0') {
            throw "Existing project API is version $($existingHealth.version). Stop that API and run Start.cmd again to load 0.11.0. Keep MongoDB and its data directory."
        }
        Write-Host "Survey workspace already running: http://$($env:HOST):$($env:PORT)"
        return
    }
    Write-Host "Survey workspace: http://$($env:HOST):$($env:PORT)"
    & $pythonExe -m uvicorn app.main:app --host $env:HOST --port $env:PORT
} finally { Pop-Location }

