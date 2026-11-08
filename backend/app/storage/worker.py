"""서버 시작 직후와 주기적으로 미완료 파일 정리 작업을 처리합니다."""

import asyncio
import logging

from backend.app.services.storage_cleanup import process_pending_deletions

log = logging.getLogger(__name__)


async def retry_deletions(interval_seconds: float) -> None:
    while True:
        try:
            await asyncio.to_thread(process_pending_deletions)
        except Exception:
            # DB 장애 중에도 job을 삭제하지 않습니다. 다음 주기에 다시 확인합니다.
            log.exception("파일 정리 작업 확인 실패; 다음 주기에 재시도합니다")
        await asyncio.sleep(interval_seconds)
