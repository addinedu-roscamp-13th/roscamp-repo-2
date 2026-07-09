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
                default:             return homeC;
                }
            }
        }
    }

    Component { id: homeC;        HomeScreen {} }
    Component { id: guideC;       GuideScreen {} }
    Component { id: searchC;      SearchScreen {} }
    Component { id: recommendC;   RecommendScreen {} }
    Component { id: adminLoginC;  AdminLoginScreen {} }
    Component { id: adminControlC;AdminControlScreen {} }

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
