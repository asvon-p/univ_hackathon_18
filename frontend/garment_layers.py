"""Automatic opening preview with optional touch corrections; originals are immutable."""

import json
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from .common import TOKEN_COOKIE, go_login, register_templates, render
from .connect import ApiError, request_bytes
from .connect import request as api

router = APIRouter()


@router.get("/garments/{gid}/layers")
async def editor(request: Request, gid: UUID, saved: bool = False):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    try:
        config = await api("GET", f"/garments/{gid}/layer-config", token=token)
    except ApiError as exc:
        return Response(exc.message, status_code=exc.status or 503)
    return render(request, "garment_layers.html", gid=str(gid), config=config, saved=saved)


@router.post("/garments/{gid}/layers")
async def save(request: Request, gid: UUID):
    try:
        form = await request.form()
        mode = str(form.get("mode", "manual"))
        points = json.loads(str(form.get("points", "[]")))
        await api(
            "PUT",
            f"/garments/{gid}/layer-config",
            token=request.cookies.get(TOKEN_COOKIE),
            json={"points": points, "mode": mode},
        )
    except ValueError:
        return Response("영역 좌표를 확인해 주세요", status_code=422)
    except ApiError as exc:
        return Response(exc.message, status_code=exc.status or 503)
    return RedirectResponse(f"/garments/{gid}/layers?saved=true", status_code=303)


@router.get("/garments/{gid}/layer-preview")
async def preview(request: Request, gid: UUID):
    try:
        data, mime = await request_bytes(
            "GET", f"/garments/{gid}/layer-image", token=request.cookies.get(TOKEN_COOKIE)
        )
        return Response(data, media_type=mime, headers={"Cache-Control": "private, no-store"})
    except ApiError as exc:
        return Response(exc.message, status_code=exc.status or 503)


register_templates(
    {
        "garment_layers.html": """{% extends "app_base.html" %}
{% block title %}누끼 정제·아우터 안쪽 설정{% endblock %}
{% block content %}<h1>누끼 정제·아우터 안쪽 설정</h1>
<p>원본 사진과 의류 속성은 유지됩니다. 작은 잡티와 가장자리를 정제한 투명 이미지를 착샷에 사용합니다.</p>
{% if saved %}<p role="status">설정을 저장했습니다. 다음 착샷 생성부터 반영됩니다.</p>{% endif %}
{% if config.category == 'outerwear' %}
{% if config.status == 'detected' %}<p role="status"><b>안쪽 영역을 자동으로 찾았어요.</b> 아래 투명 처리 결과가 맞으면 저장하거나 점을 찍을 필요 없이 그대로 착샷을 생성하세요.</p>
{% elif config.status == 'manual' %}<p role="status"><b>직접 수정한 영역을 사용하고 있어요.</b> 자동 인식이 수동 설정을 덮어쓰지 않습니다.</p>
{% elif config.status == 'off' %}<p role="status">안쪽 투명 처리를 사용하지 않습니다.</p>
{% else %}<p role="status"><b>열린 안쪽 영역을 확실하게 찾지 못했어요.</b> 원단을 임의로 잘라내지 않았습니다. 사진이 열린 아우터라면 아래에서 영역을 수정해 주세요. 닫힌 아우터라면 ‘안쪽 제거 안 함’을 선택하세요.</p>{% endif %}
{% endif %}
<h2>착샷에 사용할 정제 이미지</h2><p>회색 바둑판 부분이 투명 영역입니다. 자동 인식도 틀릴 수 있으니 원단과 라펠이 남아 있는지 확인하세요.</p>
<img src="/garments/{{gid}}/layer-preview" style="width:100%;max-width:480px;background:conic-gradient(#ddd 25%,#fff 0 50%,#ddd 0 75%,#fff 0) 0 0 / 24px 24px" alt="자동 또는 수동 설정으로 정제된 의류 이미지">
{% if config.category == 'outerwear' %}
<details id="opening-editor"><summary>잘못 인식했나요? 안쪽 영역 수정</summary>
<p>아우터는 앞을 열고 정면에서 촬영하세요. 표시된 점을 끌어 옮기거나 초기화 후 테두리를 순서대로 눌러 3~24점을 지정하세요. 라펠과 소매를 남기고 안감·배경이 보이는 중앙 부분만 선택하세요.</p>
<p>빨간 영역을 투명하게 만듭니다. 점이 교차하거나 오목한 영역은 저장할 수 없습니다.</p>
<canvas id="opening" style="display:block;width:100%;max-width:480px;touch-action:none;background:#eee" aria-label="사진에서 제거할 안쪽 영역을 선택"></canvas>
<p id="points-count" aria-live="polite"></p>
<p><button type="button" id="undo-point">마지막 점 취소</button> <button type="button" id="reset-points">영역 초기화</button></p>
<form method="post" id="manual-opening"><input type="hidden" name="mode" value="manual"><input type="hidden" name="points" id="points-value"><button class="btn btn-primary" type="submit">수정한 영역 저장</button></form>
</details>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:16px"><form method="post"><input type="hidden" name="mode" value="auto"><button type="submit">자동 인식으로 되돌리기</button></form>
<form method="post"><input type="hidden" name="mode" value="off"><button type="submit">안쪽 제거 안 함</button></form></div>
<script nonce="{{ request.state.csp_nonce }}">
const points={{ config.points|tojson }}, canvas=document.getElementById('opening'),ctx=canvas.getContext('2d'),photo=new Image();
function draw(){ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(photo,0,0,canvas.width,canvas.height);if(points.length){ctx.beginPath();points.forEach((p,i)=>{const x=p[0]*canvas.width,y=p[1]*canvas.height;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.closePath();ctx.fillStyle='rgba(225,40,40,.32)';ctx.fill();ctx.strokeStyle='#e12828';ctx.lineWidth=3;ctx.stroke();points.forEach(p=>{ctx.beginPath();ctx.arc(p[0]*canvas.width,p[1]*canvas.height,5,0,2*Math.PI);ctx.fillStyle='#e12828';ctx.fill();});}document.getElementById('points-value').value=JSON.stringify(points);document.getElementById('points-count').textContent=points.length+'개 점 선택';}
photo.onload=()=>{canvas.width=480;canvas.height=Math.round(480*photo.naturalHeight/photo.naturalWidth);draw();};
photo.src='/garment-images/{{ gid }}';
let drag=-1,down=null;
function coord(e){const r=canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))];}
canvas.addEventListener('pointerdown',e=>{if(!photo.complete)return;down=coord(e);const r=canvas.getBoundingClientRect();drag=points.findIndex(p=>Math.hypot((p[0]-down[0])*r.width,(p[1]-down[1])*r.height)<18);canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener('pointermove',e=>{if(drag>=0){points[drag]=coord(e);draw();}});
canvas.addEventListener('pointerup',e=>{if(down&&drag<0&&points.length<24){points.push(coord(e));draw();}drag=-1;down=null;});
canvas.addEventListener('pointercancel',()=>{drag=-1;down=null;});
document.getElementById('undo-point').onclick=()=>{points.pop();draw();};document.getElementById('reset-points').onclick=()=>{points.splice(0);draw();};
document.getElementById('manual-opening').onsubmit=e=>{if(points.length<3){e.preventDefault();document.getElementById('points-count').textContent='3점 이상 선택하거나 안쪽 제거 안 함을 선택하세요.';}};
</script>{% endif %}
<p><a href="/garments/{{gid}}">의류 상세로 돌아가기</a></p>{% endblock %}"""
    }
)
