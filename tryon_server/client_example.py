"""시착 API 호출 예제 (다른 PC/앱에서 이 파일만 가져다 쓰면 됨. 의존성: requests).

  python client_example.py --server http://127.0.0.1:8000 --person me.jpg --garment shirt.jpg --category tops
  python client_example.py ... --sync            # 완료까지 기다렸다가 바로 PNG 수신
  python client_example.py ... --api-key XXXX    # 서버에 TRYON_API_KEY 가 설정된 경우
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

try:  # 같은 폴더의 .env 가 있으면 TRYON_API_KEY 를 읽는다 (python-dotenv 없으면 무시)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def tryon(server: str, person: str, garment: str, category: str, *, api_key: str = "",
          garment_photo_type: str = "auto", steps: int = 30, seed: int = 42,
          segmentation_free: bool = True, sync: bool = False, out: str | None = None,
          poll_sec: float = 3.0, timeout: float = 600) -> Path:
    server = server.rstrip("/")
    headers = {"X-API-Key": api_key} if api_key else {}
    data = {
        "category": category, "garment_photo_type": garment_photo_type, "num_timesteps": steps,
        "seed": seed, "segmentation_free": str(segmentation_free).lower(),
    }
    with open(person, "rb") as fp, open(garment, "rb") as fg:
        files = {"person": (Path(person).name, fp), "garment": (Path(garment).name, fg)}
        r = requests.post(f"{server}/tryon", params={"sync": str(sync).lower()},
                          files=files, data=data, headers=headers, timeout=timeout)
    r.raise_for_status()

    if sync:
        job_id = r.headers.get("X-Job-Id", "sync")
        dest = Path(out or f"tryon_{job_id}.png")
        dest.write_bytes(r.content)
        print(f"완료 (sync): job={job_id}, {r.headers.get('X-Elapsed-Sec')}s -> {dest}")
        return dest

    job = r.json()
    job_id = job["job_id"]
    print(f"등록됨: job={job_id}, 대기 순번={job.get('queue_position')}")
    t0 = time.time()
    while True:
        s = requests.get(f"{server}/jobs/{job_id}", headers=headers, timeout=30).json()
        st = s["status"]
        if st == "done":
            img = requests.get(s["result_url"], headers=headers, timeout=60)
            img.raise_for_status()
            dest = Path(out or f"tryon_{job_id}.png")
            dest.write_bytes(img.content)
            print(f"완료: {s['elapsed_sec']}s, category={s['category']}, "
                  f"garment_photo_type={s['garment_photo_type']} -> {dest}")
            return dest
        if st == "failed":
            raise RuntimeError(f"실패: {s.get('error')}")
        print(f"  [{time.time() - t0:5.1f}s] {st} (순번 {s.get('queue_position')})")
        if time.time() - t0 > timeout:
            raise TimeoutError(job_id)
        time.sleep(poll_sec)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FASHN VTON 로컬 API 호출 예제")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--person", required=True)
    ap.add_argument("--garment", required=True)
    ap.add_argument("--category", required=True, choices=["tops", "bottoms", "one-pieces"])
    ap.add_argument("--garment-photo-type", default="auto", choices=["auto", "model", "flat-lay"])
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-segmentation-free", action="store_true")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--api-key", default=os.getenv("TRYON_API_KEY", ""),
                    help="생략 시 환경변수/.env 의 TRYON_API_KEY 사용")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    try:
        tryon(a.server, a.person, a.garment, a.category, api_key=a.api_key,
              garment_photo_type=a.garment_photo_type, steps=a.steps, seed=a.seed,
              segmentation_free=not a.no_segmentation_free, sync=a.sync, out=a.out)
    except requests.HTTPError as e:
        print(f"HTTP 오류: {e.response.status_code} {e.response.text[:300]}", file=sys.stderr)
        return 1
    except (RuntimeError, TimeoutError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
