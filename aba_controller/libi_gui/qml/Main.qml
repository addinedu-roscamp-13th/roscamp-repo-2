import QtQuick 2.15
import QtQuick.Window 2.15
import QtQuick.Controls 2.15
import "Style.js" as S
import "components"
import "screens"

ApplicationWindow {
    id: win
    visible: true
    width: S.screenW
    height: S.screenH
    title: "Libi GUI"
    color: S.bg

    TopBar {
        id: topBar
        anchors.top: parent.top
        width: parent.width
    }

    Item {
        id: contentArea
        anchors.top: topBar.bottom
        anchors.bottom: parent.bottom
        width: parent.width

        Loader {
            id: pageLoader
            anchors.fill: parent
            sourceComponent: {
                switch (controller.mode) {
                case "guide":        return guideC;
                case "search":       return searchC;
                case "recommend":    return recommendC;
                case "adminLogin":   return adminLoginC;
                case "adminControl": return adminControlC;
                case "follow":       return followC;
                default:             return homeC;
                }
            }
        }
    }

    // 전역 터치 감지 — 매 탭을 로봇에 알려 INTERACTING 진입/유지. 아래 화면 입력은 그대로 통과.
    // z 는 페이지 콘텐츠(z:0) **위**, 배너(z:90)·비상정지(z:100) **아래**여야 한다. z:-1 이면
    // 맨 아래라 hit-test 가 위의 버튼을 먼저 집어 이 MouseArea 는 탭을 아예 못 받고
    // (accepted=false/propagate 는 이벤트를 받은 뒤에만 의미) → 20초 세션이 갱신도, 첫
    // ui_touch 진입도 안 된다. 위에 두고 press 를 거절(accepted=false)해 아래 버튼으로 흘린다.
    MouseArea {
        anchors.fill: parent
        propagateComposedEvents: true
        z: 80
        onPressed: (mouse) => { controller.onScreenTouch(); mouse.accepted = false; }
    }

    Component { id: homeC;        HomeScreen {} }
    Component { id: guideC;       GuideScreen {} }
    Component { id: searchC;      SearchScreen {} }
    Component { id: recommendC;   RecommendScreen {} }
    Component { id: adminLoginC;  AdminLoginScreen {} }
    Component { id: adminControlC;AdminControlScreen {} }
    Component { id: followC;      FollowScreen {} }

    // 추종이 시작되면 영상 화면으로, 끝나면 돌아온다. 화면 전환을 QML 이 맡으므로
    // RobotController 와 PerceptionClient 는 서로를 몰라도 된다.
    //
    // **어디로 돌아가느냐는 누가 끝냈느냐로 갈린다:**
    //   관리자가 「해제」   → 관리자 화면 (그 사람이 아직 앞에 있다)
    //   로봇이 스스로 종료 → 홈 (사람을 못 찾아 포기했거나 관제가 상태를 바꿨다.
    //                        로봇도 순찰로 복귀하므로 관리자 화면에 남으면 어긋난다)
    Connections {
        target: controller
        function onFollowingChanged() {
            if (controller.following) {
                // ⚠️ [2026-08-02] **시작할 때도 비운다 — 이게 진짜 보장이다.**
                //
                //   아래 종료 경로의 resetTarget() 은 종료를 **감지해야** 돈다. 그 감지가
                //   상태 메시지 수신에 달려 있어서, 한 번 놓치면 옛 등록이 그대로 남고
                //   다음 세션이 **등록도 안 했는데 옛 사람을 따라간다**(사용자 보고).
                //   시작 시점에 비우면 이전 세션이 어떻게 끝났든 상관이 없다 —
                //   "다시 누르면 다시 등록"이 어느 경로에서도 성립한다.
                //   reset 은 멱등이라 종료 때와 두 번 돌아도 무해하다.
                perception.resetTarget();
                controller.setMode("follow");
            } else {
                // 추종이 끝났다 — 수동 「해제」(stopAdminFollow)든 로봇이 스스로 끝냈든
                // 여기 한 곳으로 다 모인다. 등록된 타겟을 안 지우면 AI 서버가 이전 사람을
                // 계속 등록된 상태로 들고 있어서, 다음 추종을 시작해도 새로 등록하기 전까진
                // 옛 타겟이 남는다.
                perception.resetTarget();
                if (controller.mode === "follow") controller.setMode("adminControl");
            }
        }
        function onFollowEndedByRobot() {
            controller.setMode("home");
        }

        // ⚠️ [2026-08-02] **길잡이도 같은 규칙을 따른다 — 시작할 때와 끝날 때 둘 다 비운다.**
        //
        //   길잡이에는 `resetTarget()` 이 **한 곳도 없었다.** 추종만 위 경로로 비우고
        //   있었는데, 등록 템플릿(`matcher`)은 AI 서버에 **하나뿐**이라 길잡이가 남긴
        //   주인이 그대로 남는다. 그 결과 두 가지가 같이 깨진다:
        //
        //     · 안내가 끝났는데(취소·회복 실패·도착) `isOwner` 가 계속 true 다.
        //       화면·BT 가 "요청자가 아직 보인다" 로 읽는다.
        //     · 다음 등록에서 **아무도 안 서 있어도** 옛 주인이 잡혀 `isOwner=true` 가
        //       오므로, `reportRegistrationOwnerSeen()` 게이트가 그대로 통과한다 —
        //       방금 넣은 "진짜 등록됐을 때만 넘어간다"가 무력화된다.
        //
        //   reset 은 멱등이라 두 경로가 겹쳐도 무해하다(추종 쪽 주석과 같은 근거).
        function onGuideRegPhaseChanged() {
            // 등록 화면에 들어왔다 = 새로 등록할 참이다. 옛 주인을 먼저 지운다.
            if (controller.guideRegPhase === "registering") perception.resetTarget();
        }
        function onGuidePhaseChanged() {
            var p = controller.guidePhase;
            if (p === "completed" || p === "cancelled" || p === "failed")
                perception.resetTarget();
        }
    }

    // INTERACTING 안내는 화면을 덮지 않는다 — 상단바(TopBar.qml)의 알약이 남은 시간을
    // 보여준다. 여기 있던 배너/모달은 그 자리에서 상단바와 화면을 가려서 없앴다.

    // 비상정지 오버레이 (관리자 로그인/조작 화면에서는 숨겨 해제 흐름 허용)
    Rectangle {
        id: estopOverlay
        visible: controller.emergencyStopped
                 && controller.mode !== "adminLogin"
                 && controller.mode !== "adminControl"
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.70)
        z: 100
        MouseArea { anchors.fill: parent }   // 하위 입력 차단

        Column {
            anchors.centerIn: parent
            spacing: 22
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "⛔"; font.pixelSize: 96 }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: "비상정지"; color: "white"; font.bold: true; font.pixelSize: 52; font.family: S.fontFamily }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "모든 동작이 중단되었습니다.\n관리자만 해제할 수 있습니다."
                horizontalAlignment: Text.AlignHCenter
                color: "white"; font.pixelSize: 22; font.family: S.fontFamily
            }
            BigButton {
                anchors.horizontalCenter: parent.horizontalCenter
                text: controller.isAdmin ? "비상정지 해제" : "관리자 로그인 후 해제"
                color: S.success
                implicitWidth: 320; implicitHeight: 92
                onClicked: {
                    if (controller.isAdmin) controller.clearEmergencyStop();
                    else controller.setMode("adminLogin");
                }
            }
        }
    }

    // 토스트 알림
    Rectangle {
        id: toastBox
        property string message: ""
        visible: opacity > 0
        opacity: 0
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 46
        height: 56
        width: toastText.implicitWidth + 56
        radius: 28
        color: Qt.rgba(0.25, 0.21, 0.19, 0.95)
        z: 200
        Text {
            id: toastText
            anchors.centerIn: parent
            text: toastBox.message
            color: "white"; font.pixelSize: 18; font.family: S.fontFamily
        }
        Behavior on opacity { NumberAnimation { duration: 200 } }
        Timer { id: toastTimer; interval: 1900; onTriggered: toastBox.opacity = 0 }
        Connections {
            target: controller
            function onToast(message) {
                toastBox.message = message;
                toastBox.opacity = 1;
                toastTimer.restart();
            }
        }
    }
}
