# 자주 쓰는 명령 모음 (저장소 루트에서 실행, conda activate univ18 후)
#   .\scripts\dev.ps1 <명령> [인자]
#   예: .\scripts\dev.ps1 setup   (최초 1회)
param(
  [Parameter(Position = 0)][string]$Command = "help",
  [Parameter(Position = 1)][string]$Arg = ""
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Check($what) { if ($LASTEXITCODE -ne 0) { throw "실패: $what" } }

switch ($Command) {
  "setup" {
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host ".env 생성 완료" -ForegroundColor Green }
    python scripts/init_auth_secret.py; Check "JWT secret"
    docker compose up -d --wait; Check "docker compose"
    alembic -c backend/alembic.ini upgrade head; Check "migrate"
    pytest; Check "pytest"
    Write-Host "`n준비 완료! .\scripts\dev.ps1 run 으로 서버를 실행하세요." -ForegroundColor Green
  }
  "db"            { docker compose up -d --wait; Check "docker compose" }
  "db-stop"       { docker compose stop }
  "db-reset"      {
    Write-Host "DB 데이터를 모두 삭제합니다. 계속하려면 y 입력" -ForegroundColor Yellow
    if ((Read-Host) -eq "y") { docker compose down -v; docker compose up -d --wait; alembic -c backend/alembic.ini upgrade head }
  }
  "migrate"       { alembic -c backend/alembic.ini upgrade head; Check "migrate" }
  "makemigration" {
    if (-not $Arg) { throw '메시지가 필요합니다: .\scripts\dev.ps1 makemigration "add garments"' }
    alembic -c backend/alembic.ini revision --autogenerate -m $Arg; Check "makemigration"
  }
  "run"           { uvicorn backend.app.main:app --reload --port 8000 }
  "serve"         { uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 }   # 시연용: reload 없음, 워커 1개
  "test"          { pytest $Arg }
  "lint"          { ruff format .; ruff check . --fix }
  "health"        { (Invoke-WebRequest -UseBasicParsing http://localhost:8000/health).Content }
  default {
    Write-Host @"
사용법: .\scripts\dev.ps1 <명령>
  setup          최초 1회: .env 생성 + DB 실행 + 마이그레이션 + 테스트
  db / db-stop   PostgreSQL 실행 / 중지 (docker)
  db-reset       DB 데이터 전체 삭제 후 재생성
  migrate        마이그레이션 적용 (alembic upgrade head)
  makemigration  "메시지"  모델 변경 → 마이그레이션 파일 생성 (C만)
  run            개발 서버 (자동 재시작)  → http://localhost:8000/docs
  serve          시연 서버 (외부 접속 허용, 재시작 없음)
  test [경로]     테스트 실행
  lint           ruff 포맷 + 검사
  health         서버 상태 확인
"@
  }
}
