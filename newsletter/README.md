# Choi Threads 24h Newsletter Workflow

`choi.openai` RSS를 읽어 최근 24시간 게시물만 추려 요약/적응 포인트를 만들고 Gmail OAuth로 메일 발송합니다.

## 1) 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) 환경 변수 준비

```bash
cp .env.example .env
```

`.env` 필수 값:

- `RSS_URL` (고정: `https://rss.app/feeds/PJfFHato1ox9YKyR.xml`)
- `TO_EMAIL` (고정: `plain9191@gmail.com`)
- `FROM_EMAIL` (발신 Gmail)
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `GEMINI_API_KEY`

## 3) Gmail OAuth 리프레시 토큰 발급(1회)

```bash
python oauth_setup.py
```

브라우저에서 Gmail 권한 승인 후 `oauth_token.json`이 생성됩니다.
그 안의 `refresh_token` 값을 `.env`의 `GOOGLE_REFRESH_TOKEN`에 넣으세요.

## 4) 실행

```bash
python newsletter_workflow.py
```

정상 동작 시 `plain9191@gmail.com`으로 메일이 발송되고,
이미 보낸 링크는 `.newsletter_state.json`에 저장되어 중복 발송을 막습니다.

## 5) GitHub Actions로 매일 오전 8시 자동 실행 (컴퓨터 꺼져도 동작)

`.github/workflows/daily-digest.yml`이 이미 포함되어 있습니다.
GitHub 저장소에 push한 뒤 아래 시크릿을 등록하세요.

저장소 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`

- `RSS_URL` = `https://rss.app/feeds/PJfFHato1ox9YKyR.xml`
- `TO_EMAIL` = `plain9191@gmail.com`
- `FROM_EMAIL` = 발신 Gmail 주소
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `GEMINI_API_KEY`
- `GEMINI_MODEL` = `gemini-3.0-flash`

스케줄은 매일 오전 8시(Asia/Seoul)이며, UTC 기준으로는 전날 23:00에 실행됩니다.

첫 발송일은 `2026-02-19`로 고정되어 있어, 그 이전 날짜 실행은 자동으로 스킵됩니다.
오늘 테스트가 필요하면 GitHub Actions 화면에서 `Run workflow`로 수동 실행하세요.

## 안전장치

스크립트는 아래 조건일 때만 Gmail OAuth 토큰을 사용합니다.

- `TASK_NAME=choi_threads_newsletter`
- `RSS_URL=https://rss.app/feeds/PJfFHato1ox9YKyR.xml`
- `TO_EMAIL=plain9191@gmail.com`

조건이 다르면 강제로 실패해 다른 용도로 토큰 사용을 막습니다.
