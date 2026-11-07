# conda 환경 sia_univ18_cloth 의 Python 으로 로컬 Gradio 데모 실행
$py = "C:\Users\admin\.conda\envs\sia_univ18_cloth\python.exe"
Set-Location $PSScriptRoot
if (-not (Test-Path "fashn-vton-1.5\weights\model.safetensors")) {
    Write-Host "가중치가 없습니다. 먼저 실행:  & $py fashn-vton-1.5\scripts\download_weights.py --weights-dir fashn-vton-1.5\weights" -ForegroundColor Yellow
    exit 1
}
& $py app.py
