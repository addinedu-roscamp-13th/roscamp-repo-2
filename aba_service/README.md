 Chatbot System Setup# Chatbot System Setup

작성일: 2026-06-12 KST

이 문서는 `/home/robotPrj_Boilerplate/lib_service` 기준으로 구축한 챗봇 프론트엔드, NGINX 정적 배포, ngrok 외부 접속 정보를 기록합니다.

## 디렉터리 구조

```text
/home/robotPrj_Boilerplate/lib_service
├── backend/
└── frontend/
    ├── dist/
    ├── index.html
    ├── package.json
    ├── src/
    └── vite.config.ts
```

## 프론트엔드

프로젝트 위치:

```text
/home/robotPrj_Boilerplate/lib_service/frontend
```

프레임워크/도구:

```text
React 19
Vite 8
TanStack Router
TanStack Query
Tailwind CSS 4
TypeScript
```

Node.js/npm 버전:

```text
Node.js v24.16.0
npm 11.16.0
```

## 정적 빌드 방식

요청 사항에 따라 SSR 서버 방식이 아니라, NGINX가 `dist` 폴더의 정적 빌드 파일을 직접 읽어서 처리하는 방식으로 구성했습니다.

수정/추가한 주요 파일:

```text
/home/robotPrj_Boilerplate/lib_service/frontend/vite.config.ts
/home/robotPrj_Boilerplate/lib_service/frontend/index.html
/home/robotPrj_Boilerplate/lib_service/frontend/src/main.tsx
```

빌드 명령:

```bash
cd /home/robotPrj_Boilerplate/lib_service/frontend
npm run build
```

빌드 산출물:

```text
/home/robotPrj_Boilerplate/lib_service/frontend/dist/index.html
/home/robotPrj_Boilerplate/lib_service/frontend/dist/assets/
```

빌드 확인 결과:

```text
dist/index.html 생성 확인
dist/assets/index-*.js 생성 확인
dist/assets/styles-*.css 생성 확인
```

## NGINX 배포

NGINX 3000번 포트에서 프론트엔드 정적 빌드 파일을 서빙하도록 설정했습니다.

NGINX 설정 파일:

```text
/etc/nginx/conf.d/chatbot-frontend.conf
```

설정 내용:

```nginx
server {
    listen 3000;
    server_name _;

    root /home/robotPrj_Boilerplate/lib_service/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /assets/ {
        try_files $uri =404;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

NGINX 설정 검사:

```bash
sudo nginx -t
```

NGINX 재시작:

```bash
sudo systemctl restart nginx
```

로컬 접속 주소:

```text
http://localhost:3000/
```

같은 네트워크 접속 주소:

```text
http://192.168.0.12:3000/
```

확인 결과:

```text
HTTP/1.1 200 OK
Server: nginx/1.31.1
Content-Type: text/html
```

## ngrok 외부 접속

ngrok을 사용해 외부에서 접속 가능하도록 구성했습니다.

외부 접속 주소:

```text
https://sanora-wretched-lenard.ngrok-free.dev
```

현재 실행한 ngrok 명령:

```bash
/tmp/ngrok http 3000 --url https://sanora-wretched-lenard.ngrok-free.dev --log=stdout
```

ngrok 연결 구조:

```text
https://sanora-wretched-lenard.ngrok-free.dev
    -> localhost:3000
    -> NGINX
    -> /home/robotPrj_Boilerplate/lib_service/frontend/dist
```

외부 접속 확인 결과:

```text
HTTP/2 200
server: nginx/1.31.1
content-type: text/html
```

주의:

```text
ngrok 프로세스가 종료되면 외부 접속도 끊깁니다.
서버 재부팅 후에는 ngrok 명령을 다시 실행해야 합니다.
```

## 현재 실행 중인 관련 서비스

```text
NGINX: 0.0.0.0:3000
NGINX: 0.0.0.0:80
ngrok: https://sanora-wretched-lenard.ngrok-free.dev -> localhost:3000
```

서비스 확인 명령:

```bash
ss -ltnp | rg ':3000|:80'
ps -eo pid,ppid,cmd | rg 'ngrok http|PID'
```

## 다시 배포하는 방법

프론트엔드 코드를 수정한 뒤 아래 순서로 진행합니다.

```bash
cd /home/robotPrj_Boilerplate/lib_service/frontend
npm run build
sudo systemctl reload nginx
```

ngrok이 꺼져 있으면 다시 실행합니다.

```bash
/tmp/ngrok http 3000 --url https://sanora-wretched-lenard.ngrok-free.dev --log=stdout
```

## 관련 기존 서버 정보

phpMyAdmin:

```text
http://localhost/phpmyadmin/
http://192.168.0.12/phpmyadmin/
```

MariaDB/phpMyAdmin 관리자 계정:

```text
사용자: admin
비밀번호: <REDACTED — 보안상 공개 저장소에 올리지 않음 / not committed for security>
```

주의:

```text
위 DB 비밀번호는 관리자 권한 계정입니다. README를 공개 저장소에 올리지 마세요.
```
