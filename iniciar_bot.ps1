# =============================================================
# INICIAR BOT + TUNEL CLOUDFLARE (acesso pelo celular)
# =============================================================
# Como usar: clique com botao direito -> "Executar com PowerShell"
# Ou no terminal: .\iniciar_bot.ps1
# =============================================================

$CLOUDFLARED = "$env:USERPROFILE\cloudflared.exe"
$BOT_PORT    = 8000
$BOT_DIR     = $PSScriptRoot
$PYTHON      = Join-Path $BOT_DIR "venv\Scripts\python.exe"

Set-Location $BOT_DIR

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "   DAYTRADE BOT - Iniciando localmente     " -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# --- 1. Mata processos antigos na porta 8000 ---
$proc = Get-NetTCPConnection -LocalPort $BOT_PORT -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
if ($proc) {
    Write-Host "[INFO] Porta $BOT_PORT ocupada. Encerrando processo antigo (PID: $proc)..." -ForegroundColor Yellow
    Stop-Process -Id $proc -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# --- 2. Inicia o bot em background ---
Write-Host "[1/2] Iniciando bot na porta $BOT_PORT..." -ForegroundColor Green
$botProcess = Start-Process -FilePath $PYTHON `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "$BOT_PORT" `
    -WorkingDirectory $BOT_DIR `
    -PassThru -NoNewWindow

Write-Host "      PID do bot: $($botProcess.Id)" -ForegroundColor Gray

# Aguarda o bot subir
Write-Host "      Aguardando bot inicializar..." -ForegroundColor Gray
Start-Sleep -Seconds 5

# Verifica se subiu
try {
    $resp = Invoke-WebRequest -Uri "http://localhost:$BOT_PORT/health" -TimeoutSec 5 -ErrorAction Stop
    Write-Host "      Bot OK - status: $($resp.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "      [AVISO] Bot pode estar ainda inicializando..." -ForegroundColor Yellow
}

# --- 3. Inicia o tunel Cloudflare ---
Write-Host ""
Write-Host "[2/2] Iniciando tunel Cloudflare..." -ForegroundColor Green
Write-Host "      (A URL do celular vai aparecer abaixo em segundos)" -ForegroundColor Gray
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Para PARAR tudo: pressione Ctrl+C" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Redireciona saida do cloudflared para mostrar a URL
# O cloudflared mostra algo como: https://xyz.trycloudflare.com
& $CLOUDFLARED tunnel --url "http://localhost:$BOT_PORT" 2>&1 | ForEach-Object {
    $line = $_
    # Destaca a URL publica
    if ($line -match "https://.*trycloudflare\.com") {
        Write-Host ""
        Write-Host ">>> ACESSE NO CELULAR: $line <<<" -ForegroundColor Yellow -BackgroundColor DarkBlue
        Write-Host ""
    } else {
        Write-Host $line -ForegroundColor Gray
    }
}

# Limpeza ao sair
Write-Host ""
Write-Host "[INFO] Tunel encerrado. Encerrando bot (PID: $($botProcess.Id))..." -ForegroundColor Yellow
Stop-Process -Id $botProcess.Id -Force -ErrorAction SilentlyContinue
Write-Host "Tudo encerrado." -ForegroundColor Cyan
