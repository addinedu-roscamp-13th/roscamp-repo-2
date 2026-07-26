import QtQuick 2.15
import "../Style.js" as S
import "../components"

Item {
    id: root

    ScreenHeader {
        id: header
        anchors { left: parent.left; top: parent.top; margins: 28 }
        width: parent.width - 56
        emoji: "🛠"; title: "작업 상태 및 에러"
        onBack: controller.setMode("home")
    }
    BigButton {
        id: logoutBtn
        anchors { right: parent.right; top: parent.top; rightMargin: 28; topMargin: 28 }
        implicitWidth: 150; implicitHeight: 56
        text: "로그아웃"; color: S.bgAlt; textColor: S.textSoft
        onClicked: controller.logout()
    }

    Row {
        anchors { left: parent.left; right: parent.right; top: header.bottom; bottom: parent.bottom
                  leftMargin: 28; rightMargin: 28; topMargin: 14; bottomMargin: 28 }
        spacing: 20

        // ── 왼쪽: 조작/제어 (작업 상태 및 에러 · 복구 제어) ──
        Card {
            width: (parent.width - 20) * 0.5
            height: parent.height
            Column {
                anchors { fill: parent; margins: 24 }
                spacing: 14

                Text { text: "📋  작업 상태 및 에러"; font.family: S.fontFamily; font.pixelSize: 22; font.bold: true; color: S.text }

                // 로봇 상태 / 작업 상태
                Column {
                    width: parent.width; spacing: 12
                    Row {
                        width: parent.width
                        Text { text: "로봇 상태"; font.family: S.fontFamily; font.pixelSize: 16; color: S.textMuted; anchors.verticalCenter: parent.verticalCenter }
                        Item { width: parent.width - robotVal.width - 90; height: 1 }
                        Text {
                            id: robotVal
                            text: controller.robotState
                            font.family: S.fontFamily; font.pixelSize: 30; font.bold: true
                            color: controller.robotState === "에러" ? S.danger : S.text
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        width: parent.width; spacing: 12
                        Text { text: "작업 상태"; font.family: S.fontFamily; font.pixelSize: 16; color: S.textMuted; anchors.verticalCenter: parent.verticalCenter }
                        StatusPill {
                            anchors.verticalCenter: parent.verticalCenter
                            pillColor: controller.taskStatus === "비상정지" ? S.danger
                                       : controller.taskStatus === "명령 대기" ? S.success : S.sky
                            text: controller.taskStatus
                        }
                    }
                }

                // 에러 배너 (에러/비상정지일 때)
                Rectangle {
                    visible: controller.robotState === "에러"
                    width: parent.width; height: visible ? 64 : 0
                    radius: 16
                    color: Qt.rgba(1, 0.42, 0.42, 0.16)
                    border.color: S.danger; border.width: 2
                    Text {
                        anchors { left: parent.left; leftMargin: 18; verticalCenter: parent.verticalCenter }
                        text: controller.emergencyStopped
                              ? "⛔ 비상정지 상태 — 모든 동작이 잠겨 있습니다"
                              : "⚠️ 에러 상태 — 로봇이 정지했습니다"
                        font.family: S.fontFamily; font.pixelSize: 17; font.bold: true; color: S.danger
                    }
                }

                Rectangle { width: parent.width; height: 1; color: S.border }

                Text { text: "🔄  복구 제어"; font.family: S.fontFamily; font.pixelSize: 20; font.bold: true; color: S.text }

                // 상황별 복구 버튼 (해당 상태일 때만 노출)
                Grid {
                    width: parent.width
                    columns: 2
                    columnSpacing: 14; rowSpacing: 14
                    property real cellW: (width - columnSpacing) / 2

                    BigButton {
                        visible: controller.guidePhase === "guiding"
                        implicitWidth: parent.cellW; implicitHeight: 76
                        text: "🚫  안내 취소"; color: S.bgAlt; textColor: S.text
                        onClicked: controller.cancelGuide()
                    }
                    BigButton {
                        visible: controller.robotState === "에러"
                        implicitWidth: parent.cellW; implicitHeight: 76
                        text: "🩹  에러 해제"; color: S.warning; textColor: "white"
                        onClicked: controller.clearError()
                    }
                    BigButton {
                        visible: controller.robotState !== "대기" && controller.robotState !== "에러"
                        implicitWidth: parent.cellW; implicitHeight: 76
                        text: "⏸  대기(idle) 복귀"; color: S.sky; textColor: S.text
                        enabledLook: !controller.emergencyStopped
                        onClicked: controller.resetToIdle()
                    }
                    BigButton {
                        visible: controller.robotState === "대기"
                        implicitWidth: parent.cellW; implicitHeight: 76
                        text: "🚶  순찰 시작"; color: S.success; textColor: "white"
                        enabledLook: !controller.emergencyStopped
                        onClicked: controller.startPatrol()
                    }
                    BigButton {
                        visible: !controller.following
                        implicitWidth: parent.cellW; implicitHeight: 76
                        text: "🧑‍🤝‍🧑  관리자 추종"; color: S.sky; textColor: S.text
                        enabledLook: !controller.emergencyStopped
                        onClicked: controller.startAdminFollow()
                    }
                    BigButton {
                        visible: controller.following
                        implicitWidth: parent.cellW; implicitHeight: 76
                        text: "🛑  추종 종료"; color: S.warning; textColor: "white"
                        onClicked: controller.stopAdminFollow()
                    }
                }
            }
        }

        // ── 오른쪽: 작업 상태 대시보드 ('작업 상태' 화면 인라인) ──
        Card {
            width: (parent.width - 20) * 0.5
            height: parent.height

            Column {
                id: statusTop
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 24 }
                spacing: 16

                Text { text: "📋  작업 상태"; font.family: S.fontFamily; font.pixelSize: 22; font.bold: true; color: S.text }

                // 로봇 상태 (표정 + 상태)
                Row {
                    spacing: 18
                    RobotFace { width: 72; height: 72; anchors.verticalCenter: parent.verticalCenter }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 4
                        Text { text: "로봇 상태"; font.family: S.fontFamily; font.pixelSize: 16; color: S.textMuted }
                        Text {
                            text: controller.robotState
                            font.family: S.fontFamily; font.pixelSize: 30; font.bold: true
                            color: controller.robotState === "에러" ? S.danger : S.text
                        }
                    }
                }

                // 작업 상태
                Row {
                    spacing: 14
                    Text { text: "작업 상태"; font.family: S.fontFamily; font.pixelSize: 16; color: S.textMuted; anchors.verticalCenter: parent.verticalCenter }
                    StatusPill {
                        anchors.verticalCenter: parent.verticalCenter
                        pillColor: controller.taskStatus === "비상정지" ? S.danger
                                   : controller.taskStatus === "명령 대기" ? S.success : S.sky
                        text: controller.taskStatus
                    }
                }

                // 배터리
                Column {
                    width: parent.width; spacing: 8
                    Row {
                        width: parent.width
                        Text { text: "배터리"; font.family: S.fontFamily; font.pixelSize: 16; color: S.textMuted; anchors.verticalCenter: parent.verticalCenter }
                        Item { width: parent.width - battVal.width - 60; height: 1 }
                        Text {
                            id: battVal
                            text: controller.battery + "%" + (controller.charging ? "  ⚡" : "")
                            font.family: S.fontFamily; font.pixelSize: 26; font.bold: true
                            color: controller.battery < 15 ? S.danger : S.text
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Rectangle {
                        width: parent.width; height: 16; radius: 8; color: S.bgAlt
                        Rectangle {
                            height: parent.height; radius: 8
                            width: parent.width * Math.max(0, Math.min(1, controller.battery/100))
                            color: controller.battery < 15 ? S.danger : (controller.battery < 35 ? S.warning : S.success)
                            Behavior on width { NumberAnimation { duration: 300 } }
                        }
                    }
                }

                Rectangle { width: parent.width; height: 1; color: S.border }

                // 현재 작업
                Row {
                    spacing: 14
                    Text { text: "🛎"; font.pixelSize: 30; anchors.verticalCenter: parent.verticalCenter }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 4
                        Text { text: "현재 작업"; font.family: S.fontFamily; font.pixelSize: 16; color: S.textMuted }
                        Text {
                            text: (controller.guidePhase === "guiding" || controller.guidePhase === "requesterLost")
                                  ? (controller.guideDestination + " 안내 중 · 남은 " + controller.distanceToGoal.toFixed(1) + "m")
                                  : "진행 중인 작업이 없습니다"
                            font.family: S.fontFamily; font.pixelSize: 20; font.bold: true; color: S.text
                        }
                    }
                }
            }

            // 최근 기록 (로그)
            Text {
                id: logTitle
                anchors { left: parent.left; top: statusTop.bottom; margins: 24; topMargin: 16 }
                text: "📜  최근 기록"
                font.family: S.fontFamily; font.pixelSize: 20; font.bold: true; color: S.text
            }
            ListView {
                anchors { left: parent.left; right: parent.right; top: logTitle.bottom; bottom: parent.bottom
                          leftMargin: 24; rightMargin: 24; topMargin: 8; bottomMargin: 20 }
                clip: true; spacing: 5
                model: controller.logs
                delegate: Text { width: ListView.view.width; text: modelData; font.family: S.fontFamily; font.pixelSize: 13; color: S.textSoft; elide: Text.ElideRight }
            }
        }
    }
}
