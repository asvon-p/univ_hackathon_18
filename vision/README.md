# vision — 팀원 B

백엔드는 `vision/__init__.py`의 아래 함수만 호출합니다. **이름·인자·반환 타입은 바꾸지 마세요.**

```python
def extract(image_path: str, garment_id: str) -> VisionResult: ...
def warmup() -> None: ...  # 선택: 있으면 서버 시작 시 1회 호출 (모델 로딩)
```

## 지켜 주세요
- 실패하면 예외 대신 `VisionResult(success=False, error="이유")`를 반환합니다. (예외가 나도 백엔드가 막아 주지만 이유가 덜 정확해집니다)
- 속성 값은 `shared/contracts.py`의 단어 목록만 사용합니다. 모르면 `"unknown"` / `None`.
- `garment_id`는 받은 값을 그대로 넣습니다.
- 장치는 자동 선택: `device = "cuda" if torch.cuda.is_available() else "cpu"` — 팀원 노트북(CPU)에서도 돌아가야 합니다.
- 모델은 `warmup()` 또는 모듈 import 시 **한 번만** 로딩합니다. 요청마다 로딩하지 않습니다.
- 모델 가중치 파일은 Git에 올리지 않습니다 (HuggingFace 등에서 자동 다운로드, 모델 이름·버전은 코드에 고정).
- 결과에 `model_version`을 채워 주세요 (예: `"fashionclip-vitb32+rembg-1"`).
- `image_path`는 검증·회전 보정·축소 후 저장된 `original.jpg`의 실제 절대 경로입니다. 업로드 시의 원래 파일 이름은 사용하지 않습니다.
- cutout은 원본과 같은 폴더의 `cutout.png`로 저장하고, `cutout_path`에는 `"{user_id}/{garment_id}/cutout.png"` 형태의 **storage 기준 상대 경로**를 반환하세요. 예: `Path(image_path).parent.parent.name`이 `user_id`입니다. 다른 경로나 실제 PNG가 아닌 파일은 백엔드가 제공하지 않습니다.
- 업로드·재분석 모두 `image_path`가 `{임시폴더}/{user_id}/{garment_id}/original.jpg` 복사본입니다. **전달된 경로 옆에만** cutout을 쓰고 실제 `STORAGE_DIR` 경로를 조립해 덮어쓰지 마세요. 반환 `cutout_path` 형식과 함수 인자는 동일합니다. C가 임시 PNG를 검증해 고유한 `cutout-{버전}.png`로 게시하며 수동 수정한 속성은 DB 병합 때 보호합니다.
- timeout 후에도 Python 모델 스레드는 실제 종료될 때까지 계속될 수 있습니다. 임시 폴더 정리는 해당 스레드 종료 시 수행하며 늦은 출력은 API/기존 파일에 반영하지 않습니다. 업로드 분석 중 탈퇴가 완료되면 결과 게시도 차단하므로 사용자 폴더가 다시 생기지 않습니다.
- 백엔드가 시간 제한(`VISION_TIMEOUT_SEC`, 기본 20초)과 GPU 동시 실행 1개 제한을 걸어 줍니다.

## PyTorch 설치 (requirements.txt 에 넣지 않음)
```powershell
conda activate univ18
# 데스크탑 (RTX 5090): CUDA 12.8 빌드 필수, PyTorch 2.7 이상
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
# GPU 없는 노트북
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```
그 외 라이브러리는 `vision/requirements.txt`를 만들어 적고, `environment.yml`의 pip 목록에 `-r vision/requirements.txt`를 추가해 주세요 (작은 PR로).

## 백엔드와 연결해 보기
```powershell
# .env 에서 VISION_PROVIDER=local 로 바꾼 뒤
.\scripts\dev.ps1 run
# http://localhost:8000/docs → POST /api/v1/garments 에 사진 업로드
pytest backend/tests/contract   # PR 전에 반드시 통과
```
