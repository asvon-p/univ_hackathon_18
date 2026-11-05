# 최초 1회, 저장소 루트(univ_hackathon_18 폴더)에서 C가 실행합니다.
#   PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\setup_branches.ps1
# 하는 일: git 초기화 → main 첫 커밋/푸시 → develop 생성 → 모든 기능 브랜치를 develop에서 생성/푸시
$ErrorActionPreference = "Stop"
$Remote = "https://github.com/asvon-p/univ_hackathon_18.git"

$Branches = @(
  "chore/project-setup",
  "feat/auth-profile",
  "feat/garment-storage",
  "feat/vision-extraction",
  "feat/garment-segmentation",
  "feat/recommendation-rules",
  "feat/recommendation-ranking",
  "feat/recommendation-feedback",
  "feat/onboarding-wardrobe-ui",
  "feat/recommendation-ui",
  "feat/avatar-2d-preview",
  "test/mvp-integration"
)

function Run($cmd) { Write-Host "> git $cmd" -ForegroundColor Cyan; Invoke-Expression "git $cmd"; if ($LASTEXITCODE -ne 0) { throw "실패: git $cmd" } }

if (-not (Test-Path ".git")) { Run "init -b main" }
if (-not ((git remote) -contains "origin")) { Run "remote add origin $Remote" }

# PR 템플릿을 GitHub 위치로 복사
New-Item -ItemType Directory -Force ".github" | Out-Null
Copy-Item "docs\pull_request_template.md" ".github\pull_request_template.md" -Force

Run "add ."
Run "status --short"
Run 'commit -m "chore: initial project scaffold"'
Run "push -u origin main"

Run "switch -c develop"
Run "push -u origin develop"

foreach ($b in $Branches) {
  Run "branch $b develop"
  Run "push -u origin $b"
}

Run "switch develop"
Write-Host "`n완료! 생성된 원격 브랜치:" -ForegroundColor Green
git branch -r
