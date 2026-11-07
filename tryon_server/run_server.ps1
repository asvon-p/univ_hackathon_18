# conda 환경 sia_univ18_cloth 로 시착 API 서버 실행 (기본 0.0.0.0:8000)
# 인증 키는 .env 의 TRYON_API_KEY 에서 읽는다 (없으면 인증 없이 동작)
$py = "C:\Users\admin\.conda\envs\sia_univ18_cloth\python.exe"
Set-Location $PSScriptRoot
if (-not (Test-Path "fashn-vton-1.5\weights\model.safetensors")) {
    Write-Host "가중치가 없습니다. 먼저 실행:  & $py fashn-vton-1.5\scripts\download_weights.py --weights-dir fashn-vton-1.5\weights" -ForegroundColor Yellow
    exit 1
}
if (-not $env:TRYON_PORT) { $env:TRYON_PORT = "8000" }
Write-Host "API 문서: http://127.0.0.1:$env:TRYON_PORT/docs"
& $py server.py
