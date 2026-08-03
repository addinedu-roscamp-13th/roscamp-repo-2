import QtQuick 2.15
import "../Style.js" as S

Item {
    id: h
    property string title: ""
    property string emoji: ""
    // 제목 오른쪽 빈 자리에 띄우는 현재 상태. 둘 다 비면 아무것도 안 그린다.
    //   subtitle — 굵은 한 줄 (예: "Side · 주행 차단")
    //   detail   — 그 아래 작은 글씨 (예: "비율 3.42 / 기준 2.10")
    // 예전에는 이런 값을 AI 서버가 JPEG 에 구워 보냈는데, 영상이 축소되면 같이
    // 뭉개져 안 읽혔다. 값으로 받아 여기서 그린다(PerceptionClient 머리말).
    property string subtitle: ""
    property string detail: ""
    property color subtitleColor: S.text
    height: 60
    signal back()

    Rectangle {
        id: backBtn
        width: 64; height: 54; radius: 16
        color: S.surface; border.color: S.border; border.width: 1.5
        anchors.verticalCenter: parent.verticalCenter
        Text { anchors.centerIn: parent; text: "←"; font.pixelSize: 28; color: S.text }
        scale: bma.pressed ? 0.94 : 1.0
        Behavior on scale { NumberAnimation { duration: 80 } }
        MouseArea { id: bma; anchors.fill: parent; onClicked: h.back() }
    }
    Text {
        anchors.left: backBtn.right; anchors.leftMargin: 16
        // 상태가 길어져도 제목을 밀지 않는다 — 넘치면 제목 쪽이 말줄임된다.
        anchors.right: info.left; anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        text: (h.emoji ? h.emoji + "  " : "") + h.title
        elide: Text.ElideRight
        font.family: S.fontFamily; font.pixelSize: 30; font.bold: true; color: S.text
    }

    Column {
        id: info
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        visible: h.subtitle !== "" || h.detail !== ""

        Text {
            anchors.right: parent.right
            text: h.subtitle
            visible: text !== ""
            font.family: S.fontFamily; font.pixelSize: 20; font.bold: true
            color: h.subtitleColor
        }
        Text {
            anchors.right: parent.right
            text: h.detail
            visible: text !== ""
            font.family: S.fontFamily; font.pixelSize: 15
            color: S.textMuted
        }
    }
}
