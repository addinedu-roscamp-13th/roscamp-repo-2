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
            if (controller.following) controller.setMode("follow");
            else if (controller.mode === "follow") controller.setMode("adminControl");
        }
        function onFollowEndedByRobot() {
            controller.setMode("home");
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
