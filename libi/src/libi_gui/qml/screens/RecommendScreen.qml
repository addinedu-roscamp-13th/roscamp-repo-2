import QtQuick 2.15
import QtQuick.Controls 2.15
import "../Style.js" as S
import "../components"

Item {
    id: root
    property string purpose: "자기개발"
    property string interest: "과학"
    property var recs: controller.recommend(purpose, interest)

    ScreenHeader {
        id: header
        anchors { left: parent.left; top: parent.top; margins: 28 }
        width: parent.width - 56
        emoji: "✨"; title: "추천"
        onBack: controller.setMode("home")
    }

    // 왼쪽: 선택
    Card {
        id: leftPanel
        anchors { left: parent.left; top: header.bottom; bottom: parent.bottom; margins: 28; topMargin: 12 }
        width: parent.width * 0.36

        Column {
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 28 }
            spacing: 22

            Text { text: "읽는 목적"; font.family: S.fontFamily; font.pixelSize: 20; font.bold: true; color: S.text }
            Flow {
                width: parent.width; spacing: 10
                Chip { text: "자기개발"; icon: "🌱"; selected: root.purpose === "자기개발"; onClicked: root.purpose = "자기개발" }
                Chip { text: "휴식";     icon: "☕"; selected: root.purpose === "휴식";     onClicked: root.purpose = "휴식" }
            }

            Text { text: "관심 분야"; font.family: S.fontFamily; font.pixelSize: 20; font.bold: true; color: S.text }
            Flow {
                width: parent.width; spacing: 10
                Chip { text: "과학"; icon: "🔬"; selected: root.interest === "과학"; onClicked: root.interest = "과학" }
                Chip { text: "예술"; icon: "🎨"; selected: root.interest === "예술"; onClicked: root.interest = "예술" }
                Chip { text: "문학"; icon: "📖"; selected: root.interest === "문학"; onClicked: root.interest = "문학" }
                Chip { text: "취미"; icon: "🎯"; selected: root.interest === "취미"; onClicked: root.interest = "취미" }
            }
        }

        Column {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 28 }
            spacing: 8
            RobotFace { anchors.horizontalCenter: parent.horizontalCenter; width: 120; height: 120 }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "리비의 추천이에요!"
                font.family: S.fontFamily; font.pixelSize: 16; color: S.textSoft
            }
        }
    }

    // 오른쪽: 추천 결과
    ListView {
        id: recList
        anchors { left: leftPanel.right; right: parent.right; top: header.bottom; bottom: parent.bottom
                  leftMargin: 24; rightMargin: 28; topMargin: 12; bottomMargin: 24 }
        clip: true
        spacing: 14
        model: root.recs

        delegate: Rectangle {
            width: recList.width - 8
            height: 132
            radius: 18
            color: S.surface
            border.color: S.border; border.width: 1.5

            Rectangle {
                id: stripe
                width: 8; radius: 4
                anchors { left: parent.left; top: parent.top; bottom: parent.bottom; margins: 14 }
                color: S.categoryColor(modelData.category)
            }
            Column {
                anchors { left: stripe.right; leftMargin: 18; right: parent.right; rightMargin: 18; verticalCenter: parent.verticalCenter }
                spacing: 6
                Row {
                    spacing: 10
                    Text { text: modelData.title; font.family: S.fontFamily; font.pixelSize: 21; font.bold: true; color: S.text }
                    StatusPill {
                        pillColor: modelData.available ? S.success : S.textMuted
                        text: modelData.available ? "대여 가능" : "대여 중"
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Text { text: modelData.author + " · " + modelData.call; font.family: S.fontFamily; font.pixelSize: 15; color: S.textMuted }
                Text {
                    text: "💡 " + (modelData.reason ? modelData.reason : "")
                    font.family: S.fontFamily; font.pixelSize: 15; color: S.textSoft
                    wrapMode: Text.WordWrap; width: parent.width
                }
            }
        }
    }
}
