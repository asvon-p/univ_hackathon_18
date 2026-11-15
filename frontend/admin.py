from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .common import TOKEN_COOKIE, go_login, register_templates, render
from .connect import ApiError, request_bytes
from .connect import request as api

router = APIRouter()


@router.get("/admin/access")
async def access(request: Request):
    try:
        user = await api("GET", "/auth/me", token=request.cookies.get(TOKEN_COOKIE))
        return JSONResponse(
            {"admin": bool(user.get("is_admin"))}, headers={"Cache-Control": "private, no-store"}
        )
    except ApiError:
        return JSONResponse({"admin": False}, headers={"Cache-Control": "private, no-store"})


@router.get("/admin")
async def dashboard(request: Request, offset: int = 0, owner: UUID | None = None):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        usage = await api("GET", "/admin/overview", token=token, params={"offset": max(0, offset)})
        artifacts = await api(
            "GET",
            "/admin/artifacts",
            token=token,
            params={"offset": max(0, offset), **({"owner": str(owner)} if owner else {})},
        )
    except ApiError as exc:
        return Response(
            exc.message, status_code=exc.status or 503, headers={"Cache-Control": "private, no-store"}
        )
    response = render(
        request,
        "admin.html",
        usage=usage,
        artifacts=artifacts,
        offset=max(0, offset),
        owner=str(owner) if owner else "",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.get("/admin/media/{group}/{identifier}/{kind}")
async def media(request: Request, group: str, identifier: str, kind: str):
    from uuid import UUID

    if group not in ("garments", "results") or kind not in (
        "original",
        "cutout",
        "layer",
        "result",
        "top",
        "bottom",
        "outerwear",
    ):
        return Response(status_code=404)
    try:
        identifier = str(UUID(identifier))
        data, mime = await request_bytes(
            "GET", f"/admin/media/{group}/{identifier}/{kind}", token=request.cookies.get(TOKEN_COOKIE)
        )
    except ValueError:
        return Response(status_code=404)
    except ApiError as exc:
        return Response(exc.message, status_code=exc.status or 503)
    return Response(
        data,
        media_type=mime,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


register_templates(
    {
        "admin.html": """{% extends "app_base.html" %}
{% block title %}관리자{% endblock %}
{% block content %}<h1>관리자 · API 사용량과 처리 결과</h1>
<p>기록 기능 적용 이후 이 서비스에서 발생한 호출입니다. 과거 호출과 다른 프로젝트의 사용량은 포함하지 않습니다.</p>
<p><b>호출 {{usage.calls}}회 · 입력 {{usage.input_tokens}} · 캐시 입력 {{usage.cached_tokens}} · 출력 {{usage.output_tokens}} 토큰</b></p>
<p>사용 금액 추정(USD): <b>{{ '$%.8f'|format(usage.estimated_usd) if usage.estimated_usd is not none else '집계 전' }}</b> · 비용 미확인 {{usage.unknown_cost_calls}}회</p>
<p>일반 처리 기준 공식 단가로 계산한 추정치입니다. 실패·응답 유실·지원하지 않는 과금 조건은 미확인으로 남습니다.
실제 청구액·세금·크레딧은 <a href="https://platform.openai.com/usage" target="_blank" rel="noreferrer">OpenAI 사용량 대시보드</a>에서 확인하세요.</p>
<details><summary>최근 API 호출</summary><table><tr><th>일시</th><th>모델</th><th>상태</th><th>입력/캐시/출력</th><th>USD 추정</th></tr>
{% for e in usage.events %}<tr><td>{{e.created_at}}</td><td>{{e.model}}</td><td>{{e.status}}</td><td>{{e.input}} / {{e.cached}} / {{e.output}}</td><td>{{e.usd if e.usd is not none else '미확인'}}</td></tr>{% endfor %}</table></details>
<h2>의류 처리 이미지</h2><form method="get"><label>사용자 ID로 필터 <input name="owner" value="{{owner}}" placeholder="사용자 UUID"></label><button>적용</button> <a href="/admin">전체</a></form><p>원본은 유지됩니다. 정제 이미지는 저장된 누끼와 앞여밈 설정으로 생성합니다.</p>
{% for g in artifacts.garments %}<details><summary>{{g.name}} · {{g.owner}} {% if g.opening %}· 앞여밈 지정{% endif %}</summary>
<div style="display:flex;gap:12px;flex-wrap:wrap">{% for kind,label in [('original','원본'),('cutout','누끼 · 없으면 원본'),('layer','정제·안쪽 제거')] %}<figure style="margin:0"><figcaption>{{label}}</figcaption><img loading="lazy" style="width:180px;max-height:260px;object-fit:contain;background:#eee" src="/admin/media/garments/{{g.id}}/{{kind}}"></figure>{% endfor %}</div></details>{% endfor %}
<h2>착샷 처리 결과</h2><p>새 결과는 의류 단계별로 보관합니다. 과거에 저장하지 않은 단계와 삭제된 사진은 표시되지 않습니다. 전신 사진·착샷 완전 삭제 또는 회원 탈퇴 시 이 결과도 함께 삭제됩니다.</p>
{% for r in artifacts.results %}<details><summary>{{r.created_at}} · {{r.names|join(', ')}} · {{r.layering_status}}</summary><div style="display:flex;gap:12px;flex-wrap:wrap">
{% for kind,label in [('bottom','하의 적용'),('top','상의 적용'),('outerwear','아우터 적용'),('result','최종 결과')] %}<figure style="margin:0"><figcaption>{{label}}</figcaption><img loading="lazy" style="width:180px" alt="보관 이미지가 없을 수 있습니다" src="/admin/media/results/{{r.id}}/{{kind}}"></figure>{% endfor %}</div></details>{% endfor %}
<p><a href="/admin?offset={{[0,offset-30]|max}}{% if owner %}&owner={{owner}}{% endif %}">이전</a> · <a href="/admin?offset={{offset+30}}{% if owner %}&owner={{owner}}{% endif %}">다음</a></p>{% endblock %}"""
    }
)
