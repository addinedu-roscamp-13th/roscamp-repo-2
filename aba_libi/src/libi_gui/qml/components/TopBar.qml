import QtQuick 2.15
import "../Style.js" as S

// 상단 바: Libi 정체성 + 로봇상태 / 배터리 / 시계 / 관리자 배지
Rectangle {
    id: bar
    height: 76
    color: S.surface

    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: S.border }

    Row {
        anchors.left: parent.left
        anchors.leftMargin: 24
        anchors.verticalCenter: parent.verticalCenter
        spacing: 14

        Image {
            source: "../assets/arte_logo.png"
            height: 60
            fillMode: Image.PreserveAspectFit
            mipmap: true
            anchors.verticalCenter: parent.verticalCenter
        }
        Column {
            anchors.verticalCenter: parent.verticalCenter
            Text { text: "Libi"; font.bold: true; font.pixelSize: 22; color: S.text; font.family: S.fontFamily }
            Text { text: "도서관 사서 로봇"; font.pixelSize: 12; color: S.textMuted; font.family: S.fontFamily }
        }
        StatusPill {
            anchors.verticalCenter: parent.verticalCenter
            pillColor: controller.robotState === "에러" ? S.danger
                       : controller.robotState === "충전중" ? S.warning
                       : controller.robotState === "안내중" ? S.sky : S.success
            text: controller.robotState
        }
    }

    Row {
        anchors.right: parent.right
        anchors.rightMargin: 24
        anchors.verticalCenter: parent.verticalCenter
        spacing: 18

        StatusPill {
            visible: controller.isAdmin
            anchors.verticalCenter: parent.verticalCenter
            pillColor: S.lavender
            text: "관리자"
        }

        // 비상정지 (배터리 바로 옆)
        Rectangle {
            id: estopBtn
            visible: !controller.emergencyStopped
            width: estopRow.width + 34; height: 50; radius: 25
            color: estopMa.pressed ? Qt.darker(S.danger, 1.12) : S.danger
            anchors.verticalCenter: parent.verticalCenter
            Row {
                id: estopRow
                anchors.centerIn: parent
                spacing: 9
                Rectangle {
                    width: 17; height: 17; radius: 4; color: "white"
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: "비상정지"; color: "white"; font.bold: true
                    font.pixelSize: 17; font.family: S.fontFamily
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            scale: estopMa.pressed ? 0.95 : 1.0
            Behavior on scale { NumberAnimation { duration: 80 } }
            MouseArea { id: estopMa; anchors.fill: parent; onClicked: controller.emergencyStop() }
        }

        Row {
            spacing: 8
            anchors.verticalCenter: parent.verticalCenter
            Rectangle {
                width: 46; height: 24; radius: 5; color: "transparent"
                border.color: S.textMuted; border.width: 2
                anchors.verticalCenter: parent.verticalCenter
                Rectangle {
                    width: 3; height: 10; radius: 1; color: S.textMuted
                    anchors.left: parent.right; anchors.verticalCenter: parent.verticalCenter
                }
                Rectangle {
                    anchors.left: parent.left; anchors.leftMargin: 3
                    anchors.verticalCenter: parent.verticalCenter
                    width: (parent.width-6) * Math.max(0, Math.min(1, controller.battery/100))
                    height: parent.height-6; radius: 3
                    color: controller.battery < 15 ? S.danger : (controller.battery < 35 ? S.warning : S.success)
                    Behavior on width { NumberAnimation { duration: 300 } }
                }
            }
            Text {
                text: controller.battery + "%"
                font.pixelSize: 16; font.bold: true; color: S.text; font.family: S.fontFamily
                anchors.verticalCenter: parent.verticalCenter
            }
            Text { visible: controller.charging; text: "⚡"; font.pixelSize: 16; anchors.verticalCenter: parent.verticalCenter }
        }

        Text {
            id: clock
            anchors.verticalCenter: parent.verticalCenter
            font.pixelSize: 18; font.bold: true; color: S.text; font.family: S.fontFamily
            text: Qt.formatDateTime(new Date(), "hh:mm")
            Timer { interval: 10000; running: true; repeat: true; onTriggered: clock.text = Qt.formatDateTime(new Date(), "hh:mm") }
        }

        // 관리자 (톱니바퀴)
        Rectangle {
            id: adminBtn
            width: 48; height: 48; radius: 14
            color: gearMa.pressed ? S.bgAlt : "transparent"
            border.color: S.border; border.width: 1.5
            anchors.verticalCenter: parent.verticalCenter
            Text { anchors.centerIn: parent; text: "⚙️"; font.pixelSize: 24 }
            scale: gearMa.pressed ? 0.92 : 1.0
            Behavior on scale { NumberAnimation { duration: 80 } }
            MouseArea { id: gearMa; anchors.fill: parent; onClicked: controller.setMode("adminLogin") }
        }
    }
}
