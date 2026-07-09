import QtQuick 2.15
import "../Style.js" as S

Item {
    id: h
    property string title: ""
    property string emoji: ""
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
        anchors.verticalCenter: parent.verticalCenter
        text: (h.emoji ? h.emoji + "  " : "") + h.title
        font.family: S.fontFamily; font.pixelSize: 30; font.bold: true; color: S.text
    }
}
