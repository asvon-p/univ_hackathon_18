"""Shared render metadata; legacy results remain accessible in the owner's history."""

RENDER_VERSION = "open-shirt-v4.1"


def layering_warnings(status):
    if status == "approximate":
        return [
            "앞여밈 위치를 추정해 상의를 보존했습니다. "
            "옷 상세의 ‘누끼 정제·아우터 안쪽 설정’에서 실제 열린 영역을 지정해 주세요."
        ]

    return (
        [
            "아우터의 열린 부분을 구분하기 어려워 상의를 보존했습니다. "
            "앞이 넓게 열리고 안쪽 배경이 보이는 아우터 사진으로 다시 시도해 주세요."
        ]
        if status == "skipped"
        else []
    )
