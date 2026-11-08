# 팀원 작업 · Push 가이드

`main`과 `develop`에는 직접 push할 수 없습니다. **내 브랜치에 push → PR → 리뷰 승인 → develop 병합** 순서로만 코드가 합쳐집니다.

## 내 브랜치
| 담당 | 브랜치 |
|---|---|
| A (추천) | `feat/recommendation-rules`, `feat/recommendation-ranking`, `feat/recommendation-feedback` |
| B (비전) | `feat/vision-extraction`, `feat/garment-segmentation` |
| C (백엔드) | `chore/project-setup`, `feat/auth-profile`, `feat/garment-storage`, `feat/recommendation-feedback`, `test/mvp-integration` |
| D (프론트) | `feat/onboarding-wardrobe-ui`, `feat/recommendation-ui`, `feat/avatar-2d-preview` |

## 1. 최초 1회: 저장소 받기와 환경 설정
```powershell
git clone https://github.com/asvon-p/univ_hackathon_18.git
cd univ_hackathon_18
git config user.name "본인 GitHub 아이디"
git config user.email "본인 GitHub 이메일"

# Python 환경 (Miniconda 필요: https://docs.anaconda.com/miniconda/)
# Anaconda Prompt 또는 conda가 연결된 PowerShell에서 실행
conda env create -f environment.yml
conda activate univ18
python --version                      # Python 3.12.14 확인
copy .env.example .env
```
VS Code에서는 `Ctrl+Shift+P` → **Python: Select Interpreter** → **univ18**을 선택하세요.
`conda` 명령을 PowerShell에서 못 찾으면 Anaconda Prompt에서 `conda init powershell`을 한 번 실행한 뒤 PowerShell을 다시 여세요.

처음 push할 때 GitHub 로그인 창이 뜨면 **Sign in with your browser**로 로그인하세요. 한 번 로그인하면 다시 뜨지 않습니다.
(push 권한이 없다는 오류가 나면 C에게 Collaborator 초대를 요청하고, 메일로 온 초대를 수락하세요.)

## 2. 매번 작업 시작 전: 최신 develop 반영
```powershell
git switch develop
git pull origin develop
git switch feat/내-브랜치            # 처음이면: git switch -c feat/내-브랜치 --track origin/feat/내-브랜치
git merge develop
conda activate univ18
conda env update -f environment.yml --prune   # requirements.txt가 바뀌었을 때만
```

## 3. 작업하고 내 브랜치에 push
```powershell
git status                            # 바뀐 파일 확인 (.env, 사진, 모델 파일이 없는지 확인)
git add 파일경로                       # 또는 git add .
git commit -m "feat: 상의-하의 조합 후보 생성"
git push                              # 처음 push라면: git push -u origin feat/내-브랜치
```
- 커밋은 작은 단위로 자주 하세요.
- 커밋 전에 `ruff format .`과 `pytest`를 실행하는 것을 권장합니다.

## 4. PR 만들기
1. GitHub 저장소 페이지에서 **Compare & pull request**를 누릅니다. (안 보이면 **Pull requests → New pull request**)
2. **base: `develop`** ← **compare: `feat/내-브랜치`** 인지 확인합니다. ⚠️ base가 `main`이면 안 됩니다.
3. 제목은 커밋 형식으로 씁니다. 예: `feat: 온도 기반 의류 필터링`
4. 템플릿(기능 요약, API/Schema 변경, 테스트 방법, 화면, 미해결 문제)을 채웁니다.
5. 오른쪽 **Reviewers**에서 리뷰어 1명을 지정합니다.

## 5. 리뷰와 병합
- 리뷰어는 **Files changed → Review changes → Approve**(또는 Request changes)를 누릅니다.
- 수정 요청을 받으면 같은 브랜치에서 고친 뒤 다시 `git push` 하면 됩니다. PR에 자동으로 반영됩니다.
- 승인을 받으면 작성자가 **Squash and merge**를 누릅니다.
- 병합 후 브랜치는 **삭제하지 않습니다**(계속 사용). 다음 작업 전에 2번을 다시 실행하세요.

## 6. 자주 나는 문제
| 상황 | 해결 |
|---|---|
| `develop`에 push하다 `protected branch` 오류 | 정상입니다. 내 브랜치로 옮겨서 push하세요: `git switch -c feat/내-브랜치` → `git push -u origin feat/내-브랜치` |
| `git merge develop`에서 충돌(conflict) | VS Code에서 충돌 파일을 열고 **Accept Current / Incoming / Both** 중 선택 → `git add .` → `git commit` → `git push`. `shared/contracts.py` 충돌은 C와 같이 해결 |
| push가 `rejected (fetch first)` | `git pull` 후 다시 `git push` |
| 실수로 `.env`나 큰 파일을 커밋 | push 전이면 `git reset --soft HEAD~1` 후 해당 파일 제외. 이미 push했다면 바로 C에게 알리고 API 키는 재발급 |
| `ModuleNotFoundError` | `conda activate univ18` 했는지 확인 → `conda env update -f environment.yml --prune` |
| 새 라이브러리가 필요 | `pip install 이름` 후 담당 `requirements.txt`에 버전과 함께 추가하고, 그 변경은 작은 PR로 따로 올리기 |
| 다른 사람 브랜치 코드가 필요 | 직접 가져오지 말고 먼저 develop에 병합되도록 요청한 뒤 `git merge develop` |

## 규칙 요약
1. `main`, `develop`에서 작업하지 않는다.
2. 작업 전에는 항상 `develop`을 받아서 내 브랜치에 merge한다.
3. PR의 base는 항상 `develop`이다.
4. 승인 1명 이상, 병합은 Squash and merge로 한다.
5. `shared/contracts.py`, `requirements.txt`, DB 모델 변경은 작은 PR로 따로 올리고 미리 알린다.
6. Python 환경은 conda `univ18`(3.12.14)만 사용하고, 라이브러리는 pip + requirements.txt로 관리한다.
7. `.env`, API 키, 사용자 사진, 모델 가중치는 절대 커밋하지 않는다.
