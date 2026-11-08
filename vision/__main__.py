"""로컬 사진 1장으로 VisionResult JSON 확인: python -m vision /path/to/photo.jpg"""

import argparse
from pathlib import Path

from . import extract, warmup


def main() -> None:
    parser = argparse.ArgumentParser(description="FITJANG B Vision manual smoke test")
    parser.add_argument("image", nargs="?", help="표준화된 JPG/PNG 사진 경로")
    parser.add_argument("--warmup", action="store_true", help="선택한 모델을 미리 내려받고 로딩")
    parser.add_argument("--garment-id", default="G-test")
    args = parser.parse_args()
    if args.warmup:
        warmup()
        print("Vision models ready")
    if not args.image:
        if not args.warmup:
            parser.error("image 또는 --warmup이 필요합니다")
        return
    result = extract(str(Path(args.image).expanduser().resolve()), args.garment_id)
    print(result.model_dump_json(indent=2))
    if not result.success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
