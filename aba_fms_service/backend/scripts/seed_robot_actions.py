"""표정/사운드/정지 기본 학습 액션 시드.

챗의 표정·사운드·정지 명령을 하드코딩이 아니라 '등록된 액션' 기반으로 동작시키기 위해
기본 액션들을 rc_robot_learned_actions 에 넣는다. 같은 이름이 있으면 건너뛴다(멱등).

실행: cd backend && .venv/bin/python -m scripts.seed_robot_actions
"""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.database import AdminSessionLocal
from app.models import RobotLearnedAction

SEED: list[dict] = [
    # 표정 (lcd/emotion)
    {"name": "표정 - 기쁨", "action_type": "emotion", "params": {"emotion": "happy"},
     "triggers": ["웃어줘", "웃어", "기뻐", "행복한 표정", "스마일", "미소"]},
    {"name": "표정 - 화남", "action_type": "emotion", "params": {"emotion": "angry"},
     "triggers": ["화내줘", "화난 표정", "화나", "짜증난 표정"]},
    {"name": "표정 - 슬픔", "action_type": "emotion", "params": {"emotion": "sad"},
     "triggers": ["슬픈 표정", "슬퍼", "우울한 표정", "울상"]},
    {"name": "표정 - 인사", "action_type": "emotion", "params": {"emotion": "hello"},
     "triggers": ["인사 표정", "안녕 표정", "반가운 표정", "인사해"]},
    {"name": "표정 - 신남", "action_type": "emotion", "params": {"emotion": "fun"},
     "triggers": ["신난 표정", "신나", "신남", "즐거운 표정", "재밌는 표정", "들뜬 표정"]},
    {"name": "표정 - 호기심", "action_type": "emotion", "params": {"emotion": "interest"},
     "triggers": ["호기심 표정", "궁금한 표정", "관심 표정", "흥미로운 표정", "궁금해"]},
    {"name": "표정 - 심심", "action_type": "emotion", "params": {"emotion": "bored"},
     "triggers": ["심심한 표정", "지루한 표정", "따분한 표정", "심심해", "지루해"]},
    {"name": "표정 - 기본", "action_type": "emotion", "params": {"emotion": "basic"},
     "triggers": ["기본 표정", "평범한 표정", "무표정", "기본 얼굴", "표정 초기화"]},
    # 사운드 (buzzer)
    {"name": "사운드 - 벨", "action_type": "buzzer", "params": {"preset": "bell"},
     "triggers": ["벨 소리", "벨소리 내줘", "종소리", "벨 울려"]},
    {"name": "사운드 - 삐", "action_type": "buzzer", "params": {"preset": "beep"},
     "triggers": ["삐 소리", "삐 소리내", "비프음", "비프"]},
    {"name": "사운드 - 알람", "action_type": "buzzer", "params": {"preset": "alarm"},
     "triggers": ["알람 울려", "경고음", "알람 소리"]},
    {"name": "사운드 - 성공음", "action_type": "buzzer", "params": {"preset": "success"},
     "triggers": ["성공음", "성공 소리", "완료음", "완료 소리"]},
    {"name": "사운드 - 오류음", "action_type": "buzzer", "params": {"preset": "error"},
     "triggers": ["오류음", "에러음", "실패음", "실패 소리"]},
    # 멜로디 연주 (buzzer/melody — 짧은 비프와 다른 엔드포인트)
    {"name": "멜로디 - 엘리제를 위하여", "action_type": "buzzer_melody", "params": {"melody": "fur_elise"},
     "triggers": ["엘리제를 위하여", "엘리제", "엘리제 연주", "엘리제를 위하여 연주해", "엘리제 연주해줘"]},
    {"name": "멜로디 - 학교종", "action_type": "buzzer_melody", "params": {"melody": "school_bell"},
     "triggers": ["학교종", "학교종 연주", "학교 종소리", "학교종 쳐줘", "학교종 연주해"]},
    # 정지 (모터 정지)
    {"name": "정지 - 모터 멈춤", "action_type": "stop", "params": {},
     "triggers": ["정지", "멈춰", "그만", "스톱", "멈춤"]},
]


async def main() -> None:
    async with AdminSessionLocal() as db:
        existing = set((await db.execute(select(RobotLearnedAction.name))).scalars().all())
        added = 0
        for s in SEED:
            if s["name"] in existing:
                print(f"skip (이미 있음): {s['name']}")
                continue
            db.add(RobotLearnedAction(
                name=s["name"],
                description=None,
                trigger_phrases=json.dumps(s["triggers"], ensure_ascii=False),
                robot_scope="explicit",
                action_type=s["action_type"],
                params=json.dumps(s["params"], ensure_ascii=False),
                enabled=True,
            ))
            added += 1
            print(f"추가: {s['name']} ({s['action_type']})")
        await db.commit()
        print(f"완료: {added}개 추가")


if __name__ == "__main__":
    asyncio.run(main())
