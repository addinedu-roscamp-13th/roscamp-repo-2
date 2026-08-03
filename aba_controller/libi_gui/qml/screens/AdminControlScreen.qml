import QtQuick 2.15
import "../Style.js" as S
import "../components"

Item {
    id: root

    // 앞카메라 미리보기용 — FollowScreen.qml:106 과 같은 수명 규칙. 화면을 떠나면
    // 끊어 AI 서버 TCP 를 붙들지 않는다. start() 는 **영상을 받겠다**는 뜻일 뿐,
    // 로봇 카메라를 켜지는 않는다(그건 BT 의 camera_select).
    Component.onCompleted: perception.start()
    Component.onDestruction: perception.stop()

    ScreenHeader {
        id: header
        anchors { left: parent.left; top: parent.top; margins: 28 }
        width: parent.width - 56
        emoji: "🛠"; title: "작업 상태"
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

        // ── 왼쪽: 조작/제어 (작업 상태 · 복구 제어) ──
        Card {
            width: (parent.width - 20) * 0.5
            height: parent.height
            Column {
                anchors { fill: parent; margins: 24 }
                spacing: 14

                Text { text: "📋  작업 상태"; font.family: S.fontFamily; font.pixelSize: 22; font.bold: true; color: S.text }

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

                Rectangle { width: parent.width; height: 1; color: S.border }

                // ── 보고 있는 카메라 (주행 중이면 앞캠 · 추종/길잡이면 그 세션의 캠) ──
                // 영상 통로는 추종·길잡이와 같다(PERCEPTION_URL → AI 서버 TCP).
                // 카메라를 켜는 건 여기가 아니라 BT 다 — 이 화면은 **모니터**지 스위치가
                // 아니다. 주행(navigate)이 아니면 `camera_select` 가 none 이라 프레임이
                // 아예 안 온다. 그래서 아래 안내 문구가 "고장" 으로 안 읽히게 설명한다.
                Row {
                    width: parent.width; spacing: 12
                    Text {
                        text: "🎥  보고 있는 카메라"
                        font.family: S.fontFamily; font.pixelSize: 20; font.bold: true; color: S.text
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    StatusPill {
                        anchors.verticalCenter: parent.verticalCenter
                        pillColor: controller.personBlocked ? S.danger : S.success
                        text: controller.personBlocked ? "정지 — 사람" : "정지 없음"
                    }
                    // 지금 몇 px 인가. 임계값을 실기에서 맞추려면 이 숫자가 있어야 한다 —
                    // 없으면 params.yaml 의 person_stop_size 를 감으로 고치게 된다.
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: controller.frontPersonSize > 0
                        text: Math.round(controller.frontPersonSize) + " px"
                        font.family: S.fontFamily; font.pixelSize: 15
                        color: controller.personBlocked ? S.danger : S.textMuted
                    }
                }

                // 사람 때문에 재탐색이 걸리기까지 남은 초. **로봇이 판정에 쓰는 값 그대로**다
                // (`/libi/fsm_state` 의 `person_block_in`) — 화면이 따로 계산하지 않는다.
                // 이게 없으면 관제에서 재계획이 사람 때문인지 지연 때문인지 못 가른다.
                Rectangle {
                    width: parent.width
                    height: visible ? 44 : 0
                    visible: controller.personBlockIn >= 0
                    radius: 12
                    color: Qt.rgba(1, 0.42, 0.42, 0.14)
                    border.color: S.danger; border.width: 1
                    Text {
                        anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter }
                        text: "⏱  " + controller.personBlockIn.toFixed(1) + "초 뒤 경로를 재탐색합니다"
                        font.family: S.fontFamily; font.pixelSize: 16; font.bold: true; color: S.danger
                    }
                }

                Rectangle {
                    width: parent.width
                    // 남은 세로를 전부 먹는다 — 고정 높이로 두면 4:3 영상이 가로로
                    // 레터박스돼 검은 띠만 커지고, 카드 아래는 통으로 빈다.
                    // `y` 는 Column 이 위 형제들 높이로 정해 주므로 자기 높이에 의존하지
                    // 않는다(바인딩 루프 없음).
                    height: Math.max(200, parent.height - y)
                    radius: S.radCard
                    color: "#1E1A18"
                    clip: true

                    Image {
                        id: frontView
                        anchors.fill: parent
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        asynchronous: false
                        // frameCounter 가 바뀔 때마다 URL 이 달라져야 새 프레임을 읽는다.
                        source: perception.frameCounter > 0
                                ? "image://perception/frame?" + perception.frameCounter
                                : ""
                        visible: perception.frameCounter > 0
                    }

                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 32
                        visible: !frontView.visible
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        text: perception.connected
                              ? "📷  주행 중에만 영상이 나옵니다"
                              : "🔌  영상 서버 연결 중"
                        font.family: S.fontFamily; font.pixelSize: 16; color: "#B9AEA6"
                    }

                    // 정지 중일 때 화면 자체를 빨갛게 둘러 관리자가 멀리서도 안다.
                    Rectangle {
                        anchors.fill: parent
                        radius: parent.radius
                        color: "transparent"
                        border.color: S.danger; border.width: 3
                        visible: controller.personBlocked
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
            // 비우기 — 화면 목록만 지운다. 로봇에는 아무것도 안 보낸다.
            Rectangle {
                id: logClear
                anchors { right: parent.right; rightMargin: 24; verticalCenter: logTitle.verticalCenter }
                width: 74; height: 34; radius: 17
                color: clearArea.pressed ? S.border : S.bgAlt
                visible: controller.logs.length > 0
                Text {
                    anchors.centerIn: parent
                    text: "🗑  비우기"
                    font.family: S.fontFamily; font.pixelSize: 13; color: S.textSoft
                }
                MouseArea {
                    id: clearArea
                    anchors.fill: parent
                    onClicked: controller.clearLogs()
                }
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
