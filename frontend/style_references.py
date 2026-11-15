import json
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError

from shared.style_references import ReferenceConfirmation, RegionAnalysis

from .common import TOKEN_COOKIE, go_login, http_status, logged_out, register_templates, render
from .connect import USE_MOCK, ApiError, request_bytes
from .connect import request as api_request
from .recommendation_navigation import safe_back

router = APIRouter()
CATEGORIES = ("top", "bottom", "outerwear")


async def list_references(token):
    if USE_MOCK:
        return []
    data = await api_request("GET", "/style-references", token=token)
    return data.get("items") or []


def error_page(request, exc, *, reference=None, back="/recommend"):
    if isinstance(exc, ApiError) and exc.status == 401:
        return logged_out()
    error = (
        exc.message
        if isinstance(exc, ApiError)
        else (exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else "입력값을 확인해 주세요")
    )
    return render(
        request,
        "style_reference.html",
        status=http_status(exc) if isinstance(exc, ApiError) else 422,
        active="recommend",
        reference=reference,
        refs=[],
        by_cat={f["category"]: f for f in reference["features"]} if reference else {},
        error=error,
        back=safe_back(back),
        categories=CATEGORIES,
    )


@router.get("/style-references")
async def page(request: Request, back: str = "/recommend"):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        return render(
            request,
            "style_reference.html",
            active="recommend",
            refs=await list_references(token),
            reference=None,
            back=safe_back(back),
            categories=CATEGORIES,
        )
    except ApiError as exc:
        return error_page(request, exc, back=back)


@router.post("/style-references/upload")
async def upload(request: Request, file: UploadFile):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    form = await request.form()
    back = safe_back(str(form.get("back", "/recommend")))
    try:
        content = await file.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise ApiError(413, "사진은 10MB 이하로 선택해 주세요")
        reference = await api_request(
            "POST",
            "/style-references",
            token=token,
            files={"file": ("reference.jpg", content, file.content_type or "application/octet-stream")},
        )
        return RedirectResponse(
            f"/style-references/{reference['id']}?" + urlencode({"back": back}), status_code=303
        )
    except ApiError as exc:
        return error_page(request, exc, back=back)
    finally:
        await file.close()


@router.get("/style-references/{reference_id}/image")
async def image(reference_id: UUID, request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return Response(status_code=401)
    try:
        data, content_type = await request_bytes(
            "GET", f"/style-references/{reference_id}/image", token=token
        )
        return Response(
            data,
            media_type=content_type,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    except ApiError as exc:
        return Response(status_code=http_status(exc))


@router.get("/style-references/{reference_id}")
async def detail(reference_id: UUID, request: Request, back: str = "/recommend"):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        reference = await api_request("GET", f"/style-references/{reference_id}", token=token)
        return render(
            request,
            "style_reference.html",
            active="recommend",
            reference=reference,
            refs=[],
            by_cat={f["category"]: f for f in reference["features"]},
            back=safe_back(back),
            categories=CATEGORIES,
        )
    except ApiError as exc:
        return error_page(request, exc, back=back)


@router.post("/style-references/{reference_id}/{action}")
async def change(reference_id: UUID, action: str, request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    form = await request.form()
    back = safe_back(str(form.get("back", "/recommend")))
    reference = None
    try:
        reference = await api_request("GET", f"/style-references/{reference_id}", token=token)
        version = int(form.get("version", "0"))
        if action == "analyze":
            selected = str(form.get("regions_json", ""))
            regions = (
                json.loads(selected)
                if selected
                else [
                    {
                        "category": form.get("category"),
                        **{key: float(form.get(key, "")) / 100 for key in ("x", "y", "width", "height")},
                    }
                ]
            )
            body = RegionAnalysis(expected_version=version, regions=regions)
            await api_request(
                "POST",
                f"/style-references/{reference_id}/analyze",
                token=token,
                json=body.model_dump(),
                timeout=75,
            )
        elif action == "confirm":
            included = form.getlist("include")
            if any(cat not in CATEGORIES for cat in included):
                raise ValueError
            features = [
                {
                    "category": cat,
                    "color": form.get(cat + "_color", "unknown"),
                    "pattern": form.get(cat + "_pattern", "unknown"),
                    "subcategory": form.get(cat + "_subcategory", "") or None,
                    "style_candidates": form.getlist(cat + "_styles"),
                }
                for cat in included
            ]
            body = ReferenceConfirmation(
                expected_version=version, features=features, focus=form.get("focus", "balanced")
            )
            reference = await api_request(
                "PATCH", f"/style-references/{reference_id}", token=token, json=body.model_dump()
            )
            query = [
                (k, v)
                for k, v in parse_qsl(urlsplit(back).query, keep_blank_values=True)
                if k not in ("reference_id", "reference_version")
            ]
            query += [("reference_id", str(reference_id)), ("reference_version", str(reference["version"]))]
            return RedirectResponse("/recommend?" + urlencode(query), status_code=303)
        elif action == "delete":
            await api_request(
                "DELETE", f"/style-references/{reference_id}?expected_version={version}", token=token
            )
            return RedirectResponse("/style-references?" + urlencode({"back": back}), status_code=303)
        else:
            return Response(status_code=404)
        return RedirectResponse(
            f"/style-references/{reference_id}?" + urlencode({"back": back}), status_code=303
        )
    except (ApiError, ValidationError, ValueError) as exc:
        if reference is not None:
            try:
                reference = await api_request("GET", f"/style-references/{reference_id}", token=token)
            except ApiError:
                reference = None
        return error_page(request, exc, reference=reference, back=back)


HTML = """{% extends "app_base.html" %}
{% block title %}저장한 스타일 재현{% endblock %}
{% block page_head %}<style>
.reference{max-width:700px;margin:auto}.reference input:not([type=checkbox]),.reference select{width:100%;min-height:44px;min-width:0;font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;box-sizing:border-box;background:white}.reference label{display:block}.ref-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.ref-card{border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}.reference .hint{line-height:1.6}.reference canvas{display:block;width:100%;height:auto;touch-action:none;border-radius:8px;border:1px solid var(--line)}.reference select[multiple]{min-height:110px}.reference button{margin-top:12px;min-height:44px}.saved-ref{display:flex;gap:14px;align-items:center;margin:16px 0}.saved-ref img{width:64px;height:80px;object-fit:contain}.ref-status{padding:10px;background:#f3f4f5;border-radius:8px}
</style>{% endblock %}
{% block content %}<div class="reference"><h1>저장한 스타일을 내 옷으로</h1>
<p>사진 속 의류의 특징을 확인하고, 내 옷장에서 비슷한 조합을 찾아요.</p>
<p class="hint">JPG·PNG·HEIC 사진(10MB 이하)을 한 번만 분석해요. SNS 계정 연결이나 링크 수집은 하지 않아요. 얼굴·계정명·배경을 빼고 한 사람의 옷 영역만 지정해 주세요.</p>
<div class="ref-status" role="note"><strong>사진은 일회성, 의류 키워드만 보관해요.</strong><br>원본은 영역 선택을 위해 최대 30분만 임시 보관하며, 분석을 시작하거나 키워드를 저장하면 삭제 처리해요. 분석 실패·중단 시에도 원본은 다시 보관하지 않아요. 파일 삭제 실패 시 접근을 차단하고 자동 재시도해요.<br>색상·패턴·옷 종류·스타일 키워드는 직접 삭제하거나 탈퇴할 때까지 본인 계정에 보관해 다음 코디에 사용해요. 사진·얼굴 특징은 키워드에 저장하지 않아요.</div>
{% if error %}<div class="alert" role="alert">{{error}}</div>{% endif %}
{% if not reference %}
<form action="/style-references/upload" method="post" enctype="multipart/form-data" data-loading="사진 준비 중…"><input type="hidden" name="back" value="{{back}}"><label>참고 스타일 사진<input type="file" name="file" accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif" required></label><button type="submit" class="btn btn-primary">사진 올리기</button></form>
<h2>보관한 스타일 키워드</h2>{% for r in refs %}<div class="saved-ref">{% if r.image_available %}<img src="/style-references/{{r.id}}/image" alt="분석 전 임시 사진" loading="lazy">{% endif %}<div><a href="/style-references/{{r.id}}?back={{back|urlencode}}">참고 스타일 {{loop.index}} · {{'확인 완료' if r.status=='ready' else '키워드 확인 필요'}}</a><p class="hint">{% for f in r.features %}{{f.category|ko('category')}} · {{f.color|ko('color')}} · {{f.pattern|ko('pattern')}} {% for s in f.style_candidates %}{{s|ko('style')}} {% endfor %}{% endfor %}{% if not r.image_available %} · 원본 보관 종료{% endif %}</p></div></div>{% else %}<p>보관한 스타일 키워드가 없어요.</p>{% endfor %}
{% else %}
<p class="ref-status">{{'특징 확인 완료 — 수정하면 다시 확인해 주세요' if reference.status=='ready' else '영역 분석 후 특징을 확인하거나 직접 입력해 주세요'}}</p>
{% for note in reference.notes %}<p class="hint">{{note}}</p>{% endfor %}
{% if reference.image_available %}
<h2>1. 의류 영역 지정</h2><p class="hint">한 벌을 드래그하고 ‘이 영역 추가’를 눌러 주세요. 상의·하의·아우터 중 보이는 옷을 모두 추가한 후 한 번에 분석하세요. 분석 후에는 원본 없이 키워드만 수정할 수 있어요.</p>
<canvas id="reference-canvas" role="img" aria-label="참고 사진의 의류 영역 선택" data-image="/style-references/{{reference.id}}/image"></canvas>
<form action="/style-references/{{reference.id}}/analyze" method="post" id="region-form" data-loading="의류 영역 분석 중…"><input type="hidden" name="version" value="{{reference.version}}"><input type="hidden" name="back" value="{{back}}">
<input type="hidden" name="regions_json" value=""><div id="selected-regions" aria-live="polite"></div>
{% set last_region=reference.regions[-1] if reference.regions else {} %}
<label>분석할 옷<select name="category">{% for cat in categories %}<option value="{{cat}}" {% if cat==last_region.category %}selected{% endif %}>{{cat|ko('category')}}</option>{% endfor %}</select></label>
<div class="ref-grid">{% for key,label,value in [('x','왼쪽 위치 (%)',10),('y','위쪽 위치 (%)',25),('width','너비 (%)',80),('height','높이 (%)',35)] %}<label>{{label}}<input type="number" name="{{key}}" value="{{value}}" min="0" max="100" step="0.1" required></label>{% endfor %}</div><button type="button" id="add-region" class="btn btn-secondary">이 영역 추가</button> <button type="submit" class="btn btn-primary">선택 영역 한 번에 분석</button></form>
{% else %}<p class="ref-status">원본 보관 종료 · 사진 대신 의류 키워드만 남아 있어요. 키워드를 수정하거나 새로운 참고 사진을 올릴 수 있어요.</p>{% endif %}
<h2>2. 보이는 특징 확인</h2><p class="hint">자동 추정이 다르면 수정해 주세요. 분석 실패 시 직접 입력할 수 있어요. 선택한 종류만 부분 재현하며 핏·소재·정확한 복제품을 보장하지 않아요.</p>
<form action="/style-references/{{reference.id}}/confirm" method="post"><input type="hidden" name="version" value="{{reference.version}}"><input type="hidden" name="back" value="{{back}}">
{% for cat in categories %}{% set f=by_cat.get(cat,{}) %}<fieldset class="ref-card"><legend><label><input type="checkbox" name="include" value="{{cat}}" {% if f %}checked{% endif %}> {{cat|ko('category')}} 특징 사용</label></legend>
{% if f %}<p class="hint">{{'비전 모델 추정' if f.extraction=='vision' else ('픽셀 색상 추정' if f.extraction=='baseline' else '직접 확인한 특징')}} · 확인 후 추천에 사용</p>{% endif %}
<div class="ref-grid"><label>{{cat|ko('category')}} 색상<select name="{{cat}}_color">{% for key,label in LABELS.color.items() %}<option value="{{key}}" {% if key==(f.color or 'unknown') %}selected{% endif %}>{{label}}</option>{% endfor %}</select></label>
<label>{{cat|ko('category')}} 패턴<select name="{{cat}}_pattern">{% for key,label in LABELS.pattern.items() %}<option value="{{key}}" {% if key==(f.pattern or 'unknown') %}selected{% endif %}>{{label}}</option>{% endfor %}</select></label>
<label>{{cat|ko('category')}} 종류 (선택)<input name="{{cat}}_subcategory" value="{{f.subcategory or ''}}" maxlength="50" placeholder="예: shirt, jeans, jacket"></label>
<label>{{cat|ko('category')}} 스타일 (복수 선택)<select name="{{cat}}_styles" multiple>{% for key,label in LABELS.style.items() %}<option value="{{key}}" {% if key in (f.style_candidates or []) %}selected{% endif %}>{{label}}</option>{% endfor %}</select></label></div></fieldset>{% endfor %}
<label>재현할 때 우선할 특징<select name="focus">{% for key,label in [('balanced','색상·패턴·종류·스타일 균형'),('color','색상 중심'),('style','스타일 중심')] %}<option value="{{key}}" {% if reference.focus==key %}selected{% endif %}>{{label}}</option>{% endfor %}</select></label>
<button type="submit" class="btn btn-primary">특징 확인하고 내 옷으로 추천</button></form>
<form action="/style-references/{{reference.id}}/delete" method="post"><input type="hidden" name="version" value="{{reference.version}}"><input type="hidden" name="back" value="{{back}}"><button type="submit" class="link-btn">이 스타일 키워드 삭제</button></form><p class="hint">삭제하면 키워드와 과거 추천 속 참고 정보도 정리해요. 남은 임시 사진이 있다면 함께 삭제해요. 옷장 의류와 하루 일정은 유지돼요.</p>
{% endif %}<p><a href="{{back}}">코디 조건으로 돌아가기</a> · <a href="/style-references?back={{back|urlencode}}">참고 사진 목록</a></p></div>{% endblock %}
{% block script %}{% if reference and reference.image_available %}<script nonce="{{ request.state.csp_nonce | default('') }}">
const canvas=document.getElementById('reference-canvas'),form=document.getElementById('region-form'),ctx=canvas.getContext('2d'),img=new Image();let start=null;
const selected={},labels={top:'상의',bottom:'하의',outerwear:'아우터'};
function region(){return {category:form.elements.category.value,...Object.fromEntries(['x','y','width','height'].map(k=>[k,+form.elements[k].value/100]))};}
function refreshRegions(){const list=document.getElementById('selected-regions');list.replaceChildren();Object.values(selected).forEach(r=>{const p=document.createElement('p'),b=document.createElement('button');p.textContent=labels[r.category]+' 영역 추가됨 ';b.type='button';b.textContent='제외';b.onclick=()=>{delete selected[r.category];refreshRegions();};p.append(b);list.append(p);});form.elements.regions_json.value=JSON.stringify(Object.values(selected));}
document.getElementById('add-region').onclick=()=>{if(!form.reportValidity())return;const r=region();selected[r.category]=r;refreshRegions();};
form.addEventListener('submit',()=>{if(!Object.keys(selected).length){const r=region();selected[r.category]=r;}refreshRegions();});
function draw(){if(!img.complete||!img.naturalWidth)return;ctx.drawImage(img,0,0,canvas.width,canvas.height);ctx.strokeStyle='#e23838';ctx.lineWidth=Math.max(3,canvas.width/160);ctx.strokeRect(+form.elements.x.value/100*canvas.width,+form.elements.y.value/100*canvas.height,+form.elements.width.value/100*canvas.width,+form.elements.height.value/100*canvas.height);}
img.onload=()=>{canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;draw();};img.src=canvas.dataset.image;
form.querySelectorAll('input[type=number]').forEach(input=>input.addEventListener('input',draw));
function point(e){const b=canvas.getBoundingClientRect();return [Math.min(100,Math.max(0,(e.clientX-b.left)/b.width*100)),Math.min(100,Math.max(0,(e.clientY-b.top)/b.height*100))];}
canvas.addEventListener('pointerdown',e=>{start=point(e);canvas.setPointerCapture(e.pointerId);});canvas.addEventListener('pointermove',e=>{if(!start)return;const p=point(e);const vals=[Math.min(start[0],p[0]),Math.min(start[1],p[1]),Math.abs(p[0]-start[0]),Math.abs(p[1]-start[1])];['x','y','width','height'].forEach((k,i)=>form.elements[k].value=(Math.floor(vals[i]*10)/10).toFixed(1));draw();});canvas.addEventListener('pointerup',()=>{start=null;});canvas.addEventListener('pointercancel',()=>{start=null;});
</script>{% endif %}{% endblock %}"""
register_templates({"style_reference.html": HTML})
