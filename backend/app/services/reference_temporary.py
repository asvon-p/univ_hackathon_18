"""Reference-only model scratch space; recover orphan crops after a worker/PC crash."""

import logging
import shutil
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)


def work_root():
    root = Path(tempfile.gettempdir()) / "fitjang-reference-analysis"
    root.mkdir(exist_ok=True)
    return root


def expire_scratch(max_age_seconds=1800):
    root = work_root().resolve()
    removed = 0
    for folder in root.glob("task-*"):
        try:
            if folder.is_symlink() or not folder.resolve().is_relative_to(root) or not folder.is_dir():
                continue
            if time.time() - folder.stat().st_mtime < max_age_seconds:
                continue
            shutil.rmtree(folder)
            removed += 1
        except OSError:
            log.warning("참고 분석 임시 파일 정리 보류; 다음 주기에 재시도")
    return removed
