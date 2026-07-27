import QtQuick 2.15
import "../Style.js" as S
import "../components"

Item {
    id: root
    property bool guiding: controller.guidePhase === "guiding" || controller.guidePhase === "requesterLost"
    property bool requesting: controller.guidePhase === "requesting"
    // 고르는 것과 출발하는 것을 나눈다 — 터치패널에서 한 번 잘못 누르면 곧바로 로봇이
    // 출발하던 것을 막는다. 지도든 칩이든 누르면 여기 담기고, 출발은 아래 버튼 하나뿐이다.
    property string picked: ""

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
        anchors { left: parent.left; top: header.bottom; margins: 28; topMargin: 12 }
        width: parent.width * 0.52
        // 도면 비율 그대로 — 세로로 늘리면 위아래가 빈 띠로 남는다(PreserveAspectFit).
        height: width / mapAspect
        highlight: root.guiding ? controller.guideDestination : root.picked
        // 탭은 **선택만** 한다. 출발은 아래 버튼.
        onFacilityClicked: (name) => { if (!root.guiding) root.picked = name }
    }

    // 오른쪽 패널
    Item {
        id: rightPanel
        anchors { left: map.right; right: parent.right; top: header.bottom; bottom: parent.bottom
                  leftMargin: 24; rightMargin: 28; topMargin: 12; bottomMargin: 28 }

        // (1) 목적지 선택 — 고르기만 하고, 출발은 맨 아래 버튼 하나로 모은다.
        Item {
            visible: !root.guiding && controller.guidePhase !== "completed"
            anchors.fill: parent

            Column {
                id: pickCol
                anchors { left: parent.left; right: parent.right; top: parent.top }
                spacing: 16
                Text {
                    text: "어디로 안내해드릴까요?"
                    font.family: S.fontFamily; font.pixelSize: 24; font.bold: true; color: S.text
                }
                // 지도가 그림뿐이라 "누를 수 있다"가 안 보인다 — 검색 화면에는 같은 안내가
                // 이미 있는데 여기만 없었다.
                Text {
                    text: "지도의 구역을 눌러도 고를 수 있어요"
                    font.family: S.fontFamily; font.pixelSize: 15; color: S.textMuted
                }
                Flow {
                    width: parent.width
                    spacing: 12
                    Repeater {
                        model: controller.facilities()
                        delegate: Chip {
                            icon: modelData.icon
                            text: modelData.name
                            selected: root.picked === modelData.name
                            onClicked: root.picked = modelData.name
                        }
                    }
                }
            }

            BigButton {
                anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom }
                enabledLook: root.picked !== "" && !root.requesting
                text: root.requesting ? "관제 승인 기다리는 중…"
                                      : (root.picked === "" ? "목적지를 골라주세요"
                                                            : root.picked + " (으)로 안내 시작")
                color: root.picked === "" ? S.bgAlt : S.primary
                textColor: root.picked === "" ? S.textMuted : S.onPrimary
                implicitWidth: parent.width; implicitHeight: 88
                onClicked: if (root.picked !== "" && !root.requesting) controller.startGuide(root.picked)
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
                // 남은 거리 — 실제 위치와 목적지 좌표로 잰 값이다. 위치를 모르면(-1)
                // 숫자를 지어내지 않고 그렇게 말한다.
                Column {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 2
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: controller.distanceToGoal >= 0
                              ? controller.distanceToGoal.toFixed(1) + " m" : "— m"
                        font.family: S.fontFamily; font.pixelSize: 56; font.bold: true; color: S.primary
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: controller.distanceToGoal >= 0 ? "남은 거리" : "위치 확인 중"
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
