# FITJANG B Vision MVP, 2026-11-08

기존 `vision/README.md` 팀 규정은 그대로 유지합니다. 이 파일은 이번 임시 구현 안내입니다.

## 오늘의 시연 모드 (별도 GPU/모델 다운로드 필요 없음)

```bash
python -m pip install -r vision/requirements.txt
export VISION_PROVIDER=local
export VISION_CLASSIFIER=demo
export VISION_SEGMENTATION=flat
python -m pytest vision/tests backend/tests/contract -q
python -m vision /path/to/your/original.jpg --garment-id G-test
```

Windows PowerShell에서는 `export X=Y` 대신 `$env:X='Y'`를 사용합니다.

- 실제 **의류 이미지가 분석**되어 대표 색상을 추정하고, **단색 배경**일 때만 단순 배경 제거 PNG를 만듭니다.
- `VISION_CLASSIFIER=demo`는 **의류 유형 CNN 분류가 아닙니다**. 시연 편의를 위해 `top`을 반환합니다. 상의/하의/아우터가 섞이면 옷 등록 후 화면에서 반드시 올바른 유형으로 수정하세요. 시연만을 위한 일괄 기본 유형은 `VISION_DEMO_CATEGORY=bottom`처럼 설정할 수 있지만 이는 이미지 예측이 아닙니다.
- 패턴·스타일·두께는 거짓 예측 대신 각각 `unknown`, 빈 리스트, `null`로 둡니다.
- 피부/의류가 단색 배경과 비슷하면 단순 누끼 결과가 불완전할 수 있으며 복잡한 배경에서는 생성하지 않습니다.

## 모델 단계별 교체

1. **시연:** `VISION_CLASSIFIER=demo`, `VISION_SEGMENTATION=flat`.
2. **자동 분류 실험:** `python -m pip install 'transformers>=4.40,<5' 'torch>=2.3'`, `VISION_CLASSIFIER=clip`. 처음 실행 시 모델 다운로드가 필요합니다. CLIP은 CNN이 아닌 ViT 기반이며 의류 사진에 대한 정확도/지연을 검증해야 합니다.
3. **실제 누끼:** `python -m pip install 'rembg[cpu]>=2.0'`, `VISION_SEGMENTATION=rembg`. 첫 사용 시 가중치 다운로드; 속도 측정 필요. Apple Silicon 호환성은 따로 확인하세요.
4. **CNN 자체 모델:** 나중에 `pipeline._predict` 내부만 torchvision CNN 등 학습한 모델로 교체. `vision.extract`의 인자/반환형은 바꾸지 않습니다.

`VISION_TIMEOUT_SEC=20` 안에서 동작하도록 이미지 해상도·모델 로딩 전략을 점검하세요. CLIP+rembg를 동시에 쓰면 제한을 초과할 수 있습니다.

## B와 C 사이에 약속한 형식

```python
from vision import extract
result = extract("/path/to/{user_id}/{garment_id}/original.jpg", "G1")
# VisionResult(success=True, attributes=GarmentAttributes(...), model_version="...")
```

백엔드는 직접 `VISION_PROVIDER=local`에서 이 함수를 가져다 쓰며, **전달된 임시폴더 외에는 PNG를 쓰지 않습니다**. 정상적으로 cutout을 만든 경우에만 `{user_id}/{garment_id}/cutout.png` 상대경로를 반환합니다.

## 확인할 사항

- `python -m pytest vision/tests backend/tests/contract -q`
- 옷 3장(상의, 하의, 아우터) 실제 등록해 색상을 확인, 유형은 수동 수정.
- 의류 이미지가 안 뜨면 C의 이미지 인증/경로 연결을 확인.
- 2D 착샷은 **FASHN VTON 서버 담당 영역**입니다. B의 cutout은 필수 입력이 아니며 원본을 먼저 사용합니다.
