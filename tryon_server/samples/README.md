# 샘플 이미지 폴더 구조

```
samples/
  person/              <- 전신 사진 (여러 장 OK, 파일명 자유)
  garment/
    tops/              <- 상의
    bottoms/           <- 하의
    one-pieces/        <- 원피스
```

- 카테고리는 **폴더로 자동 인식**된다. 옷 사진을 해당 폴더에 넣기만 하면 됨.
- 옷 사진 종류(착용 사진 / 누끼·평면 상품 사진)는 자동 판별된다(사람이 검출되면 model, 아니면 flat-lay).
- 확장자: jpg, jpeg, png, webp
- `example_*.webp` 는 저장소 동봉 예제. 지워도 됨.

배치 실행: 프로젝트 루트에서
```
python batch_tryon.py            # person 전부 x garment 전부
python batch_tryon.py --person me --category tops --steps 20
```
결과: `outputs/batch_<시각>/` 에 이미지 + `index.html`(사람/옷/결과 나란히 비교) + `results.csv`
