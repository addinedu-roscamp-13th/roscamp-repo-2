// 길잡이 종료 → 패널 홈 복귀. ROS 수신 콜백이 부르는 완료 분기를 단위로 본다.
//
// 실행:
//   cmake -S . -B build -DLIBI_GUI_TESTS=ON && cmake --build build -j
//   ./build/test_guide_completion_ui
//
// public API에는 "테스트용으로 guide 중으로 만들기"가 없다. 실제 ROS 수신 콜백이
// 호출하는 완료 분기를 검증하기 위해 fixture에서만 내부 상태를 준비한다.
#include <QCoreApplication>
#include <cstdio>

#define private public
#include "../src/RobotController.h"
#undef private

static int g_fail = 0;
static void check(bool ok, const char *what) {
    std::printf("%s  %s\n", ok ? "  ok  " : "FAILED", what);
    if (!ok) { g_fail++; }
}

// 안내를 막 시작한 상태로 만든다(아직 WORKING 을 못 봄).
static void beginGuide(RobotController &c) {
    c.m_guidePhase = QStringLiteral("guiding");
    c.m_mode = QStringLiteral("guide");
    c.m_guideSawWorking = false;
    c.m_guideEndedAt.invalidate();
    c.m_guideSince.start();          // 시간 기반 폴백의 기준점 (startGuide 와 같다)
}

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);

    // ── ① 정상 종료: WORKING 을 본 뒤 벗어나면 끝난다 ──────────────────────
    {
        RobotController c;
        beginGuide(c);
        c.finishGuideIfLeftWorking(QStringLiteral("WORKING"));   // 안내가 실제로 시작
        check(c.guidePhase() == QLatin1String("guiding"),
              "WORKING 을 받는 동안에는 안내가 계속된다");
        c.finishGuideIfLeftWorking(QStringLiteral("PATROL"));    // 벗어남 = 종료
        // ⚠️ 여기서 `completed` 를 기대하면 안 된다. `finishGuideIfLeftWorking` 은
        //    `completed` 를 거쳐 **곧바로 `resetGuidePhase()` 로 `idle` 까지 되감는다**
        //    (`RobotController.cpp` 의 "화면을 나가기 전에 단계를 되감는다" 주석).
        //    안 되감으면 길잡이에 다시 들어왔을 때 목적지 화면이 통째로 숨는다.
        //    2026-08-02 에 그 되감기가 들어오면서 이 단언이 낡았다.
        check(c.guidePhase() == QLatin1String("idle"),
              "안내 종료는 단계를 처음(idle)으로 되감는다");
        check(c.mode() == QLatin1String("home"), "안내 종료는 홈 화면으로 돌아간다");
        check(c.m_guideEndedAt.isValid(), "종료 직후 잔여 터치 차단 타이머를 시작한다");
    }

    // ── ② 전이 직후 경쟁: WORKING 을 한 번도 못 봤으면 끝난 게 아니다 ──────
    //
    // `fsm_state` 는 5Hz 로 계속 온다. FMS 가 WORKING 전이를 마치고 granted 를
    // 돌려주는 사이에 **직전 상태(PATROL)를 실은 메시지가 이미 날아오고 있다.**
    // 그게 phase="guiding" 직후 도착하면, 가드가 없으면 안내를 시작하자마자
    // 종료시킨다 — 화면은 홈으로 튕기고 로봇만 안내를 계속한다.
    {
        RobotController c;
        beginGuide(c);
        c.finishGuideIfLeftWorking(QStringLiteral("PATROL"));    // 날아오던 옛 상태
        check(c.guidePhase() == QLatin1String("guiding"),
              "전이 직후 날아온 옛 상태로 안내를 끝내지 않는다");
        check(c.mode() == QLatin1String("guide"),
              "그때 화면도 홈으로 튕기지 않는다");
        // 곧 WORKING 이 오고, 그 뒤에 벗어나야 비로소 끝난다.
        c.finishGuideIfLeftWorking(QStringLiteral("WORKING"));
        c.finishGuideIfLeftWorking(QStringLiteral("IDLE"));
        check(c.guidePhase() == QLatin1String("idle"),
              "WORKING 을 본 뒤 벗어나면 그때 끝난다(단계는 idle 로 되감긴다)");
        check(c.mode() == QLatin1String("home"), "그때 화면도 홈으로 돌아간다");
    }

    // ── ③ 다음 안내가 옛 플래그를 물려받지 않는다 ─────────────────────────
    {
        RobotController c;
        beginGuide(c);
        c.finishGuideIfLeftWorking(QStringLiteral("WORKING"));
        c.finishGuideIfLeftWorking(QStringLiteral("PATROL"));     // 1회차 종료
        beginGuide(c);                                            // 2회차 시작
        c.finishGuideIfLeftWorking(QStringLiteral("PATROL"));      // 또 옛 상태
        check(c.guidePhase() == QLatin1String("guiding"),
              "두 번째 안내도 같은 가드를 받는다");
    }

    // ── ④ WORKING 을 놓쳐도 유예가 지나면 종료로 친다 ──────────────────────
    //
    // WORKING 메시지를 한 번이라도 놓치면(브릿지 재연결·DDS 유실·패널이 늦게 붙음)
    // `m_guideSawWorking` 이 영영 false 로 남는다. 폴백이 없으면 로봇이 안내를
    // 끝냈는데도 `resetGuidePhase()` 와 `setMode("home")` 이 **둘 다 안 불려**
    // 패널만 안내 화면에 갇힌다. 추종에는 있던 폴백이 길잡이에만 없었다.
    {
        RobotController c;
        beginGuide(c);
        c.m_guideGraceMs = 0;        // 유예를 0 으로 — 20초를 실제로 기다릴 수 없다
        // `QElapsedTimer::hasExpired` 는 **초과**(>)라 0ms 로는 같은 tick 에 안 지난다.
        while (c.m_guideSince.elapsed() <= 0) { }        // 1ms 안에 빠진다
        c.finishGuideIfLeftWorking(QStringLiteral("PATROL"));
        check(c.guidePhase() == QLatin1String("idle"),
              "시작 신호를 놓쳐도 20초 뒤엔 안내 단계를 처음으로 되감는다");
        check(c.mode() == QLatin1String("home"),
              "시작 신호를 놓쳐도 20초 뒤엔 홈 화면으로 돌아간다");
    }

    // ── ⑤ 유예 전에는 여전히 안 끝난다 (②의 회귀 방지) ─────────────────────
    {
        RobotController c;
        beginGuide(c);                                            // 방금 시작
        c.finishGuideIfLeftWorking(QStringLiteral("PATROL"));
        check(c.guidePhase() == QLatin1String("guiding"),
              "유예 안에서는 옛 상태로 안내를 끝내지 않는다");
        check(c.mode() == QLatin1String("guide"), "그때 화면도 그대로다");
    }

    std::printf(g_fail ? "\n실패 %d건\n" : "\n전부 통과\n", g_fail);
    return g_fail ? 1 : 0;
}
