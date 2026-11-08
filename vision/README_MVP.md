# FITJANG Vision MVP 실행 가이드

## 1. 담당 기능
- CLIP: 사진을 분석해 의류 카테고리와 세부 종류 예측
- 색상 분석: 대표 색상 추출
- rembg (U2Net): 배경 제거 및 투명 PNG 생성
- 백엔드 연동: vision.extract(image_path, garment_id)

패턴, 스타일, 보온성 예측은 아직 미구현입니다.
CLIP은 CNN이 아니라 ViT 기반 사전학습 모델입니다.

## 2. 설치
저장소 루트에서 Python 3.12 환경을 활성화한 뒤:

python -m pip install -r backend/requirements.txt
python -m pip install -r vision/requirements.txt
python -m pip install "torch>=2.3" "transformers>=4.40,<5" "rembg[cpu]>=2.0,<3"

NVIDIA GPU 사용자는 PyTorch 공식 설치 안내에 따라 CUDA용
torch를 별도로 설치하세요.

## 3. 실제 모델 실행 (macOS/Linux)
VISION_CLASSIFIER=clip VISION_SEGMENTATION=rembg \
python -m vision /path/to/clothes.jpg

실행 결과:
- success: true
- category: top/bottom/outerwear/shoes
- color: 대표 색상
- cutout_path: 누끼 생성 시 상대 경로

처음 실행하면 CLIP과 U2Net 모델이 다운로드됩니다.

## 4. FastAPI 연결
.env 파일과 PostgreSQL을 준비한 상태에서:

VISION_PROVIDER=local \
VISION_CLASSIFIER=clip \
VISION_SEGMENTATION=rembg \
python -m uvicorn backend.app.main:app --port 8000

http://127.0.0.1:8000/docs

회원가입 -> Authorize -> POST /api/v1/garments 순서로 테스트합니다.

반드시 응답에서 analysis_status=done, error=null 및
cutout_url 존재 여부를 확인하세요.
HTTP 201만으로 AI 분석 성공이 보장되지는 않습니다.

## 5. iPhone 사진 주의사항
백엔드는 실제 JPEG/PNG만 허용하며, 3200만 픽셀 초과 시
415 오류를 반환합니다.

HEIC 사진 변환 또는 고해상도 JPG 축소:
sips -s format jpeg -Z 2048 input.HEIC --out upload.jpg

## 6. 모델 다운로드 오류
rembg의 u2net.onnx 다운로드가 HTTP 503으로 실패하는 경우
별도 검증된 모델 파일을 ~/.rembg/models/u2net/u2net.onnx에
설치할 수 있습니다. 모델 가중치는 Git에 커밋하지 않습니다.

## 7. 테스트
python -m pytest vision/tests backend/tests/contract backend/tests/adapters/test_vision.py -q

2026-11-08 Mac 실측:
- 갈색 아우터 -> outerwear / brown
- 투명 PNG 생성 성공
- SafeVision 어댑터 분석 약 7.95초
- 실제 FastAPI 업로드 및 PostgreSQL 저장 성공

단일 사진 성공은 전체 분류 정확도를 의미하지 않습니다.
jacket/windbreaker 등 세부 분류는 추가 검증이 필요합니다.

## 8. 모드 설명
VISION_CLASSIFIER=demo: 실제 AI 분류가 아닌 기본값 top 반환
VISION_SEGMENTATION=flat: 단색 배경만 간단 제거
VISION_SEGMENTATION=off: 배경 제거 비활성화
VISION_CLASSIFIER=clip: 실제 CLIP 사용
VISION_SEGMENTATION=rembg: 실제 U2Net 사용

2D 가상착용(VTON)은 다른 팀원의 담당 기능입니다.
