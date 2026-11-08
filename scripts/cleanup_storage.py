"""DB에 기록된 기한 도래 파일 정리 작업을 한 번 처리합니다(임의 파일 탐색/삭제 없음)."""

import json

from backend.app.services.storage_cleanup import process_pending_deletions

if __name__ == "__main__":
    print(json.dumps(process_pending_deletions()))
