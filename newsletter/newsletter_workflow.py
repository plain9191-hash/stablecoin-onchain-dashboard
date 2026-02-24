#!/usr/bin/env python3
"""Choi Threads newsletter workflow.

1) Pull RSS feed
2) Keep only last 24h entries
3) Summarize and extract practical adaptation points
4) Send a Gmail email using OAuth2 refresh token
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request
from zoneinfo import ZoneInfo

import feedparser
from dateutil import parser as dt_parser
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


TASK_NAME = "choi_threads_newsletter"
EXPECTED_RSS = "https://rss.app/feeds/PJfFHato1ox9YKyR.xml"
EXPECTED_RECIPIENT = "plain9191@gmail.com"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"


@dataclass
class Entry:
    title: str
    link: str
    published_at: datetime
    summary: str
    view_count: int | None = None


def compact_title(title: str, max_chars: int = 72) -> str:
    clean = strip_html(title)
    clean = re.sub(r"\s+", " ", clean).strip(" -|")
    if not clean:
        return "(제목 없음)"
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "…"


def get_env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name)
    if (value is None or value == "") and default is not None:
        value = default
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def parse_entry_datetime(raw_entry: dict[str, Any]) -> datetime | None:
    candidates = [
        raw_entry.get("published"),
        raw_entry.get("updated"),
        raw_entry.get("created"),
    ]
    for raw in candidates:
        if not raw:
            continue
        try:
            dt = dt_parser.parse(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def load_sent_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        links = data.get("sent_links", [])
        return set(links) if isinstance(links, list) else set()
    except Exception:
        return set()


def save_sent_state(path: Path, sent_links: set[str]) -> None:
    payload = {"sent_links": sorted(sent_links)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_recent_entries(rss_url: str, hours_back: int, max_items: int, already_sent: set[str]) -> list[Entry]:
    feed = feedparser.parse(rss_url)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)

    recent: list[Entry] = []
    for raw in feed.entries:
        published_at = parse_entry_datetime(raw)
        if not published_at:
            continue
        if published_at < cutoff or published_at > now:
            continue

        link = raw.get("link", "").strip()
        if not link or link in already_sent:
            continue

        title = (raw.get("title") or "(제목 없음)").strip()
        summary = (raw.get("summary") or raw.get("description") or "").strip()
        view_count = parse_view_count(raw)
        recent.append(
            Entry(
                title=title,
                link=link,
                published_at=published_at,
                summary=summary,
                view_count=view_count,
            )
        )

    # If view count exists, rank by high views first. Otherwise keep latest first.
    recent.sort(
        key=lambda x: (
            x.view_count is None,
            -(x.view_count or 0),
            -x.published_at.timestamp(),
        )
    )
    return recent[:max_items]


def parse_view_count(raw_entry: dict[str, Any]) -> int | None:
    candidates = [
        "view_count",
        "views",
        "viewCount",
        "engagement",
        "engagement_count",
        "metrics_views",
        "threads_view_count",
    ]
    for key in candidates:
        value = raw_entry.get(key)
        parsed = normalize_int(value)
        if parsed is not None:
            return parsed

    # Fallback: scan any field name containing "view".
    for key, value in raw_entry.items():
        if "view" not in str(key).lower():
            continue
        parsed = normalize_int(value)
        if parsed is not None:
            return parsed
    return None


def normalize_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None
    return None


def summarize_entries(entries: list[Entry], model: str, api_key: str) -> str:
    item_lines: list[str] = []
    for idx, e in enumerate(entries, start=1):
        item_lines.append(
            f"[{idx}]\n"
            f"title: {e.title}\n"
            f"published_utc: {e.published_at.isoformat()}\n"
            f"view_count: {e.view_count if e.view_count is not None else 'unknown'}\n"
            f"link: {e.link}\n"
            f"content_hint: {e.summary[:2000]}\n"
        )

    prompt = (
        "다음은 Choi Threads의 최근 24시간 게시물이다.\n"
        "한국어로 아래 형식만 출력하라.\n"
        "- 각 게시물별: 1) 한줄 요약 2) 적응할 점 2개(실행형)\n"
        "- 마지막: 전체 트렌드 3줄\n"
        "형식 예시:\n"
        "## [번호] 제목\n"
        "- 요약: ...\n"
        "- 적응할 점:\n"
        "  1) ...\n"
        "  2) ...\n"
        "\n"
        "## 전체 트렌드\n"
        "- ...\n"
        "- ...\n"
        "- ...\n\n"
        "입력 데이터:\n"
        + "\n".join(item_lines)
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }

    candidate_models = []
    for m in [model, "gemini-2.0-flash", "gemini-1.5-flash"]:
        if m and m not in candidate_models:
            candidate_models.append(m)

    last_error = "unknown"
    for model_name in candidate_models:
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{url_parse.quote(model_name)}:generateContent?key={url_parse.quote(api_key)}"
        )
        try:
            req = url_request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with url_request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            candidates = data.get("candidates", [])
            if not candidates:
                last_error = f"{model_name}: empty candidates"
                continue
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
            if text:
                return text
            last_error = f"{model_name}: empty text"
        except url_error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = str(exc)
            last_error = f"{model_name}: HTTP {exc.code} {detail[:180]}"
        except (url_error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = f"{model_name}: {type(exc).__name__}"

    print(f"[WARN] Gemini summarize failed. Using fallback summary. Reason: {last_error}")
    return fallback_summary(entries, reason=last_error)


def fallback_summary(entries: list[Entry], reason: str = "") -> str:
    lines: list[str] = []
    for idx, e in enumerate(entries, start=1):
        summary = extract_key_summary(e)
        action_1, action_2 = build_adaptation_points(e)
        lines.append(f"## [{idx}] {compact_title(e.title)}")
        lines.append(f"- 요약: {summary}")
        lines.append("- 적응할 점:")
        lines.append(f"  1) {action_1}")
        lines.append(f"  2) {action_2}")
        lines.append("")

    lines.append("## 전체 트렌드")
    lines.append("- 최근 게시물은 모델 출시/업데이트, 에이전트 자동화, 워크플로우 통합 주제가 반복됩니다.")
    lines.append("- 실무 관점에서는 도입 속도보다 검증 가능한 실험 설계와 운영 가드레일이 성과를 좌우합니다.")
    if reason:
        lines.append(f"- 요약 엔진 상태: Gemini 호출 실패로 추출형 요약 사용 ({reason}).")
    else:
        lines.append("- 요약 엔진 상태: Gemini 호출 실패로 추출형 요약을 사용했습니다.")
    return "\n".join(lines)


def strip_html(text: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", text or "")
    unescaped = html.unescape(no_tags)
    collapsed = re.sub(r"\s+", " ", unescaped).strip()
    return collapsed


def extract_key_summary(entry: Entry, max_chars: int = 180) -> str:
    source = strip_html(entry.summary)
    if not source:
        return f"'{compact_title(entry.title)}' 관련 업데이트로 보이며, 상세 내용은 원문 링크 확인이 필요합니다."

    parts = re.split(r"(?<=[.!?다])\s+", source)
    candidate = ""
    for p in parts:
        p = p.strip()
        if len(p) < 25:
            continue
        candidate = p
        break
    if not candidate:
        candidate = source

    if len(candidate) > max_chars:
        candidate = candidate[: max_chars - 1].rstrip() + "…"
    return candidate


def build_adaptation_points(entry: Entry) -> tuple[str, str]:
    text = f"{entry.title} {strip_html(entry.summary)}".lower()
    if any(k in text for k in ["출시", "공개", "업데이트", "beta", "release", "버전"]):
        return (
            "오늘 안에 신규 기능 3개를 체크리스트로 정리하고 우리 업무 적용 가능 여부를 표시한다.",
            "이번 주에 기존 방식 대비 시간/품질 개선을 확인하는 30분 테스트를 1회 실행한다.",
        )
    if any(k in text for k in ["claude", "grok", "gemini", "llm", "모델", "anthropic", "openai"]):
        return (
            "현재 사용하는 프롬프트 1개를 선택해 모델별 출력 품질을 같은 기준으로 비교한다.",
            "정확도/속도/비용 중 우선순위 1개를 정하고 다음 배포 기준으로 문서화한다.",
        )
    if any(k in text for k in ["figma", "디자인", "ux", "슬라이드", "ppt", "notebooklm"]):
        return (
            "디자인-개발 handoff 과정에서 자동화 가능한 단계 1개를 오늘 바로 파일럿한다.",
            "팀 공용 템플릿에 반영할 규칙 2개를 정리해 다음 작업부터 강제 적용한다.",
        )
    if any(k in text for k in ["로봇", "전장", "규제", "정책", "윤리", "risk", "리스크"]):
        return (
            "관련 이슈를 기술/정책/평판 리스크 3축으로 나눠 팀 위키에 기록한다.",
            "우리 제품에서 동일 리스크가 생길 수 있는 시나리오 1개를 뽑아 대응안을 작성한다.",
        )
    return (
        "원문에서 핵심 주장 1개와 근거 1개를 뽑아 팀 노트에 3줄로 요약한다.",
        "이번 주 업무에 바로 적용 가능한 실험 항목 1개를 정해 완료 기준을 숫자로 적는다.",
    )


def build_email_body(entries: list[Entry], llm_summary: str, hours_back: int) -> str:
    lines: list[str] = []
    lines.append(f"Choi Threads Digest (last {hours_back}h)")
    lines.append("")
    lines.append(f"총 {len(entries)}개 게시물")
    lines.append("")

    for idx, e in enumerate(entries, start=1):
        lines.append(f"[{idx}] {compact_title(e.title)}")
        lines.append(f"- 링크: {e.link}")
        lines.append(f"- 게시시각(UTC): {e.published_at.isoformat()}")
        lines.append("")

    lines.append("===== 요약 및 적응 포인트 =====")
    lines.append(llm_summary)
    lines.append("")
    lines.append(f"Generated at (UTC): {datetime.now(timezone.utc).isoformat()}")

    return "\n".join(lines)


def build_email_html(entries: list[Entry], llm_summary: str, hours_back: int) -> str:
    summary_html = html.escape(llm_summary)
    rows: list[str] = []
    for idx, e in enumerate(entries, start=1):
        title = html.escape(compact_title(e.title))
        link = html.escape(e.link)
        posted = html.escape(e.published_at.isoformat())
        rows.append(
            "<article class=\"card\">"
            f"<strong class=\"title\">[{idx}] {title}</strong>"
            f"<div class=\"link\"><a href=\"{link}\">{link}</a></div>"
            f"<div class=\"meta\">게시시각(UTC): {posted}</div>"
            "</article>"
        )

    generated = html.escape(datetime.now(timezone.utc).isoformat())
    return (
        "<!doctype html>"
        "<html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<style>"
        "body{margin:0;background:#f6f7f9;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}"
        ".wrap{max-width:760px;margin:0 auto;padding:24px 16px 40px;}"
        ".hero{background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;padding:18px 18px 14px;}"
        ".headline{margin:0;font-size:22px;line-height:1.25;letter-spacing:-0.02em;}"
        ".sub{margin:8px 0 0;color:#6b7280;font-size:13px;}"
        ".section{margin-top:16px;}"
        ".card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px;margin:10px 0;}"
        ".title{display:block;font-size:16px;line-height:1.45;}"
        ".link{margin-top:8px;word-break:break-all;}"
        ".link a{color:#0f766e;font-size:14px;font-weight:700;text-decoration:none;}"
        ".link a:hover{text-decoration:underline;}"
        ".meta{margin-top:6px;font-size:12px;color:#6b7280;word-break:break-all;}"
        ".summary{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px;}"
        ".summary pre{margin:0;white-space:pre-wrap;word-break:break-word;font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#111827;}"
        ".foot{margin-top:14px;color:#6b7280;font-size:12px;}"
        "</style></head><body>"
        "<div class=\"wrap\">"
        "<header class=\"hero\">"
        f"<h1 class=\"headline\">Choi Threads Digest (last {hours_back}h)</h1>"
        f"<p class=\"sub\">총 {len(entries)}개 게시물</p>"
        "</header>"
        "<section class=\"section\">"
        + "".join(rows)
        + "</section>"
        "<section class=\"section summary\">"
        "<strong>요약 및 적응 포인트</strong>"
        f"<pre>{summary_html}</pre>"
        "</section>"
        f"<div class=\"foot\">Generated at (UTC): {generated}</div>"
        "</div></body></html>"
    )


def send_gmail_plaintext(
    sender: str,
    to_email: str,
    subject: str,
    body: str,
    html_body: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> None:
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[GMAIL_SCOPE],
    )

    service = build("gmail", "v1", credentials=creds)

    message = MIMEMultipart("alternative")
    message["to"] = to_email
    message["from"] = sender
    message["subject"] = subject
    message.attach(MIMEText(body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def guardrails(task_name: str, rss_url: str, to_email: str) -> None:
    if task_name != TASK_NAME:
        raise RuntimeError(
            "OAuth token usage is blocked. TASK_NAME must be 'choi_threads_newsletter'."
        )
    if rss_url.strip() != EXPECTED_RSS:
        raise RuntimeError("OAuth token usage is blocked. RSS_URL does not match allowed feed.")
    if to_email.strip().lower() != EXPECTED_RECIPIENT:
        raise RuntimeError("OAuth token usage is blocked. TO_EMAIL is not the allowed recipient.")


def should_skip_before_start_date(start_date_local: str, local_timezone: str) -> bool:
    if not start_date_local:
        return False

    today_local = datetime.now(ZoneInfo(local_timezone)).date()
    try:
        start_date = date.fromisoformat(start_date_local)
    except ValueError as exc:
        raise RuntimeError("START_DATE_LOCAL must be YYYY-MM-DD format.") from exc
    return today_local < start_date


def main() -> None:
    load_dotenv()

    task_name = get_env("TASK_NAME", TASK_NAME)
    rss_url = get_env("RSS_URL", required=True)
    to_email = get_env("TO_EMAIL", required=True)
    from_email = get_env("FROM_EMAIL", required=True)

    guardrails(task_name=task_name, rss_url=rss_url, to_email=to_email)

    hours_back = int(get_env("HOURS_BACK", "24"))
    max_items = int(get_env("MAX_ITEMS", "20"))
    state_file = Path(get_env("STATE_FILE", ".newsletter_state.json"))
    start_date_local = get_env("START_DATE_LOCAL", "")
    local_timezone = get_env("LOCAL_TIMEZONE", "Asia/Seoul")

    if should_skip_before_start_date(start_date_local, local_timezone):
        print(
            f"Skip sending: local date in {local_timezone} is before START_DATE_LOCAL={start_date_local}"
        )
        return

    client_id = get_env("GOOGLE_CLIENT_ID", required=True)
    client_secret = get_env("GOOGLE_CLIENT_SECRET", required=True)
    refresh_token = get_env("GOOGLE_REFRESH_TOKEN", required=True)

    gemini_api_key = get_env("GEMINI_API_KEY", required=True)
    gemini_model = get_env("GEMINI_MODEL", "gemini-flash-latest")

    sent_links = load_sent_state(state_file)
    entries = fetch_recent_entries(
        rss_url=rss_url,
        hours_back=hours_back,
        max_items=max_items,
        already_sent=sent_links,
    )

    if not entries:
        print("No new entries in last 24h. No email sent.")
        return

    llm_summary = summarize_entries(entries, model=gemini_model, api_key=gemini_api_key)
    body = build_email_body(entries, llm_summary, hours_back)
    html_body = build_email_html(entries, llm_summary, hours_back)

    now_kr = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    subject = f"[Choi Threads] Last {hours_back}h Digest - {now_kr}"

    send_gmail_plaintext(
        sender=from_email,
        to_email=to_email,
        subject=subject,
        body=body,
        html_body=html_body,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )

    sent_links.update(e.link for e in entries)
    save_sent_state(state_file, sent_links)
    print(f"Sent digest with {len(entries)} items to {to_email}")


if __name__ == "__main__":
    main()
