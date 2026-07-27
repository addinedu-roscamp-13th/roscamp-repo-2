import QtQuick 2.15
import "../Style.js" as S
import "../components"

Item {
    id: root

    // 하단: 기능 버튼 가로 줄 (비중 축소)
    Row {
        id: bottomBar
        anchors {
            left: parent.left; right: parent.right; bottom: parent.bottom
            leftMargin: 28; rightMargin: 28
            bottomMargin: 20
        }
        height: 158
        spacing: 18
        property real cellW: (width - spacing * 2) / 3

        Repeater {
            model: [
                { icon: "🧭", title: "길잡이", sub: "목적지까지 안내해요", c: S.primary,  mode: "guide" },
                { icon: "🔎", title: "검색",   sub: "책·자료 위치 찾기",  c: S.sky,      mode: "search" },
                { icon: "✨", title: "추천",   sub: "취향 맞춤 도서",     c: S.lavender, mode: "recommend" }
            ]
            delegate: Item {
                width: bottomBar.cellW
                height: bottomBar.height

                // 부드러운 그림자 (카드 아래에 살짝)
                Rectangle {
                    x: 6; y: 9
                    width: parent.width - 12; height: card.height
                    radius: card.radius
                    color: S.shadow
                }

                Rectangle {
                    id: card
                    anchors.fill: parent
                    radius: 22
                    color: S.surface
                    border.color: S.border; border.width: 1.5
                    scale: ma.pressed ? 0.97 : 1.0
                    Behavior on scale { NumberAnimation { duration: 90; easing.type: Easing.OutQuad } }

                    Row {
                        anchors { left: parent.left; verticalCenter: parent.verticalCenter; leftMargin: 28 }
                        spacing: 20

                        Rectangle {
                            width: 100; height: 100; radius: 28
                            color: modelData.c
                            anchors.verticalCenter: parent.verticalCenter
                            Text { anchors.centerIn: parent; text: modelData.icon; font.pixelSize: 56 }
                        }
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 6
                            Text { text: modelData.title; font.family: S.fontFamily; font.pixelSize: 40; font.bold: true; color: S.text }
                            Text { text: modelData.sub;   font.family: S.fontFamily; font.pixelSize: 21; color: S.textMuted }
                        }
                    }

                    Text {
                        anchors { right: parent.right; verticalCenter: parent.verticalCenter; rightMargin: 22 }
                        text: "›"; font.pixelSize: 44; color: S.textMuted
                    }

                    MouseArea { id: ma; anchors.fill: parent; onClicked: controller.setMode(modelData.mode) }
                }
            }
        }
    }

    // 상단: 표정(주인공) + 인사 + 순찰 상태
    Item {
        id: faceRegion
        anchors {
            top: parent.top; bottom: bottomBar.top
            left: parent.left; right: parent.right
            topMargin: 10
        }

        Column {
            anchors.centerIn: parent
            spacing: 12

            RobotFace {
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(faceRegion.width * 0.9, faceRegion.height - 118)
                height: width
            }

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "안녕하세요, 리비예요!"
                font.family: S.fontFamily; font.pixelSize: 32; font.bold: true; color: S.text
            }

            // 로봇이 **지금 무슨 상태인지** 그대로 말한다. 예전엔 `patrolActive`(목 플래그)만
            // 보고 "순찰 중"을 하드코딩해서, 상단바가 "응대중"인데 여기만 "순찰 중"이라고
            // 하는 상황이 났다(실측). 같은 화면이 같은 것을 두 가지로 말하면 안 된다.
            StatusPill {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: text !== ""
                pillColor: S.ledColorFor(controller.robotState)
                text: {
                    switch (controller.robotState) {
                    case "순찰":
                    case "보안순찰":   return controller.robotState + " 중 · 도움이 필요하면 불러주세요";
                    case "응대중":     return "도와드릴게요 · 무엇을 찾으세요?";
                    case "대기":       return "대기 중 · 화면을 눌러 불러주세요";
                    case "작업중":
                    case "안내중":     return controller.robotState + " · 잠시만요";
                    case "충전중":     return "충전 중 · 조금 뒤에 도와드릴게요";
                    case "복귀중":     return "복귀 중 · 잠시만요";
                    default:           return "";   // 에러 등 — 여기서 안내할 말이 없다
                    }
                }
            }
        }
    }
}
