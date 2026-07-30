// RosLink 의 패널 요청 통로 — id 상관과 타임아웃만 본다.
//
// 여기서 지키려는 건 하나다: **답이 없어도 콜백은 반드시 한 번 불린다.**
// HTTP 는 연결 실패가 곧바로 에러였지만 토픽은 아무도 안 듣고 있어도 발행이 성공한다.
// 그래서 타임아웃이 빠지면 길잡이·추종 승인(fail-closed)이 "요청 중" 화면에 영영 갇힌다 —
// 화면만 보면 멈춘 이유를 알 수 없는 종류의 고장이다.
//
// 응답 있는 경로는 tests/panel_responder.py 를 같이 띄워야 돈다(브릿지가 로봇 쪽에
// 보여주는 모습 그대로: /panel_request 구독 → /panel_result 발행).
//   인자 없이 실행하면 타임아웃만, `--with-responder` 를 주면 응답 경로까지 본다.
#include "../src/RosLink.h"

#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QJsonObject>
#include <QTimer>
#include <cstdio>

static int failures = 0;

static void check(bool cond, const char *what) {
    std::printf("%s  %s\n", cond ? "  ok  " : "FAILED", what);
    if (!cond) ++failures;
}

/** 이벤트 루프를 ms 만큼 돌린다 — RosLink 의 QTimer 와 큐잉 시그널이 여기서 처리된다. */
static void settle(int ms) {
    QEventLoop loop;
    QTimer::singleShot(ms, &loop, &QEventLoop::quit);
    loop.exec();
}

int main(int argc, char *argv[]) {
    QCoreApplication app(argc, argv);
    const bool withResponder = argc > 1 && QString::fromLocal8Bit(argv[1]) == "--with-responder";

    RosLink ros;
    settle(1500);   // DDS 디스커버리

    // ── 1. 아무도 안 듣고 있으면 타임아웃으로 실패가 온다 ────────────────────
    if (!withResponder) {
        check(!ros.panelLinkUp(), "응답자가 없으면 panelLinkUp() 이 거짓 (호출측은 HTTP 로 폴백)");

        bool called = false;
        bool okSeen = true;
        QElapsedTimer t; t.start();
        ros.panelRequest(QStringLiteral("guide.request"),
                         QJsonObject{{"robot_id", "pinky1"}, {"waypoint", "없는정점"}},
                         [&](bool ok, QJsonObject) { called = true; okSeen = ok; },
                         /*timeoutMs=*/800);

        check(!called, "발행 직후에는 아직 콜백이 안 불린다");
        settle(1500);
        check(called, "응답이 없어도 콜백이 불린다 (타임아웃)");
        check(!okSeen, "타임아웃은 ok=false 로 온다 — fail-closed 분기가 그대로 돈다");
        check(t.elapsed() >= 800, "타임아웃 전에 미리 실패시키지 않는다");

        // 늦게 온 응답이 죽은 콜백을 다시 부르지 않는지는 pending 에서 take 로 지운 것으로
        // 보장된다. 여기서 재현하려면 응답자가 지연 발행을 해야 해 --with-responder 쪽에 둔다.
        std::printf("\n응답 경로는 responder 와 같이 실행: "
                    "python3 tests/panel_responder.py & ./test_panel_link --with-responder\n");
    } else {
        // ── 2. 응답자가 있으면 id 로 매칭돼 돌아온다 ─────────────────────────
        check(ros.panelLinkUp(), "응답자가 붙으면 panelLinkUp() 이 참 (ROS2 경로 선택)");

        int calls = 0;
        QJsonObject body;
        bool okSeen = false;
        ros.panelRequest(QStringLiteral("panel.transition"),
                         QJsonObject{{"robot_id", "pinky1"}, {"target_state", "IDLE"}},
                         [&](bool ok, QJsonObject b) { ++calls; okSeen = ok; body = b; },
                         /*timeoutMs=*/3000);
        settle(2000);
        check(calls == 1, "응답이 오면 콜백이 정확히 한 번 불린다");
        check(okSeen, "정상 응답은 ok=true");
        check(body.value("echo").toString() == QLatin1String("panel.transition"),
              "요청한 op 에 대한 응답이 돌아온다 (id 상관)");

        // 타임아웃 뒤 늦게 오는 응답이 이미 실패 처리한 콜백을 다시 부르면, 화면이
        // "실패" 를 띄운 뒤 뒤늦게 "성공" 으로 뒤집힌다. 그게 안 되는지 본다.
        int lateCalls = 0;
        ros.panelRequest(QStringLiteral("slow.op"), QJsonObject{{"delay_ms", 1500}},
                         [&](bool, QJsonObject) { ++lateCalls; },
                         /*timeoutMs=*/300);
        settle(700);
        check(lateCalls == 1, "타임아웃으로 한 번 불린다");
        settle(2000);
        check(lateCalls == 1, "타임아웃 뒤 늦게 온 응답은 콜백을 다시 부르지 않는다");
    }

    std::printf(failures ? "\nFAIL (%d 실패)\n" : "\nOK\n", failures);
    return failures ? 1 : 0;
}
