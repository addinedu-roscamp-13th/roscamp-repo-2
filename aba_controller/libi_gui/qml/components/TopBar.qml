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
            // 보안순찰 중엔 chaseState(IntruderChase 내부 상태)로 문구만 바꾼다.
            // robotState 자체("보안순찰")는 안 바꾼다 — FMS 상위 상태는 그대로 유지,
            // 여기 텍스트만 그 안의 하위 상태를 덧보여준다.
            readonly property string securityLabel:
                controller.robotState !== "보안순찰" ? controller.robotState
                : controller.chaseState === "chasing" ? "추종중"
                : controller.chaseState === "release" ? "유실"
                : controller.robotState
            anchors.verticalCenter: parent.verticalCenter
            // 실물 로봇 LED 와 같은 규칙 — Style.js ledColorFor() 참고
            pillColor: S.ledColorFor(controller.robotState)
            text: securityLabel
        }

        // 응대 세션이 끝나기 직전 경고. 예전엔 화면 위를 덮는 배너였는데 상단바까지 가려
        // 배터리·비상정지가 안 보였다 — 상태 알약 옆 한 자리로 옮기고, 평소엔 아예 안 띄운다.
        //
        // "화면을 누르면 계속"은 **로봇이 해줘야 하는 약속**이라 rosConnected 일 때만 말한다.
        // 목 모드에서는 onScreenTouch 가 발행할 상대가 없어(RobotController.cpp:756) 눌러도
        // 세션이 안 늘어난다 — 그 상태에서 이 문구를 띄우면 화면이 거짓말을 한다.
        // ⚠️ [2026-08-02] **길잡이 중에는 안 띄운다.**
        //
        //   길잡이는 목적지를 고르고 등록하는 동안 로봇이 `INTERACTING` 에 머문다.
        //   그때 이 알약이 "곧 이동합니다 3초 · 화면을 누르면 계속" 을 띄우면
        //   **두 가지가 동시에 거짓이 된다**:
        //     · 로봇은 안내 목적지로 갈 뿐 "곧 이동" 하는 게 아니다
        //     · 등록 3초를 서서 기다려야 하는데 화면을 누르라고 재촉한다
        //   (실측 2026-08-02: 길잡이 목적지 선택 화면 위에 그대로 떴다)
        //
        //   ⚠️ [2026-08-02] `guidePhase` 만으로는 못 막는다. 목적지를 **고르는 동안**은
        //      아직 `idle` 이라(안내는 등록이 끝나야 `guiding` 이 된다) 그때 그대로 떴다.
        //      화면이 길잡이면 — 등록 전이든 후든 — 안 띄운다.
        StatusPill {
            readonly property bool guiding:
                controller.mode === "guide"
                || (controller.guidePhase !== "" && controller.guidePhase !== "idle"
                    && controller.guidePhase !== "completed" && controller.guidePhase !== "failed"
                    && controller.guidePhase !== "cancelled")
            visible: !guiding && controller.robotState === "이용중"
                     && controller.interactingRemaining <= 5
            anchors.verticalCenter: parent.verticalCenter
            pillColor: S.warning
            text: "곧 이동합니다 " + controller.interactingRemaining + "초"
                  + (controller.rosConnected ? " · 화면을 누르면 계속" : "")
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
