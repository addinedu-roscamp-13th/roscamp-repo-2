# Team Arte

<div align="center">

<img src="https://images.prismic.io/asd0821/ojvQkTRwaGMIdnXo_Arte_logo2.png?auto=format,compress" alt="Arte Logo" width="800"/>

###  프로젝트 명 : ABA (Arte Book Asistance)

**ROS2 · AI 자율주행 로봇개발자 부트캠프 2팀 — 도서관 도서 배달·수거 모바일 매니퓰레이터 `LIBI`**

**[ Presentation ](#)** | **[ Demo ](#)**

</div>

---

## 🚀 프로젝트 개요

> **LIBI** 는 도서관에서 회원·사서의 요청을 받아 **책을 배달하고 수거하는 모바일 매니퓰레이터**입니다.
> 주행 AMR 위에 로봇팔을 얹은 형태로, ROS2 브리지(`libi_service`)가 웹 요청을 받아 로봇의 이동과 팔 작업을 지휘합니다.

- **모바일 매니퓰레이터** = 주행 AMR(`libi_drive`) + 로봇팔(`libi_handy`)
- **제어** = `libi_service`(ROS2 브리지)가 주행(nav2)·팔 작업을 지휘
- **AI** = 비전 인식 + 도서관 안내 챗봇(`LiBi AI`) 서브시스템

### 🛠 기술 스택 (Tech Stack)

| 구분 | 기술 |
|------|------|
| **Language** | ![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white) ![C++](https://img.shields.io/badge/C++-00599C?style=for-the-badge&logo=cplusplus&logoColor=white) |
| **Framework** | ![ROS2](https://img.shields.io/badge/ROS2_Jazzy-22314E?style=for-the-badge&logo=ros&logoColor=white) ![Nav2](https://img.shields.io/badge/Nav2-3C8DBC?style=for-the-badge&logo=ros&logoColor=white) |
| **Robot** | ![Robot Arm](https://img.shields.io/badge/LIBI_Handy_(로봇팔)-FF6F00?style=for-the-badge&logo=robotframework&logoColor=white) ![AMR](https://img.shields.io/badge/LIBI_Drive_(주행_AMR)-009688?style=for-the-badge&logo=robotframework&logoColor=white) |
| **Simulation** | ![Gazebo](https://img.shields.io/badge/Gazebo-FF6600?style=for-the-badge&logo=gazebo&logoColor=white) ![RViz](https://img.shields.io/badge/RViz-22314E?style=for-the-badge&logo=ros&logoColor=white) |
| **Server / AI** | ![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white) ![Ollama](https://img.shields.io/badge/Ollama_(qwen3)-000000?style=for-the-badge&logo=ollama&logoColor=white) ![Ultralytics](https://img.shields.io/badge/YOLO_/_Ultralytics-111F68?style=for-the-badge&logo=pytorch&logoColor=white) |
| **Database** | ![MariaDB](https://img.shields.io/badge/MariaDB-003545?style=for-the-badge&logo=mariadb&logoColor=white) |
| **GUI / Web** | ![Qt5/QML](https://img.shields.io/badge/Qt5_/_QML_(C++)-41CD52?style=for-the-badge&logo=qt&logoColor=white) ![React](https://img.shields.io/badge/React-61DAFB?style=for-the-badge&logo=react&logoColor=black) |
| **Communication** | ![HTTP](https://img.shields.io/badge/HTTP-005571?style=for-the-badge&logo=internetcomputer&logoColor=white) ![ROS2 DDS](https://img.shields.io/badge/ROS2_DDS_(CycloneDDS)-22314E?style=for-the-badge&logo=ros&logoColor=white) ![TCP](https://img.shields.io/badge/TCP-0B7285?style=for-the-badge&logoColor=white) ![WebSocket](https://img.shields.io/badge/WebSocket_(WS)-010101?style=for-the-badge&logo=socketdotio&logoColor=white) |
| **Tools** | ![Git](https://img.shields.io/badge/Git-F05032?style=for-the-badge&logo=git&logoColor=white) |

### 📖 프로젝트 시나리오 (Scenario)

#### 1️⃣ 도서 배달 요청 - 고객

<div align="center">

| 📱 도서 요청 | ➡️ | 🗺 경로 생성 | ➡️ | 🦾 상차 | ➡️ | 🤖 배달 | ➡️ | 🙆 배달 완료 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|

<!-- 시나리오 이미지로 교체 가능: <img src="docs/img/scenario_delivery.png" width="700"/> -->

</div>

#### 2️⃣ 도서 수거 요청 - 직원

<div align="center">

| 📱 수거 요청 | ➡️ | 🦾 상차 | ➡️ | 🤖 배달 | ➡️ | 🦾 하차 | ➡️ | 📚 서가 정리 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|

<!-- 시나리오 이미지로 교체 가능: <img src="docs/img/scenario_pickup.png" width="700"/> -->

</div>

### 🗺 Map 구성

> _작업 환경(Map) 이미지와 설명은 추가 예정입니다._

<!-- <img src="docs/img/map.png" alt="Map" width="600"/> -->

- 구역 설명: _(예: 서가 구역 A/B/C, 대출 데스크, 충전 스테이션)_
- 주요 좌표/스테이션: _(예: pickup, dropoff, charger)_

---

## 📂 Folder Structure

```
ABA/                                       # monorepo 루트 (pingdergarten 컨벤션)
│
├── aba_controller/                        # [Equipment] 로봇 온보드 컨트롤러
│   ├── libi_drive_controller/             #   주행 보드 — nav2 주행 스택
│   ├── libi_handy_controller/             #   팔 보드 — 매니퓰레이션(상/하차)
│   └── libi_gui/                          #   터치패널 UI (Qt5/QML·C++) — 이용자 안내·검색
│
├── aba_server/                            # [Server] 웹 백엔드 + 웹 프론트 (함께 서빙, 클라이언트 정문)
├── aba_fms_service/                       # [Server] Fleet 관제 (Fleet Management Service)
├── aba_ai_service/                        # [Server] 비전 AI + LiBi AI 챗봇
│
├── tests/                                 # 테스트
│
├── README.md
└── .env.example
```

---

## 🏗 System Architecture

<!-- <img src="docs/img/system_architecture.png" alt="System Architecture" width="700"/> -->

### 3계층 구성

```
[Client]    회원 / 사서 브라우저, 터치패널 UI        ──HTTP──┐
[Server]    aba_server(백엔드+프론트) + ABA DB / aba_fms_service / aba_ai_service
              │ ROS2(DDS)
[Equipment] Libi Drive Board (주행)  ──DDS──  Libi Handy Board (팔)
```

- **통신**: HTTP(클라↔서버), ROS2/DDS(서비스↔컨트롤러·drive↔handy), TCP(서버간)
- **로봇 지휘**: `libi_service` 가 로봇 컨트롤러의 **"이동 지휘자"** — 서버/코디네이터에서 실행, ROS2 로 `libi_drive`(주행)·`libi_handy`(팔) 에 명령

---

## 🚦 로봇 제어 / 태스크 처리

> **`libi_service`**(ROS2 브리지)가 웹 요청을 받아 로봇의 이동·작업을 지휘합니다. (단일 로봇 기준)

- **로봇 상태 모니터링** — 로봇의 위치/배터리/태스크 상태 수집
- **작업 처리(Task)** — 배달/수거 요청을 순차 처리해 로봇에 명령
- **주행 명령** — `libi_service` 가 nav2 로 목적지 이동 명령
- **팔 작업 트리거** — 태스크 도착 후 `libi_service` 가 `libi_handy`(상/하차) 트리거
- **역할 분리** — 명령·조정은 `libi_service`, 로컬 행동(추종·도킹)은 컨트롤러가 담당

<!-- <img src="docs/img/robot_control.png" alt="Robot Control" width="700"/> -->

---

## 🔄 Sequence Diagram

> _주요 시나리오(배달/수거) 시퀀스 다이어그램은 추가 예정입니다._

<!-- <img src="docs/img/sequence_diagram.png" alt="Sequence Diagram" width="700"/> -->

---

## 🗄 ERD

> `LiBi AI` 는 **books + robot_logs** 두 테이블을 같은 DB(MariaDB)에 둡니다. 전체 ABA DB 스키마는 `db/` 참고.

<!-- <img src="docs/img/erd.png" alt="ERD" width="700"/> -->

**`books`** — 도서 정보 + 위치 + 재고 (LIKE 검색 및 LLM 컨텍스트 주입 대상)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | INT PK | 도서 ID |
| `title` / `author` | VARCHAR | 제목 / 저자 |
| `summary` | TEXT | 요약 |
| `category` | VARCHAR | 분류 |
| `location` | VARCHAR | 서가 위치 (예: A-03-02) |
| `stock` | INT | 재고 (보유 우선 정렬) |
| `lang` | VARCHAR | 언어 |

**`robot_logs`** — 로봇 작업 로그(`/rosout` 저장, `rcl_interfaces/msg/Log` 매핑)

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | BIGINT PK | 로그 ID |
| `ts` | DATETIME(3) | 타임스탬프(ms) |
| `level` | VARCHAR | DEBUG/INFO/WARN/ERROR/FATAL |
| `node` / `msg` | VARCHAR / TEXT | rosout 노드명 / 메시지 |
| `task_id` / `robot_id` | VARCHAR | 작업·로봇 추적(선택) |

---

## 🔌 Interface Specification

> 노드/모듈 간 통신 인터페이스(Topic, Service, Action, API)를 정리합니다. _(예시 — 실제 인터페이스로 교체 예정)_

| Name | Type | Direction | Description |
|------|------|-----------|-------------|
| `/robot/goal` | Topic | GUI → Robot | 목적지 전달 |
| `/robot/status` | Topic | Robot → libi_service | 로봇 상태 보고 |
| `perform_action` | Action | libi_service → libi_handy | 상/하차 팔 작업 트리거 |
| `/api/books` | REST | Web → aba_server | 도서 검색/요청 |
| ... | ... | ... | ... |

---

## 🖥 GUI

> _GUI 화면 캡처와 주요 기능 설명은 추가 예정입니다._

<!-- <img src="docs/img/gui.png" alt="GUI" width="700"/> -->

- 로봇 온보드 터치패널 UI(`libi_gui`, Qt5/QML · C++) — 이용자 안내·도서 검색/추천 (관리자 모드 포함)
- 회원/사서 웹 — 도서 검색·요청·수거 (`aba_server` 가 백엔드와 프론트를 함께 서빙)

---

## 🤖 LiBi AI — AI 챗봇 서브시스템

> 도서관 AI 가이드 챗봇. **경량 RAG**(정규식 의도 판별 + MariaDB LIKE 검색 + 로컬 LLM 프롬프트 주입)로,
> 임베딩/벡터DB/Elasticsearch 없이 구현합니다. ROS 로봇과 분리된 `aba_ai_service` 서브시스템.

**처리 흐름**

```
자연어/음성 입력 → 의도 판별(정규식) → 후보 도서 검색(MariaDB LIKE)
→ 컨텍스트 생성 → 로컬 LLM(Ollama) 주입 → 스트리밍 응답 → 도서 카드/서가 위치
```

**Docker 구성 (4 서비스, DB 1개)**

| 서비스 | 역할 | 비고 |
|---|---|---|
| `mariadb` | 데이터 (books + robot_logs) | DB 1개에 테이블만 분리 |
| `ollama` | 로컬 LLM (qwen3:1.7b) | 모델 볼륨 보존, 유동 교체 |
| `backend` | FastAPI (도서/관리 API + STT WS, 8010) | nginx 만 접근 |
| `nginx` | React SPA 서빙 + `/api`·`/ollama` 프록시 | 개발 중엔 생략 가능 |

---

## ⚙️ 구현 (Implementation)

### 로봇팔 (Robot Arm) — `libi_handy`

- 매니퓰레이션(상/하차) — 팔 관절 제어로 책 상·하차
- 태스크 도착 후 `libi_service` 가 **`perform_action`** 으로 트리거

### 주행로봇 (Mobile Robot) — `libi_drive`

- **nav2** 표준 스택 주행, `libi_service` 가 목적지 명령
- URDF 변경 불필요 — 기존 로봇 URDF 재사용

---

## 🎬 Demo

### 로봇팔 (Robot Arm)

- 데모 영상: [링크](#)

### 주행로봇 (Mobile Robot)

- 데모 영상: [링크](#)

---

## 📅 Project Schedule

| Sprint | 기간 | 주요 내용 |
|------|------|-----------|
| Sprint 1 | 06/15 ~ 06/19 | 기획 및 설계(SR, SA Map Design) |
| Sprint 2 | 06/22 ~ 06/26 | 프로젝트 상세설계 (Scenario, Interface Specification, ERD), 기술조사 |
| Sprint 3 | 06/29 ~ 07/03 | 프로젝트 상세설계 (Scenario, Interface Specification, ERD), 기술조사 |
| Sprint 4 | 06/29 ~ 07/03 | 기술조사, Floder Structure - release v0.1 |
| Sprint 5 | 06/29 ~ 07/03 | 개발(전체 기능의 20%), 컴포넌트 전체 연동 테스트 - release v0.2 |
| Sprint 6 | 06/29 ~ 07/03 | 개발(전체 기능의 50%) - release v0.3 |
| Sprint 7 | 06/29 ~ 07/03 | 개발(전체 기능의 80%) - release v0.4 |
| Sprint 8 | 06/29 ~ 07/03 | 개발(전체 기능의 100%) - release v0.5 |
| Sprint 9 | 06/29 ~ 07/03 | 프로젝트 안정화, 발표자료 준비, 프로젝트 코드 리뷰 : v1.0 |


<!-- <img src="docs/img/schedule.png" alt="Schedule" width="700"/> -->

---

## 👥 팀원 소개

| 이름 | 역할 | 담당 업무 | GitHub |
|------|------|-----------|--------|
| 이강택 | 팀장 | ... | [@id](https://github.com/) |
| 인경일 | 팀원 | ... | [@id](https://github.com/) |
| 정호재 | 팀원 | ... | [@id](https://github.com/) |
| 이형주 | 팀원 | ... | [@id](https://github.com/) |
| 이세형 | 팀원 | ... | [@id](https://github.com/) |
