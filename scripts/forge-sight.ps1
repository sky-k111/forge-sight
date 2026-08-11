#Requires -Version 5.1
<#
.SYNOPSIS
  ForgeSight 一键启动：数据库(Docker) + 后端(uvicorn) + 前端(Vite)，每个服务一个独立窗口。

.EXAMPLE
  .\forge-sight.ps1              # 完整启动（默认：数据库 + 后端 + 前端）
  .\forge-sight.ps1 -Backend     # 只启后端
  .\forge-sight.ps1 -Frontend    # 只启前端
  .\forge-sight.ps1 -Db          # 只启数据库
  .\forge-sight.ps1 -SkipDb      # 数据库已在运行，跳过（只启后端+前端）
  .\forge-sight.ps1 -Stop        # 停止后端与前端（数据库容器保留，数据不丢）
#>
param(
  [switch]$All,
  [switch]$Backend,
  [switch]$Frontend,
  [switch]$Db,
  [switch]$SkipDb,
  [switch]$Stop
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$StateDir = Join-Path $HOME ".forge-sight"
$StateFile = Join-Path $StateDir "services.json"
$Shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }

function Test-Port([int]$Port, [int]$TimeoutMs = 800) {
  try {
    $client = [System.Net.Sockets.TcpClient]::new()
    $task = $client.ConnectAsync("127.0.0.1", $Port)
    if ($task.Wait($TimeoutMs)) { $client.Dispose(); return $true }
    $client.Dispose(); return $false
  } catch { return $false }
}

function Wait-Port([int]$Port, [int]$TimeoutSec) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  do {
    if (Test-Port $Port) { return $true }
    Start-Sleep -Milliseconds 800
  } while ((Get-Date) -lt $deadline)
  return $false
}

# 开一个独立窗口跑服务（窗口标题便于识别，/T 杀进程树时一起收掉）
function Start-Window([string]$Title, [string]$WorkDir, [string]$Command) {
  $inner = "& { `$Host.UI.RawUI.WindowTitle='$Title'; Set-Location '$WorkDir'; $Command }"
  $proc = Start-Process $Shell -ArgumentList "-NoExit", "-Command", $inner -PassThru
  return $proc.Id
}

function Save-State($svc) {
  if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
  ($svc | ConvertTo-Json) | Set-Content -Path $StateFile -Encoding UTF8
}

function Start-Database {
  Write-Host "[1/3] 启动数据库 (docker) ..." -ForegroundColor Cyan
  Push-Location $Root
  docker compose up -d 2>&1 | Out-Host
  if ($LASTEXITCODE -ne 0) {
    # 容器名已被旧项目占用时回退：直接启动已存在的 stopped 容器（数据保留，不重建）
    Write-Warning "docker compose 创建失败，尝试直接启动已有容器（数据保留）..."
    docker start forgesight-mysql forgesight-postgres 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "数据库启动失败：请检查 docker 状态后重试（docker ps / docker logs forgesight-mysql）" }
  }
  Pop-Location
  $mysqlOk = Wait-Port 3307 90
  $pgOk = Wait-Port 5433 90
  if ($mysqlOk -and $pgOk) { Write-Host "  数据库就绪 ✓" -ForegroundColor Green }
  else {
    if (-not $mysqlOk) { Write-Warning "MySQL 3307 未就绪" }
    if (-not $pgOk) { Write-Warning "PostgreSQL 5433 未就绪" }
  }
}

function Start-Backend {
  $py = Join-Path $Root "backend\.venv\Scripts\python.exe"
  if (-not (Test-Path $py)) {
    throw "找不到虚拟环境 $py`n请先执行：cd backend; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt"
  }
  Write-Host "[2/3] 启动后端 http://127.0.0.1:8000 ..." -ForegroundColor Cyan
  $pidB = Start-Window "ForgeSight-Backend" (Join-Path $Root "backend") "& .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload"
  if (Wait-Port 8000 90) { Write-Host "  后端就绪 ✓" -ForegroundColor Green }
  else { Write-Warning "后端 8000 未就绪，请查看 ForgeSight-Backend 窗口日志" }
  return $pidB
}

function Start-Frontend {
  $fe = Join-Path $Root "frontend-new"
  $Pkg = if (Test-Path (Join-Path $fe "pnpm-lock.yaml")) { "pnpm" } else { "npm" }
  if (-not (Test-Path (Join-Path $fe "node_modules"))) {
    Write-Host "  首次运行：安装前端依赖 ($Pkg) ..." -ForegroundColor Yellow
    Push-Location $fe
    try { if ($Pkg -eq "pnpm") { pnpm install | Out-Host } else { npm ci --no-audit --no-fund | Out-Host } } finally { Pop-Location }
  }
  Write-Host "[3/3] 启动前端 http://127.0.0.1:5173 ..." -ForegroundColor Cyan
  $pidF = Start-Window "ForgeSight-Frontend" $fe "$Pkg run dev -- --strictPort"
  if (Wait-Port 5173 120) { Write-Host "  前端就绪 ✓" -ForegroundColor Green }
  else { Write-Warning "前端 5173 未就绪，请查看 ForgeSight-Frontend 窗口日志" }
  return $pidF
}

function Stop-Services {
  $found = $false
  if (Test-Path $StateFile) {
    $svc = Get-Content $StateFile | ConvertFrom-Json
    foreach ($name in "backend", "frontend") {
      $id = $svc.$name
      if ($id) { Write-Host "停止 $name (PID $id) ..."; taskkill /PID $id /T /F 2>$null | Out-Null; $found = $true }
    }
    Remove-Item $StateFile -Force
  }
  # 兜底：按端口找残留进程（如窗口被手动关闭、PID 文件丢失）
  foreach ($port in 8000, 5173) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
      $p = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
      if ($p) { Write-Host "停止残留 $($p.ProcessName) (PID $($conn.OwningProcess), 端口 $port) ..."; Stop-Process -Id $conn.OwningProcess -Force -Confirm:$false; $found = $true }
    }
  }
  if ($found) { Write-Host "服务已全部停止。" -ForegroundColor Green } else { Write-Host "没有检测到运行中的服务。" }
  Write-Host "提示：数据库容器保留（如需停止：docker compose stop）" -ForegroundColor DarkGray
}

# ---------- 主流程 ----------
if ($Stop) { Stop-Services; exit }
if (-not ($All -or $Backend -or $Frontend -or $Db)) { $All = $true }

$svc = @{ backend = $null; frontend = $null }
# 每启动一个服务立即保存 PID，中途失败也能用 -Stop 收干净
$save = { Save-State $svc }

if ($All -or $Db) {
  if (-not $SkipDb) { Start-Database } else { Write-Host "跳过数据库（-SkipDb）" -ForegroundColor DarkGray }
}
if ($All -or $Backend) { $svc.backend = Start-Backend; & $save }
if ($All -or $Frontend) { $svc.frontend = Start-Frontend; & $save }

Write-Host ""
Write-Host "  ✓ 启动完成，浏览器即将打开 http://127.0.0.1:5173" -ForegroundColor Green
Write-Host "  ✓ 接口文档 http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host "  ✓ 停止服务：.\forge-sight.ps1 -Stop" -ForegroundColor DarkGray
Start-Process "http://127.0.0.1:5173"
