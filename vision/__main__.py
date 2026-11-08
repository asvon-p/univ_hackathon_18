"""로컬 사진 1장으로 VisionResult JSON 확인: python -m vision /path/to/photo.jpg"""
import argparse
from pathlib import Path

from . import extract


def main() -> None:
    parser = argparse.ArgumentParser(description="FITJANG B Vision manual smoke test")
    parser.add_argument("image", help="original.jpg 사진 경로")
    parser.add_argument("--garment-id", default="G-test")
    args = parser.parse_args()
    result = extract(str(Path(args.image).expanduser().resolve()), args.garment_id)
    print(result.model_dump_json(indent=2))
    if not result.success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
