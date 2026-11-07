# 가상 시착(Virtual Try-On) 파일럿 — FASHN VTON v1.5 (로컬 GPU)

사람 전신 사진 + 의상(상의/하의/원피스) 사진 → 그 사람이 옷을 입은 모습을 생성.
**FASHN VTON v1.5** 오픈소스 모델(Apache-2.0, 2026-01-27 공개)을 **이 PC의 GPU에서 직접** 실행한다.
클라우드 API 호출 없음, 비용 없음.

- 코드: https://github.com/fashn-AI/fashn-vton-1.5 (`fashn-vton-1.5/` 에 클론됨)
- 가중치: https://huggingface.co/fashn-ai/fashn-vton-1.5 (~2GB, `fashn-vton-1.5/weights/`)
- 모델: 972M 파라미터 MMDiT, 픽셀 공간, 출력 576×864, RTX 30xx 이상에서 bf16

## 1. 환경

| 항목 | 값 |
|---|---|
| conda 환경 | `sia_univ18_cloth` (Python 3.12) |
| GPU | NVIDIA RTX 3060 Ti, VRAM 8GB (드라이버 560.94, CUDA 12.6 런타임) |
| torch | CUDA 12.4 빌드 (`--index-url https://download.pytorch.org/whl/cu124`) |
| 포즈 추정 | DWPose (onnxruntime-gpu) |
| 인체 파싱 | fashn-human-parser (~244MB, 첫 실행 시 HF 캐시로 자동 다운로드) |

### 실측 (2026-11-07, 저장소 예제 이미지, RTX 3060 Ti 1장)

| 항목 | 값 |
|---|---|
| 파이프라인 로드 | 약 10초 |
| 30 스텝 (기본) | 39초, 1.25 s/step |
| 20 스텝 | 26초 |
| 최대 VRAM | 2.95GB (num_samples=1) |

공식 문서의 "약 8GB" 보다 훨씬 적게 써서 8GB 카드로 충분하다. `num_samples` 를 올리면 VRAM 이 비례해 늘어난다.

주의: onnxruntime-gpu 최신판(1.24+)은 CUDA 13 빌드라 이 PC(드라이버 560.94, CUDA 12.x)에서 DLL 로드에 실패한다.
`onnxruntime-gpu<1.24` (현재 1.23.2, CUDA 12 빌드) 로 고정해 두었다. CUDA 13 으로 올릴 이유는 없다.

GPU 가 여러 장이면 `FASHN_GPU_INDEX=1` 처럼 환경변수로 사용할 GPU(nvidia-smi 번호)를 지정할 수 있다.

### 설치 (이미 완료된 항목 포함)

```powershell
$py = "C:\Users\admin\.conda\envs\sia_univ18_cloth\python.exe"
& $py -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
& $py -m pip install -e .\fashn-vton-1.5
& $py -m pip install "onnxruntime-gpu<1.24,>=1.20"   # CUDA 12 빌드로 고정
& $py -m pip install gradio pillow
& $py fashn-vton-1.5\scripts\download_weights.py --weights-dir fashn-vton-1.5\weights
```

## 2. 사용법

### 샘플 넣는 곳 (여러 장 일괄 실행용)

```
samples/
  person/              <- 전신 사진 (여러 장, 파일명 자유)
  garment/
    tops/              <- 상의
    bottoms/           <- 하의
    one-pieces/        <- 원피스
```

- 카테고리는 폴더명으로 자동 인식. 옷 사진 종류(착용/평면)는 사람 검출로 자동 판별.
- `example_*.webp` 는 저장소 예제. 지워도 됨.

### 배치 실행 (사람 전부 × 옷 전부)

```powershell
C:\Users\admin\.conda\envs\sia_univ18_cloth\python.exe batch_tryon.py
```

- `--person me` / `--garment shirt` : 파일명 부분 일치로 필터, `--category tops` : 카테고리 제한
- `--steps 20` : 빠르게, `--limit 5` : 조합 수 제한, `--dry-run` : 조합만 출력
- `--out outputs/batch_x` : 같은 폴더에 이어서 실행(이미 있는 결과는 건너뜀)
- 결과: `outputs/batch_<시각>/` 에 `사람__카테고리__옷__s시드.png`, `index.html`(사람/옷/결과 비교표), `results.csv`

### 환경 점검 (GPU / onnxruntime provider / 가중치 경로)

```powershell
C:\Users\admin\.conda\envs\sia_univ18_cloth\python.exe tryon_cli.py --info
```

### 저장소 예제 이미지로 스모크 테스트

```powershell
C:\Users\admin\.conda\envs\sia_univ18_cloth\python.exe tryon_cli.py --sample
```

### CLI

```powershell
C:\Users\admin\.conda\envs\sia_univ18_cloth\python.exe tryon_cli.py --person samples\person\me.jpg --garment samples\garment\shirt.jpg --category tops --garment-photo-type flat-lay
```

- `--category` : `tops` | `bottoms` | `one-pieces` | `auto`(기본, `samples/garment/<카테고리>/` 폴더명으로 인식)
- `--garment-photo-type` : `auto`(기본, 사람 검출로 판별) | `model`(착용 사진) | `flat-lay`(누끼/평면 상품 사진)
- `--num-samples 1~4`, `--num-timesteps 20/30/50`, `--guidance-scale 1.5`, `--seed 42`, `--no-segmentation-free`
- 결과: `outputs/` 에 PNG 저장

### HTTP API 서버 (다른 앱/PC 에서 호출)

```powershell
$env:TRYON_API_KEY = "원하는키"     # 생략하면 인증 없음 (LAN 테스트용)
.\run_server.ps1                   # 0.0.0.0:8000, 문서 http://127.0.0.1:8000/docs
```

모델을 GPU 에 상주시키고, 워커 1개가 큐에서 작업을 순서대로 처리한다(동시 요청은 줄을 선다).

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/tryon` | multipart: `person`, `garment` 파일 + `category`(tops/bottoms/one-pieces). 선택: `garment_photo_type`(auto), `num_timesteps`(30), `seed`(42), `guidance_scale`(1.5), `segmentation_free`(true). 202 + `job_id` 반환. `?sync=true` 면 완료까지 기다렸다가 PNG 반환 |
| `GET` | `/jobs/{job_id}` | `queued / running / done / failed`, 대기 순번, 완료 시 `result_url` |
| `GET` | `/results/{job_id}.png` | 결과 이미지 (기본 6시간 보관, `TRYON_JOB_TTL_SEC`) |
| `GET` | `/jobs` | 최근 작업 목록 |
| `GET` | `/health` | 모델 로드 상태, GPU, 대기 수 (인증 불필요) |

인증은 `X-API-Key` 헤더. 호출 예제: [client_example.py](client_example.py)

```powershell
C:\Users\admin\.conda\envs\sia_univ18_cloth\python.exe client_example.py --server http://127.0.0.1:8000 --api-key 원하는키 --person me.jpg --garment shirt.jpg --category tops
```

```python
import requests
r = requests.post("http://서버:8000/tryon", headers={"X-API-Key": "키"},
                  files={"person": open("me.jpg", "rb"), "garment": open("shirt.jpg", "rb")},
                  data={"category": "tops"})
job_id = r.json()["job_id"]            # 이후 GET /jobs/{job_id} 폴링 -> result_url 다운로드
```

외부 인터넷에서 호출하려면 공유기 포트포워딩 또는 cloudflared/ngrok 터널로 8000 포트를 노출한다. 그 경우 `TRYON_API_KEY` 는 반드시 설정.

처리량: GPU 1장 기준 30스텝 약 38초/장(시간당 약 90장), 20스텝 약 26초/장.

### 웹 데모 (Gradio)

```powershell
.\run_app.ps1
```

http://127.0.0.1:7860 → 사진 2장 업로드 → 카테고리 선택 → 시착 실행. 첫 실행은 모델 로드로 수십 초.

## 3. 파일 구성

| 파일 | 역할 |
|---|---|
| `local_tryon.py` | 파이프라인 로드(싱글턴), 추론, 저장, GPU 정보. Windows 용 DLL 선적재 처리 |
| `tryon_cli.py` | CLI (`--info`, `--sample`, 일반 실행) |
| `batch_tryon.py` | samples/ 전체 조합 일괄 실행 + 비교표 HTML + CSV (`--no-segmentation-free` 지원) |
| `server.py` / `run_server.ps1` | FastAPI HTTP API 서버 (작업 큐, API 키 인증) |
| `client_example.py` | API 호출 예제 (requests 만 필요, 다른 PC 에서 사용 가능) |
| `app.py` / `run_app.ps1` | Gradio 웹 데모 |
| `fashn-vton-1.5/` | 공식 저장소 클론 (editable 설치), `weights/` 에 가중치 |
| `samples/` | 테스트 이미지 |
| `outputs/` | 결과 (git 제외) |
| `api_pilot/` | (참고용) FASHN 유료 클라우드 API 버전. 로컬이 안 될 때의 대안. 1장당 1크레딧 ≈ $0.075 |

## 4. 모델 한계 (공식 모델카드)

- 출력 해상도 576×864 고정
- 체형 보존이 완벽하지 않을 수 있음
- 긴 옷→짧은 옷, 두꺼운 옷→얇은 옷으로 바꿀 때 원래 옷 흔적이 남을 수 있음
- 상의+하의 동시 시착은 미지원 → 상의 결과를 입력으로 하의를 한 번 더 돌리는 2단계로 처리

## 5. 다음 단계 후보

- 샘플 이미지 여러 장으로 품질/속도/VRAM 실측 표 만들기
- 상의→하의 2단계 파이프라인
- 입력 사진 검증(전신 여부, 포즈 검출 실패 처리)
- 서비스화: 작업 큐 + GPU 1장 기준 동시 처리 1건
