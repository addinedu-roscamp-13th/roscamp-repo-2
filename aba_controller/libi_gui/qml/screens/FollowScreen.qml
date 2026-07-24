import QtQuick 2.15
import "../Style.js" as S
import "../components"

// 관리자 추종 화면 — AI 서버(perception_server)의 영상을 그대로 띄우고 등록/해제를 누른다.
// bbox·OWNER 라벨은 서버가 JPEG 안에 이미 그려 보내므로 여기서 따로 그리지 않는다.
Item {
    id: root

    // 화면이 살아있는 동안만 스트림을 받는다 — 추종을 안 하는데 영상을 계속 끌어오면
    // AI 서버와 네트워크만 쓴다.
    Component.onCompleted: perception.start()
    Component.onDestruction: perception.stop()

    Column {
        anchors.fill: parent
        anchors.margins: S.pad
        spacing: 14

        ScreenHeader {
            width: parent.width
            title: "관리자 추종"
            emoji: "🧑‍🤝‍🧑"
            onBack: controller.setMode("adminControl")
        }

        // 영상 (3등분 방향 가이드선·bbox·상태값은 서버가 프레임에 그려서 보낸다)
        Rectangle {
            width: parent.width
            height: parent.height - (lidarRow.visible ? 316 : 250)
            radius: S.radCard
            color: "#1E1A18"
            clip: true

            Image {
                id: view
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

            // 첫 프레임이 오기 전 / 끊겼을 때
            Column {
                anchors.centerIn: parent
                spacing: 12
                visible: !view.visible
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: perception.connected ? "📷" : "🔌"
                    font.pixelSize: 64
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: perception.connected ? "영상 대기 중…" : "AI 서버에 연결 중…"
                    color: "white"; font.pixelSize: 22; font.family: S.fontFamily
                }
            }

            // 연결 상태 배지
            Rectangle {
                anchors.top: parent.top; anchors.right: parent.right; anchors.margins: 12
                width: badge.implicitWidth + 24; height: 34; radius: S.radPill
                color: perception.connected ? S.success : S.danger
                Text {
                    id: badge
                    anchors.centerIn: parent
                    text: perception.connected ? "연결됨" : "끊김"
                    color: "white"; font.pixelSize: 15; font.bold: true; font.family: S.fontFamily
                }
            }
        }

        // 장애물 거리 — 영상의 3등분 방향 구역(좌/중앙/우)과 같은 순서로 놓는다.
        // 서버가 --lidar-ros 로 떴을 때만 값이 온다.
        Row {
            id: lidarRow
            width: parent.width
            height: 56
            spacing: 10
            visible: Object.keys(perception.lidar).length > 0
            property real cellW: (width - spacing * 2) / 3

            // cm -> 표시 문자열. -1(측정값 없음)은 숫자로 보여주면 안 된다.
            function fmt(cm) { return (cm === undefined || cm < 0) ? "—" : (cm / 100).toFixed(2) + " m" }
            // 가까울수록 붉게. 임계값은 표시용이며 회피 판단은 서버가 한다.
            function tone(cm) {
                if (cm === undefined || cm < 0) return S.textMuted;
                if (cm < 50) return S.danger;
                if (cm < 100) return S.warning;
                return S.text;
            }

            Repeater {
                model: [
                    { label: "◀ 좌", value: perception.lidar.frontLeft },
                    { label: "▲ 정면", value: perception.lidar.front },
                    { label: "우 ▶", value: perception.lidar.frontRight }
                ]
                Rectangle {
                    width: lidarRow.cellW; height: lidarRow.height
                    radius: S.radButton
                    color: S.surface; border.color: S.border; border.width: 1.5
                    Column {
                        anchors.centerIn: parent
                        spacing: 2
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: modelData.label
                            color: S.textMuted; font.pixelSize: 13; font.family: S.fontFamily
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: lidarRow.fmt(modelData.value)
                            color: lidarRow.tone(modelData.value)
                            font.pixelSize: 20; font.bold: true; font.family: S.fontFamily
                        }
                    }
                }
            }
        }

        // 상태 문구 (연결 오류 사유가 여기 뜬다)
        Text {
            width: parent.width
            text: perception.statusText
            color: S.textMuted; font.pixelSize: 15; font.family: S.fontFamily
            elide: Text.ElideRight
        }

        // 등록 / 해제
        Row {
            width: parent.width
            spacing: 14
            property real cellW: (width - spacing) / 2

            BigButton {
                implicitWidth: parent.cellW; implicitHeight: 84
                text: "🎯  등록"; color: S.success; textColor: "white"
                enabledLook: perception.connected
                onClicked: perception.registerTarget()
            }
            BigButton {
                implicitWidth: parent.cellW; implicitHeight: 84
                text: "↩  해제"; color: S.bgAlt; textColor: S.text
                enabledLook: perception.connected
                onClicked: perception.resetTarget()
            }
        }
    }
}
