# tryon_server

FASHN VTON v1.5로 착샷(사람 사진 + 옷 사진 → 옷을 입은 사진)을 만드는 서버. NVIDIA GPU 필요.

## 설치

```powershell
conda create -n tryon python=3.12 -y
conda activate tryon
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
git clone https://github.com/fashn-AI/fashn-vton-1.5.git
pip install -e .\fashn-vton-1.5
pip install -r requirements.txt python-dotenv
python fashn-vton-1.5\scripts\download_weights.py --weights-dir fashn-vton-1.5\weights
```

`tryon_server` 폴더 안에서 실행.

## 설정

`tryon_server/.env`

```
TRYON_API_KEY=키
```

## 실행

```powershell
python server.py
```

http://127.0.0.1:8000/docs

## API

헤더 `X-API-Key: 키`

| 요청 | 설명 |
|---|---|
| `POST /tryon` | `person`, `garment` 파일 + `category`(tops/bottoms/one-pieces) → `job_id` |
| `GET /jobs/{job_id}` | `status`: queued / running / done / failed |
| `GET /results/{job_id}.png` | 결과 이미지 (576×864) |
| `GET /health` | 서버 상태 |

한 벌에 약 40초.

## frontend 연결

`frontend/.env`

```
TRYON_API_URL=http://서버주소:8000
TRYON_API_KEY=키
```

## 올리지 않는 것

`.env`, `fashn-vton-1.5/`, `outputs/`
