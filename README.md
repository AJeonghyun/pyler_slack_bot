# Labeling Vote Bot

Python 기반 Slack Bot으로, 스크린샷이 올라온 메시지에 `:vote:` reaction이 달리면 같은 thread에 Labeling Review 투표 카드를 생성합니다. 팀원은 0점부터 5점까지 버튼으로 투표하고, 같은 thread에서 자유롭게 의견을 남길 수 있습니다.

이번 MVP에는 AI 요약, Notion 연동, OCR, 이미지 분석, 유사 케이스 검색, 관리자 웹페이지, PostgreSQL, FastAPI를 포함하지 않습니다.

## 기능

- Slack Socket Mode 기반 실행
- `reaction_added` 이벤트 감지
- `:vote:` reaction 기준 투표 case 생성
- 같은 `channel_id + root_ts`에 대해 투표 카드 중복 생성 방지
- 0점부터 5점까지 Block Kit 버튼 투표
- 사용자별 1개 투표 저장 및 재투표 시 수정
- `chat.update`로 투표 결과 갱신
- `투표 마감` 버튼으로 투표 종료 및 최종 결과 thread 댓글 작성
- SQLite 자동 테이블 생성

## Slack App 설정

### Bot Token Scopes

Slack App의 OAuth & Permissions에서 Bot Token Scopes에 다음 scope를 추가합니다.

```text
chat:write
reactions:read
channels:history
groups:history
im:history
```

개인 DM에서 테스트하거나 사용하려면 `im:history`가 필요합니다. 그룹 DM까지 지원하려면
추가로 `mpim:history`를 넣습니다.

### Event Subscriptions

Event Subscriptions를 켜고 Bot Events에 다음 이벤트를 추가합니다.

```text
reaction_added
```

### Interactivity & Shortcuts

Interactivity & Shortcuts를 켜야 버튼 클릭 action을 받을 수 있습니다. Socket Mode를 사용하므로 공개 Request URL 없이 개발할 수 있습니다.

### Socket Mode

Socket Mode를 켜고 App-Level Token을 발급합니다. App-Level Token에는 다음 scope가 필요합니다.

```text
connections:write
```

## 환경변수

`.env.example`을 참고해 `.env` 파일을 만듭니다.

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
DB_PATH=./labeling_vote_bot.db
VOTE_TRIGGER_REACTION=vote
```

토큰 값은 코드에 넣지 말고 환경변수로만 설정합니다.

## 실행 방법

Python 3.10 이상을 사용합니다.

```bash
pip install -r requirements.txt
python app.py
```

## 테스트

Slack 토큰 없이 로컬 DB, Block Kit 생성, 핸들러 흐름을 검증할 수 있습니다.

```bash
python -m unittest tests.test_mvp
```

앱을 실행한 뒤 Slack 채널에 Bot을 초대합니다.

```text
/invite @Labeling Vote Bot
```

## 사용 방법

```text
1. 스크린샷 메시지를 Slack 채널에 업로드한다.
2. 해당 메시지에 :vote: 이모지를 단다.
3. Bot이 thread에 투표 카드를 생성한다.
4. 팀원들이 0~5점 버튼으로 투표한다.
5. 의견은 같은 thread 댓글로 토론한다.
6. 리뷰어가 투표 마감 버튼을 누른다.
```

## 데이터베이스

앱 시작 시 `DB_PATH` 위치에 SQLite 데이터베이스가 없으면 자동으로 생성합니다.

`cases` 테이블은 투표 case와 Slack 메시지 정보를 저장합니다.

`votes` 테이블은 `case_id + user_id` 기준으로 사용자의 현재 점수를 저장합니다.

## 프로젝트 구조

```text
labeling-vote-bot/
├── app.py
├── db.py
├── slack_blocks.py
├── requirements.txt
├── .env.example
└── README.md
```
