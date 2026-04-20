# MyNet

AWS 인프라 기반 디지털 미디어 공유 플랫폼
---
<img width="1547" height="856" alt="스크린샷 2026-04-20 102750" src="https://github.com/user-attachments/assets/673e8d2d-3eaa-4245-b0b7-b42e2287eb39" />

## 아키텍처

```
사용자
  │
  ▼
ALB (Application Load Balancer)
  │
  ▼
EC2 (Private Subnet)
  ├── Nginx  ──→  정적 파일 직접 서빙
  └── Gunicorn ──→ Flask App
          │
          ├── RDS MySQL (Private Subnet, SSL)
          ├── S3  (미디어 저장 / Presigned URL)
          └── CloudFront CDN (미디어 배포)
```

| 서비스 | 용도 |
|---|---|
| EC2 | Flask 앱 실행 (Private Subnet) |
| ALB | 외부 트래픽 수신 및 EC2 프록시 |
| S3 | 이미지·동영상 원본 저장 |
| CloudFront | S3 콘텐츠 CDN 배포 |
| RDS MySQL | 사용자·미디어 메타데이터 저장 |

---

## 주요 기능

- 회원가입 / 로그인 / 로그아웃
- 이미지·동영상 업로드 (10 MB 이하 단순 업로드 / 초과 시 S3 Multipart 업로드)
- 미디어 목록 조회 및 검색
- 미디어 상세 페이지 (조회수, 관련 게시물)
- 관리자 대시보드
  - EC2 CPU / 메모리 / 디스크 사용률 실시간 조회
  - 업로드 기록 (업로더 · 일시 · 형식 · 크기)
  - S3 오브젝트 목록
  - RDS 미디어 메타데이터 테이블
  - 사용자 관리 (권한 변경 · 삭제)

---

## 기술 스택

| 분류 | 기술 |
|---|---|
| Backend | Python 3, Flask 3.1, SQLAlchemy |
| DB | MySQL 8.0 (RDS), PyMySQL |
| Storage | AWS S3, CloudFront |
| Server | Gunicorn, Nginx |
| AWS SDK | boto3 |
| 모니터링 | psutil |

---

## 프로젝트 구조

```
app/
├── app.py              # Flask 라우트 및 API
├── models.py           # SQLAlchemy 모델 (User, Media, MediaFile)
├── config.py           # AWS·DB·Flask 설정
├── requirements.txt    # Python 의존성
├── nginx.conf          # Nginx 설정
├── templates/          # Jinja2 HTML 템플릿
│   ├── base.html
│   ├── index.html
│   ├── upload.html
│   ├── media_detail.html
│   ├── login.html
│   ├── register.html
│   ├── admin.html
│   └── admin_users.html
├── static/
│   ├── css/style.css
│   └── js/upload.js
├── packages/           # Windows용 오프라인 패키지
└── packages_linux/     # Linux용 오프라인 패키지
```

---

## 배포 방법

### 1. 파일 전송 (Bastion 경유 SCP)

```bash
scp -i key.pem -o ProxyJump=ubuntu@<bastion-ip> \
  app.py config.py models.py requirements.txt nginx.conf \
  ubuntu@<ec2-private-ip>:/home/ubuntu/app/

scp -i key.pem -o ProxyJump=ubuntu@<bastion-ip> \
  templates/* \
  ubuntu@<ec2-private-ip>:/home/ubuntu/app/templates/

scp -i key.pem -o ProxyJump=ubuntu@<bastion-ip> \
  static/css/style.css static/js/upload.js \
  ubuntu@<ec2-private-ip>:/home/ubuntu/app/static/
```

### 2. EC2에서 의존성 설치

```bash
cd /home/ubuntu/app

# pip가 없는 경우
python3 get-pip.py

# 오프라인 환경 (인터넷 미연결 EC2)
pip3 install --no-index --find-links=packages_linux -r requirements.txt

# 인터넷 연결 가능한 경우
pip3 install -r requirements.txt
```

### 3. RDS SSL 인증서 확인

```bash
ls /home/ubuntu/app/global-bundle.pem
# 없으면 다운로드
wget https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
  -O /home/ubuntu/app/global-bundle.pem
```

### 4. Nginx 설정 적용

```bash
sudo cp nginx.conf /etc/nginx/sites-available/mediahub
sudo ln -sf /etc/nginx/sites-available/mediahub /etc/nginx/sites-enabled/
# nginx.conf의 static 경로를 실제 경로로 수정
sudo sed -i 's|/home/ec2-user/app|/home/ubuntu/app|g' /etc/nginx/sites-available/mediahub
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Gunicorn 실행

```bash
cd /home/ubuntu/app
gunicorn -w 4 -b 127.0.0.1:5000 app:app --daemon \
  --access-logfile /home/ubuntu/app/access.log \
  --error-logfile  /home/ubuntu/app/error.log
```

#### systemd 서비스로 등록 (권장)

```bash
sudo tee /etc/systemd/system/gunicorn.service > /dev/null <<EOF
[Unit]
Description=MediaHub Gunicorn
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/app
ExecStart=/usr/local/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now gunicorn
```

---

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/` | 미디어 목록 |
| GET | `/post/<id>` | 미디어 상세 |
| GET/POST | `/login` | 로그인 |
| GET/POST | `/register` | 회원가입 |
| GET | `/upload` | 업로드 페이지 |
| POST | `/api/presign` | S3 단순 업로드 Presigned URL 발급 |
| POST | `/api/multipart/init` | Multipart 업로드 시작 |
| POST | `/api/multipart/presign-part` | 파트별 Presigned URL 발급 |
| POST | `/api/multipart/complete` | Multipart 업로드 완료 |
| POST | `/api/multipart/abort` | Multipart 업로드 취소 |
| POST | `/api/media` | 미디어 메타데이터 저장 |
| DELETE | `/api/media/<id>` | 미디어 삭제 |
| GET | `/api/media` | 미디어 목록 (JSON) |
| GET | `/admin` | 관리자 대시보드 |
| GET | `/admin/users` | 사용자 관리 |
