# digital-me 桌宠启动（Windows PowerShell）
# 用法： powershell -ExecutionPolicy Bypass -File .\run_windows.ps1
# 说明：Windows 侧用 Edge/Chrome 的 --app 模式开窗口（无地址栏）。
#       --app 窗口做不到「透明 + 常驻最前」，要那两样得加 pywebview（需你点头再加这一个依赖）。

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# ---------- 1. Python + pyyaml ----------
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
if (-not $py) { Write-Host "✗ 没装 Python。装 Python 3.11+ 后重跑"; exit 1 }

& $py.Source -3 -c "import yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "安装 pyyaml（一次性）…"
  & $py.Source -3 -m pip install --user pyyaml
}

# ---------- 2. 起服务 ----------
$health = "000"
try { $health = (Invoke-WebRequest "http://127.0.0.1:8765/api/health" -UseBasicParsing -TimeoutSec 2).StatusCode } catch { $health = "000" }

if ($health -eq "000") {
  New-Item -ItemType Directory -Force -Path logs | Out-Null
  Start-Process -FilePath $py.Source -ArgumentList "-3","server.py","--port","8765" `
    -RedirectStandardOutput "logs\server.log" -RedirectStandardError "logs\server.err" -WindowStyle Hidden
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 250
    try { $health = (Invoke-WebRequest "http://127.0.0.1:8765/api/health" -UseBasicParsing -TimeoutSec 2).StatusCode } catch { $health = "000" }
    if ($health -ne "000") { break }
  }
}

if ($health -eq "200") { Write-Host "✓ 服务 + 模型就绪  http://127.0.0.1:8765" }
elseif ($health -eq "503") { Write-Host "! 服务起来了，但 ollama 或模型没就绪，看 http://127.0.0.1:8765/api/health" }
else { Write-Host "✗ 服务没起来，看 logs\server.log"; exit 1 }

# ---------- 3. 开窗口 ----------
$candidates = @(
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { Write-Host "✗ 没找到 Edge/Chrome，先用浏览器打开 http://127.0.0.1:8765/"; exit 1 }

Write-Host "启动桌宠窗口（关窗口即退出）"
Start-Process -FilePath $browser -ArgumentList "--app=http://127.0.0.1:8765/","--window-size=400,640"
