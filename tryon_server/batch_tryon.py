"""여러 샘플 일괄 시착.

samples/person/*            x   samples/garment/{tops,bottoms,one-pieces}/*
의 모든 조합을 돌리고 outputs/batch_<시각>/ 에 결과 + index.html(비교표) + results.csv 를 만든다.

예)
  python batch_tryon.py                              # 전체 조합
  python batch_tryon.py --person me --category tops  # 파일명에 'me' 포함된 사람 x 상의만
  python batch_tryon.py --steps 20 --limit 5         # 빠르게 5개만
  python batch_tryon.py --out outputs/batch_x        # 같은 폴더에 이어서(이미 있는 결과는 건너뜀)
"""
from __future__ import annotations

import argparse
import csv
import html
import sys
import time
from pathlib import Path

from PIL import Image

import local_tryon as lt

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
SAMPLES = lt.ROOT / "samples"


def list_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS)


def thumb(src: Path, dest: Path, max_side: int = 384) -> Path:
    im = Image.open(src).convert("RGB")
    im.thumbnail((max_side, max_side))
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, quality=85)
    return dest


def write_index(out_dir: Path, rows: list[dict]) -> Path:
    def cell_img(rel: str, title: str) -> str:
        return (f'<a href="{html.escape(rel)}" target="_blank"><img src="{html.escape(rel)}" '
                f'title="{html.escape(title)}"></a>')

    parts = [
        "<!doctype html><meta charset='utf-8'><title>FASHN VTON batch</title>",
        "<style>body{font-family:sans-serif;margin:16px;background:#111;color:#ddd}"
        "table{border-collapse:collapse}td,th{border:1px solid #333;padding:6px;vertical-align:top;text-align:center}"
        "img{max-height:360px;max-width:260px}small{color:#999}.err{color:#f66}</style>",
        f"<h2>배치 결과 ({len(rows)}건) — {out_dir.name}</h2>",
        "<table><tr><th>#</th><th>사람</th><th>옷</th><th>결과</th><th>정보</th></tr>",
    ]
    for i, r in enumerate(rows, 1):
        info = (f"{html.escape(r['person'])}<br>+ {html.escape(r['garment'])}<br>"
                f"<small>{r['category']} / {r['garment_photo_type']} / {r['steps']}step / seed {r['seed']} / "
                f"segfree={r.get('segmentation_free', True)}<br>"
                f"{r['elapsed_sec']}s</small>")
        result = (cell_img(r["result_rel"], "result") if r.get("result_rel")
                  else f"<span class='err'>{html.escape(r.get('error', ''))}</span>")
        parts.append(
            f"<tr><td>{i}</td><td>{cell_img(r['person_thumb'], r['person'])}</td>"
            f"<td>{cell_img(r['garment_thumb'], r['garment'])}</td><td>{result}</td><td>{info}</td></tr>"
        )
    parts.append("</table>")
    p = out_dir / "index.html"
    p.write_text("\n".join(parts), encoding="utf-8")
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FASHN VTON v1.5 배치 시착")
    ap.add_argument("--person", default=None, help="사람 파일명에 포함된 문자열로 필터")
    ap.add_argument("--garment", default=None, help="옷 파일명에 포함된 문자열로 필터")
    ap.add_argument("--category", default=None, choices=lt.CATEGORIES, help="특정 카테고리만")
    ap.add_argument("--garment-photo-type", default="auto", choices=lt.GARMENT_PHOTO_TYPES + ("auto",))
    ap.add_argument("--steps", type=int, default=30, help="20=빠름, 30=보통, 50=고품질")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--guidance-scale", type=float, default=1.5)
    ap.add_argument("--no-segmentation-free", action="store_true",
                    help="원래 옷 영역을 지우고 생성(segmentation_free=False). 기본은 켬")
    ap.add_argument("--limit", type=int, default=None, help="최대 조합 수")
    ap.add_argument("--out", default=None, help="출력 폴더 (기본 outputs/batch_<시각>). 기존 폴더면 이어서 실행")
    ap.add_argument("--dry-run", action="store_true", help="조합만 출력하고 종료")
    args = ap.parse_args(argv)

    persons = list_images(SAMPLES / "person")
    garments: list[tuple[Path, str]] = []
    for cat in lt.CATEGORIES:
        if args.category and cat != args.category:
            continue
        garments += [(g, cat) for g in list_images(SAMPLES / "garment" / cat)]
    if args.person:
        persons = [p for p in persons if args.person.lower() in p.name.lower()]
    if args.garment:
        garments = [(g, c) for g, c in garments if args.garment.lower() in g.name.lower()]

    if not persons:
        print(f"사람 사진 없음: {SAMPLES / 'person'}", file=sys.stderr)
        return 2
    if not garments:
        print(f"옷 사진 없음: {SAMPLES / 'garment'}/{{tops,bottoms,one-pieces}}/", file=sys.stderr)
        return 2

    combos = [(p, g, c) for p in persons for g, c in garments]
    if args.limit:
        combos = combos[: args.limit]
    print(f"사람 {len(persons)}장 x 옷 {len(garments)}장 = {len(combos)}개 조합")
    for p, g, c in combos:
        print(f"  {p.name}  +  {c}/{g.name}")
    if args.dry_run:
        return 0

    out_dir = Path(args.out) if args.out else lt.OUT_DIR / f"batch_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"출력: {out_dir}")
    print(f"GPU : {lt.gpu_info()}")

    csv_path = out_dir / "results.csv"
    # 같은 폴더에 이어서 실행할 때 이전 결과를 비교표에 유지
    rows: list[dict] = []
    if csv_path.exists():
        with csv_path.open(encoding="utf-8-sig") as f:
            rows = [dict(r) for r in csv.DictReader(f)]
    FIELDS = ["person", "garment", "category", "steps", "seed", "person_thumb", "garment_thumb",
              "segmentation_free", "garment_photo_type", "elapsed_sec", "result_rel", "error"]
    for r in rows:
        for k in FIELDS:
            r.setdefault(k, "")
    t_all = time.time()
    for i, (p, g, c) in enumerate(combos, 1):
        seg_tag = "" if not args.no_segmentation_free else "__nosegfree"
        name = f"{p.stem}__{c}__{g.stem}__s{args.seed}{seg_tag}"
        result_path = out_dir / f"{name}.png"
        row = {
            "person": p.name, "garment": g.name, "category": c, "steps": args.steps, "seed": args.seed,
            "person_thumb": thumb(p, out_dir / "thumbs" / f"p_{p.stem}.jpg").relative_to(out_dir).as_posix(),
            "garment_thumb": thumb(g, out_dir / "thumbs" / f"g_{c}_{g.stem}.jpg").relative_to(out_dir).as_posix(),
            "segmentation_free": not args.no_segmentation_free,
            "garment_photo_type": args.garment_photo_type, "elapsed_sec": "", "result_rel": "", "error": "",
        }
        if result_path.exists():
            print(f"[{i}/{len(combos)}] 건너뜀(이미 있음): {result_path.name}")
            if not any(r["result_rel"] == result_path.name for r in rows):
                row["result_rel"] = result_path.name
                rows.append(row)
            continue
        rows = [r for r in rows if r["result_rel"] != result_path.name]
        print(f"[{i}/{len(combos)}] {p.name} + {c}/{g.name} ...", flush=True)
        try:
            res = lt.run_tryon(
                p, g, category=c, garment_photo_type=args.garment_photo_type,
                num_timesteps=args.steps, seed=args.seed, guidance_scale=args.guidance_scale,
                segmentation_free=not args.no_segmentation_free,
            )
            res.images[0].save(result_path)
            row.update(result_rel=result_path.name, elapsed_sec=f"{res.elapsed_sec:.1f}",
                       garment_photo_type=res.garment_photo_type)
            print(f"    -> {result_path.name}  ({res.elapsed_sec:.1f}s, {res.garment_photo_type}, "
                  f"VRAM {res.peak_vram_gb:.2f}GB)" if res.peak_vram_gb else f"    -> {result_path.name}")
        except Exception as e:  # noqa: BLE001
            row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"    !! 실패: {row['error']}", file=sys.stderr)
        rows.append(row)
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        write_index(out_dir, rows)

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    write_index(out_dir, rows)
    ok = sum(1 for r in rows if r["result_rel"])
    print(f"\n완료: {ok}/{len(rows)} 성공(폴더 누적), 총 {time.time() - t_all:.0f}s")
    print(f"비교표: {out_dir / 'index.html'}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
