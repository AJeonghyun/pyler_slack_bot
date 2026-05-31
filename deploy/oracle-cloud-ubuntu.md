# Oracle Cloud Always Free 배포 가이드

이 봇은 Slack Socket Mode로 동작하므로 공개 HTTP endpoint, HTTPS 인증서, inbound 포트가 필요 없습니다.
Oracle Cloud VM에서 Slack으로 나가는 outbound 인터넷 연결만 가능하면 됩니다.

## 1. VM 생성

Oracle Cloud 콘솔에서 Always Free 대상 Compute VM을 생성합니다.

- Image: Ubuntu 24.04 또는 22.04
- Shape: Ampere A1 Flex 또는 Always Free eligible shape
- OCPU/RAM: 1 OCPU, 1-6 GB RAM이면 충분
- Network: 기본 VCN 사용 가능
- Inbound: SSH `22`만 본인 IP로 허용 권장

이 봇은 SQLite를 사용하므로 같은 DB 파일을 여러 프로세스가 동시에 쓰지 않게 VM 한 대에서 컨테이너 한 개만 실행합니다.

## 2. 서버 접속

```bash
ssh ubuntu@<PUBLIC_IP>
```

## 3. Docker 설치

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

그룹 권한을 반영하려면 한 번 로그아웃 후 다시 SSH 접속합니다.

## 4. 코드 배치

```bash
sudo mkdir -p /opt
sudo chown "$USER:$USER" /opt
cd /opt
git clone https://github.com/AJeonghyun/pyler_slack_bot.git
cd pyler_slack_bot
```

## 5. 환경변수 설정

```bash
cp .env.example .env
nano .env
```

Oracle VM에서는 Docker Compose가 `DB_PATH=/data/labeling_vote_bot.db`를 주입합니다.
`.env`에는 Slack token 값과 트리거 이모지만 넣으면 됩니다.

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
DB_PATH=/data/labeling_vote_bot.db
VOTE_TRIGGER_REACTION=ballot_box_with_ballot
```

`.env`는 Git에 커밋하지 않습니다.

## 6. 실행

```bash
docker compose up -d --build
docker compose logs -f
```

정상 실행 로그 예시:

```text
Bot user id: ...
Starting Labeling Vote Bot with trigger reaction :ballot_box_with_ballot:
Bolt app is running!
```

## 7. 서버 재부팅 후 자동 실행

Docker Compose의 `restart: unless-stopped`만으로도 Docker daemon이 뜨면 컨테이너가 다시 시작됩니다.
systemd로 Compose 프로젝트 자체를 서비스로 관리하려면 다음을 실행합니다.

```bash
sudo cp deploy/labeling-vote-bot.service /etc/systemd/system/labeling-vote-bot.service
sudo systemctl daemon-reload
sudo systemctl enable labeling-vote-bot
sudo systemctl start labeling-vote-bot
sudo systemctl status labeling-vote-bot
```

## 8. 운영 명령

```bash
# 상태 확인
docker compose ps

# 로그 확인
docker compose logs -f

# 코드 업데이트 후 재배포
git pull
docker compose up -d --build

# 중지
docker compose down
```

## 9. 데이터 보존

SQLite DB는 Docker named volume `labeling-vote-data`의 `/data/labeling_vote_bot.db`에 저장됩니다.

간단 백업:

```bash
mkdir -p ~/labeling-vote-bot-backups
sudo docker cp labeling-vote-bot:/data/labeling_vote_bot.db ~/labeling-vote-bot-backups/labeling_vote_bot_$(date +%Y%m%d_%H%M%S).db
```
