param([string]$PythonExe = '', [string]$NodeExe = 'node')
$ErrorActionPreference = 'Stop'
$testRoot = $PSScriptRoot
$codeRoot = Join-Path (Split-Path -Parent $testRoot) '01_可运行代码'
if (-not $PythonExe) { $PythonExe = Join-Path $codeRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonExe)) { throw '先按01_可运行代码/配置说明.md创建.venv并安装依赖，或传入-PythonExe。' }
if (-not (Get-Command $NodeExe -ErrorAction SilentlyContinue)) { throw '找不到Node.js，请安装或传入-NodeExe。' }
$outputRoot = Join-Path $testRoot '测试输出'
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$env:RUN_MONGO_TESTS = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:MONGODB_URI) { $env:MONGODB_URI = 'mongodb://127.0.0.1:27019' }
Push-Location $testRoot
try {
    $junitPath = Join-Path $outputRoot 'Python测试.xml'
    & $PythonExe -B -X utf8 -m pytest -q -s -p no:cacheprovider "--junitxml=$junitPath" 2>&1 | Tee-Object -Variable pythonOutput
    $pythonExit = $LASTEXITCODE
    $pythonOutput | Out-File -LiteralPath (Join-Path $outputRoot 'Python测试.log') -Encoding utf8
    $jsTests = @(Get-ChildItem -LiteralPath (Join-Path $testRoot '自动化测试') -Filter '*.mjs' -File | Sort-Object Name | ForEach-Object { $_.FullName })
    & $NodeExe --test @jsTests 2>&1 | Tee-Object -Variable nodeOutput
    $nodeExit = $LASTEXITCODE
    $nodeOutput | Out-File -LiteralPath (Join-Path $outputRoot 'Node测试.log') -Encoding utf8
    if ($pythonExit -ne 0 -or $nodeExit -ne 0) { throw "测试失败：Python=$pythonExit Node=$nodeExit，请检查测试输出。" }
    Write-Host 'Python与Node命令均成功。请检查XML中无失败且无跳过，再核对测试结果.md。'
} finally { Pop-Location }
