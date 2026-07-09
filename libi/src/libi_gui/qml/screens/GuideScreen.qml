import QtQuick 2.15
import "../Style.js" as S
import "../components"

Item {
    id: root
    property bool guiding: controller.guidePhase === "guiding" || controller.guidePhase === "requesterLost"

    ScreenHeader {
        id: header
        anchors { left: parent.left; top: parent.top; margins: 28 }
        width: parent.width - 56
        emoji: "🧭"; title: "길잡이"
        onBack: { if (root.guiding) controller.cancelGuide(); controller.setMode("home") }
    }

    // 왼쪽: 지도
    MapView {
        id: map
        anchors { left: parent.left; top: header.bottom; bottom: parent.bottom; margins: 28; topMargin: 12 }
        width: parent.width * 0.52
        highlight: root.guiding ? controller.guideDestination : ""
        robotY: root.guiding ? 0.55 : 0.90
        robotX: 0.50
    }

    // 오른쪽 패널
    Item {
        id: rightPanel
        anchors { left: map.right; right: parent.right; top: header.bottom; bottom: parent.bottom
                  leftMargin: 24; rightMargin: 28; topMargin: 12; bottomMargin: 28 }

        // (1) 목적지 선택
        Column {
            visible: !root.guiding && controller.guidePhase !== "completed"
            anchors.fill: parent
            spacing: 16
            Text {
                text: "어디로 안내해드릴까요?"
                font.family: S.fontFamily; font.pixelSize: 24; font.bold: true; color: S.text
            }
            Flow {
                width: parent.width
                spacing: 12
                Repeater {
                    model: controller.facilities()
                    delegate: Chip {
                        icon: modelData.icon
                        text: modelData.name
                        onClicked: controller.startGuide(modelData.name)
                    }
                }
            }
        }

        // (2) 안내 중
        Card {
            visible: root.guiding
            anchors.fill: parent
            Column {
                anchors.centerIn: parent
                width: parent.width - 48
                spacing: 18
                RobotFace { anchors.horizontalCenter: parent.horizontalCenter; width: 150; height: 150 }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    text: controller.guideDestination + " (으)로 안내 중"
                    font.family: S.fontFamily; font.pixelSize: 24; font.bold: true; color: S.text
                    wrapMode: Text.WordWrap
                }
                // 상태 문구 (시나리오 정확 문구)
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: controller.guidePhase === "requesterLost" ? "요청자를 찾는 중입니다" : "안내 시작 — 따라오세요!"
                    font.family: S.fontFamily; font.pixelSize: 18
                    color: controller.guidePhase === "requesterLost" ? S.warning : S.textSoft
                }
                // 남은 거리
                Column {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 2
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: controller.distanceToGoal.toFixed(1) + " m"
                        font.family: S.fontFamily; font.pixelSize: 56; font.bold: true; color: S.primary
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: "남은 거리"
                        font.family: S.fontFamily; font.pixelSize: 15; color: S.textMuted
                    }
                }
                BigButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "안내 취소"; color: S.bgAlt; textColor: S.textSoft
                    implicitWidth: 200; implicitHeight: 76
                    onClicked: controller.cancelGuide()
                }
            }
        }

        // (3) 도착 완료
        Card {
            visible: controller.guidePhase === "completed"
            anchors.fill: parent
            Column {
                anchors.centerIn: parent
                spacing: 18
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: "🎉"; font.pixelSize: 72 }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "목적지 도착 / 안내 종료"
                    font.family: S.fontFamily; font.pixelSize: 26; font.bold: true; color: S.text
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "이용해 주셔서 감사합니다!"
                    font.family: S.fontFamily; font.pixelSize: 18; color: S.textSoft
                }
                BigButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "처음으로"; color: S.primary
                    implicitWidth: 220; implicitHeight: 80
                    onClicked: controller.setMode("home")
                }
            }
        }
    }
}
