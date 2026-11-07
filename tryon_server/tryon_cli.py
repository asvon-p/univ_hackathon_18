"""FASHN VTON v1.5 로컬 시착 CLI.

예)
  python tryon_cli.py --person samples/person/me.jpg --garment samples/garment/shirt.jpg --category tops
  python tryon_cli.py --info          # GPU / onnxruntime 상태만 출력
  python tryon_cli.py --sample        # 저장소 동봉 예제 이미지로 스모크 테스트
"""
from __future__ import annotations

import argparse
import sys

import local_tryon as lt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FASHN VTON v1.5 로컬 시착")
    ap.add_argument("--person", help="전신 사진 경로")
    ap.add_argument("--garment", help="의상 사진 경로")
    ap.add_argument("--category", default="auto", choices=lt.CATEGORIES + ("auto",),
                    help="auto: samples/garment/<카테고리>/ 폴더명으로 인식")
    ap.add_argument("--garment-photo-type", default="auto", choices=lt.GARMENT_PHOTO_TYPES + ("auto",),
                    help="model: 착용 사진 / flat-lay: 누끼·평면 상품 사진 / auto: 사람 검출로 판별")
    ap.add_argument("--num-samples", type=int, default=1, help="1~4")
    ap.add_argument("--num-timesteps", type=int, default=30, help="20=빠름, 30=보통, 50=고품질")
    ap.add_argument("--guidance-scale", type=float, default=1.5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-segmentation-free", action="store_true")
    ap.add_argument("--device", default=None, help="cuda / cpu (기본 자동)")
    ap.add_argument("--info", action="store_true", help="환경 정보만 출력")
    ap.add_argument("--sample", action="store_true", help="저장소 예제 이미지로 실행")
    args = ap.parse_args(argv)

    print(f"GPU : {lt.gpu_info()}")
    print(f"ONNX: {lt.onnx_providers()}")
    print(f"가중치: {lt.WEIGHTS_DIR}")
    if args.info:
        return 0

    if args.sample:
        args.person = str(lt.REPO_DIR / "examples" / "data" / "model.webp")
        args.garment = str(lt.REPO_DIR / "examples" / "data" / "garment.webp")
        args.category = "tops"
    if not args.person or not args.garment:
        ap.error("--person 과 --garment 는 필수입니다 (--info/--sample 제외)")

    try:
        res = lt.run_tryon(
            args.person, args.garment,
            category=args.category,
            garment_photo_type=args.garment_photo_type,
            num_samples=args.num_samples,
            num_timesteps=args.num_timesteps,
            guidance_scale=args.guidance_scale,
            seed=args.seed,
            segmentation_free=not args.no_segmentation_free,
            save_prefix="cli",
            device=args.device,
        )
    except FileNotFoundError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        msg = str(e)
        if "out of memory" in msg.lower():
            print("[오류] CUDA 메모리 부족. --num-samples 1 로 줄이거나 다른 GPU 프로그램을 종료하세요.", file=sys.stderr)
        print(f"[오류] {msg[:500]}", file=sys.stderr)
        return 1

    peak = f"{res.peak_vram_gb:.2f}GB" if res.peak_vram_gb is not None else "n/a"
    print(f"완료: {len(res.images)}장, {res.elapsed_sec:.1f}s, 최대 VRAM {peak}, "
          f"category={res.category}, garment_photo_type={res.garment_photo_type}")
    for p in res.saved:
        print(f"  -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
