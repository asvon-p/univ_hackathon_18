"""Owned, confirmed day-plan cards. Purpose-only labels avoid collecting private appointment names."""

from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from shared.day_plans import ENVIRONMENT_LABELS, PURPOSE_LABELS, DayPlanInput

from .common import TOKEN_COOKIE, go_login, http_status, logged_out, register_templates, render
from .connect import USE_MOCK, ApiError
from .connect import request as api_request
from .recommendation_navigation import after_schedule_delete, safe_back, with_schedule

router = APIRouter()
FIELDS = (
    "start",
    "end",
    "purpose",
    "environment",
    "intensity",
    "priority",
    "outdoor_minutes",
    "temperature_min_c",
    "temperature_max_c",
    "required_style",
)


async def list_plans(token):
    if USE_MOCK:
        return []
    data = await api_request("GET", "/day-plans", token=token)
    return data.get("items") or []


def blank_event():
    return {
        "start": "09:00",
        "end": "10:00",
        "purpose": "class",
        "environment": "indoor",
        "intensity": "normal",
        "priority": "normal",
    }


def display(plan):
    if not plan:
        return {
            "date": datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
            "timezone": "Asia/Seoul",
            "events": [blank_event()],
        }
    return {
        **plan,
        "events": [
            {
                **e,
                "start": e["start_at"][11:16],
                "end": e["end_at"][11:16],
            }
            for e in plan["events"]
        ],
    }


@router.get("/day-plans")
async def page(request: Request, edit: str = "", back: str = "/recommend"):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    back = safe_back(back)
    try:
        plans = await list_plans(token)
        plan = await api_request("GET", f"/day-plans/{edit}", token=token) if edit else None
        return render(
            request,
            "day_plans.html",
            active="recommend",
            plans=plans,
            form=display(plan),
            purposes=PURPOSE_LABELS,
            environments=ENVIRONMENT_LABELS,
            back=back,
            with_schedule=with_schedule,
        )
    except ApiError as exc:
        if exc.status == 401:
            return logged_out()
        return render(
            request,
            "day_plans.html",
            status=http_status(exc),
            plans=[],
            form=display(None),
            purposes=PURPOSE_LABELS,
            environments=ENVIRONMENT_LABELS,
            error=exc.message,
            back=back,
            with_schedule=with_schedule,
        )


@router.post("/day-plans/save")
async def save(request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    data = await request.form()
    back = safe_back(str(data.get("back", "/recommend")))
    rows = [
        {key: data.getlist(key)[i] if i < len(data.getlist(key)) else "" for key in FIELDS}
        for i in range(len(data.getlist("start")))
    ]
    form = {
        "date": data.get("date", ""),
        "timezone": data.get("timezone", "Asia/Seoul"),
        "events": rows,
        "id": data.get("id", ""),
        "version": data.get("version", ""),
    }
    try:
        zone = ZoneInfo(form["timezone"])
        events = []
        for row in rows:
            event = {k: row[k] for k in ("purpose", "environment", "intensity", "priority")}
            for key in ("start", "end"):
                if row[key] == "00:00" and key == "end":
                    from datetime import timedelta

                    dt = datetime.fromisoformat(form["date"]) + timedelta(days=1)
                else:
                    dt = datetime.fromisoformat(form["date"] + "T" + row[key])
                event[key + "_at"] = dt.replace(tzinfo=zone).isoformat()
            for key in ("temperature_min_c", "temperature_max_c"):
                event[key] = float(row[key]) if row[key] else None
            event["outdoor_minutes"] = int(row["outdoor_minutes"]) if row["outdoor_minutes"] else None
            event["required_style"] = row["required_style"] or None
            events.append(event)
        body = DayPlanInput(date=form["date"], timezone=form["timezone"], events=events).model_dump(
            mode="json"
        )
        if form["id"]:
            body["expected_version"] = int(form["version"])
        plan = await api_request(
            "PATCH" if form["id"] else "POST",
            f"/day-plans/{form['id']}" if form["id"] else "/day-plans",
            token=token,
            json=body,
        )
        return RedirectResponse(with_schedule(back, plan["id"], plan["version"]), status_code=303)
    except (ValueError, ValidationError, KeyError) as exc:
        error = (
            str(exc.errors()[0]["msg"])
            if isinstance(exc, ValidationError)
            else "날짜·시작/종료 시각·숫자를 확인해 주세요"
        )
        status = 422
    except ApiError as exc:
        if exc.status == 401:
            return logged_out()
        error, status = exc.message, http_status(exc)
    try:
        plans = await list_plans(token)
    except ApiError:
        plans = []
    return render(
        request,
        "day_plans.html",
        status=status,
        active="recommend",
        plans=plans,
        form=form,
        purposes=PURPOSE_LABELS,
        environments=ENVIRONMENT_LABELS,
        error=error,
        back=back,
        with_schedule=with_schedule,
    )


@router.post("/day-plans/delete")
async def delete(request: Request):
    token = request.cookies.get(TOKEN_COOKIE)
    if not token:
        return go_login(request)
    data = await request.form()
    back = safe_back(str(data.get("back", "/recommend")))
    try:
        await api_request(
            "DELETE",
            f"/day-plans/{data.get('id')}?expected_version={int(data.get('version', 0))}",
            token=token,
        )
        back = after_schedule_delete(back, str(data.get("id")))
        return RedirectResponse("/day-plans?" + urlencode({"back": back}), status_code=303)
    except (ApiError, ValueError) as exc:
        if isinstance(exc, ApiError) and exc.status == 401:
            return logged_out()
        return render(
            request,
            "day_plans.html",
            status=http_status(exc) if isinstance(exc, ApiError) else 422,
            plans=[],
            form=display(None),
            purposes=PURPOSE_LABELS,
            environments=ENVIRONMENT_LABELS,
            error=exc.message if isinstance(exc, ApiError) else "일정 버전을 확인하세요",
            back=back,
            with_schedule=with_schedule,
        )


HTML = """{% extends "app_base.html" %}
{% block title %}하루 일정{% endblock %}
{% block page_head %}<style>
.day-editor{max-width:640px;margin:auto}.day-editor input,.day-editor select{width:100%;min-width:0;min-height:44px;padding:8px;border:1px solid var(--line);border-radius:8px;font:inherit;background:#fff;box-sizing:border-box}.day-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.day-card{border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}.day-editor label{display:block;font-size:.9rem}.day-editor button{min-height:44px}.day-editor .hint{line-height:1.6}.saved-plan{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid var(--line)}
</style>{% endblock %}
{% block content %}<div class="day-editor"><h1>하루 일정에 맞추기</h1>
<p>같은 상의·하의로 하루를 보내고, 구간별로 아우터를 어떻게 입을지 확인해요.</p>
<p class="hint">고른 옷·참고 사진·선호 스타일·기온은 돌아가도 유지돼요. 일정을 저장하면 해당 일정이 선택돼요.</p>
<p class="hint">최대 10개 · 겹치지 않는 하루 일정만 입력해요. 장소·수업명·개인 약속 이름은 입력하지 않아도 돼요. 기온은 수동 입력이며 예보가 아니에요. 혼합 일정의 기온은 야외 구간 기준이에요.</p>
{% if error %}<div class="alert" role="alert">{{ error }}</div>{% endif %}
<form method="post" action="/day-plans/save" id="day-form">
<input type="hidden" name="back" value="{{back}}">
<input type="hidden" name="id" value="{{ form.id or '' }}"><input type="hidden" name="version" value="{{ form.version or '' }}">
<div class="day-grid"><label>날짜<input type="date" name="date" value="{{ form.date }}" required></label><label>시간대<input name="timezone" value="{{ form.timezone }}" required></label></div>
<div id="day-events">{% for e in form.events %}<fieldset class="day-card"><legend>일정 <span class="event-number">{{ loop.index }}</span></legend><div class="day-grid">
<label>시작<input name="start" type="time" value="{{ e.start }}" required></label><label>종료<input name="end" type="time" value="{{ e.end }}" required><small>00:00은 다음 날 자정</small></label>
<label>목적<select name="purpose">{% for k,v in purposes.items() %}<option value="{{k}}" {% if e.purpose==k %}selected{% endif %}>{{v}}</option>{% endfor %}</select></label>
<label>환경<select name="environment">{% for k,v in environments.items() %}<option value="{{k}}" {% if e.environment==k %}selected{% endif %}>{{v}}</option>{% endfor %}</select></label>
<label>활동 강도<select name="intensity">{% for k,v in [('low','낮음'),('normal','보통'),('high','높음')] %}<option value="{{k}}" {% if e.intensity==k %}selected{% endif %}>{{v}}</option>{% endfor %}</select></label>
<label>중요도<select name="priority"><option value="normal">보통</option><option value="high" {% if e.priority=='high' %}selected{% endif %}>우선 고려</option></select></label>
<label>최저 기온 °C (선택)<input name="temperature_min_c" type="number" step="0.1" min="-50" max="60" value="{{ e.temperature_min_c if e.temperature_min_c is defined and e.temperature_min_c is not none else '' }}"></label>
<label>최고 기온 °C (선택)<input name="temperature_max_c" type="number" step="0.1" min="-50" max="60" value="{{ e.temperature_max_c if e.temperature_max_c is defined and e.temperature_max_c is not none else '' }}"></label>
<label>야외 시간 (혼합일 때, 분)<input name="outdoor_minutes" type="number" min="0" max="1440" value="{{e.outdoor_minutes if e.outdoor_minutes is defined and e.outdoor_minutes is not none else ''}}"></label>
<label>확인한 상·하의 태그 조건<select name="required_style"><option value="">없음</option><option value="formal" {% if e.required_style=='formal' %}selected{% endif %}>둘 다 포멀</option><option value="sporty" {% if e.required_style=='sporty' %}selected{% endif %}>둘 다 스포티</option></select></label>
</div><p class="hint">실내 기온을 모르면 비워 주세요. 복장 태그 조건은 자동 적용하지 않으며, 실제 행사 규정은 직접 확인해 주세요.</p><button type="button" class="link-btn remove-event">이 일정 빼기</button></fieldset>{% endfor %}</div>
<button type="button" class="btn" id="add-event">일정 추가</button><button class="btn btn-primary" type="submit">일정 저장하고 코디 고르기</button>
</form><p><a href="{{back}}">저장하지 않고 코디 추천으로 돌아가기</a> · <a href="/day-plans?back={{back|urlencode}}">새 일정 입력</a></p>
<h2 style="margin-top:32px">저장한 일정</h2>{% for p in plans %}<div class="saved-plan"><span>{{p.date}} · {{p.events|length}}개</span><a href="{{with_schedule(back,p.id,p.version)}}">코디 추천</a><a href="/day-plans?edit={{p.id}}&back={{back|urlencode}}">수정</a><form action="/day-plans/delete" method="post"><input type="hidden" name="back" value="{{back}}"><input type="hidden" name="id" value="{{p.id}}"><input type="hidden" name="version" value="{{p.version}}"><button class="link-btn" type="submit">삭제</button></form></div>{% else %}<p>저장한 일정이 없어요.</p>{% endfor %}<p class="hint">삭제하면 과거 추천의 일정 사본과 일정별 설명도 정리해요. 옷과 코디 의류 구성은 유지돼요. 선택 중인 일정을 삭제하면 그 일정 선택만 해제돼요.</p><a href="{{back}}">코디 추천으로 돌아가기</a></div>{% endblock %}
{% block script %}<script nonce="{{ request.state.csp_nonce | default('') }}">
const events=document.getElementById('day-events');function renumber(){events.querySelectorAll('.event-number').forEach((n,i)=>n.textContent=i+1);document.getElementById('add-event').disabled=events.children.length>=10;}
document.getElementById('add-event').addEventListener('click',()=>{if(events.children.length>=10)return;const card=events.firstElementChild.cloneNode(true);card.querySelectorAll('input').forEach(e=>e.value='');card.querySelectorAll('select').forEach(e=>e.selectedIndex=0);card.querySelector('[name=intensity]').value='normal';events.append(card);renumber();});
events.addEventListener('click',e=>{if(e.target.classList.contains('remove-event')&&events.children.length>1){e.target.closest('fieldset').remove();renumber();}});renumber();
</script>{% endblock %}"""
register_templates({"day_plans.html": HTML})
