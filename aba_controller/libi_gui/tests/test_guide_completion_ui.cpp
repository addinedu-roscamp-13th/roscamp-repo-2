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
        check(c.guidePhase() == QLatin1String("completed"), "안내 종료는 completed가 된다");
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
        check(c.guidePhase() == QLatin1String("completed"),
              "WORKING 을 본 뒤 벗어나면 그때 끝난다");
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

    std::printf(g_fail ? "\n실패 %d건\n" : "\n전부 통과\n", g_fail);
    return g_fail ? 1 : 0;
}
