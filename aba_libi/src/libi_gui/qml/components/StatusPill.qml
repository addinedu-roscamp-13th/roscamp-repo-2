import QtQuick 2.15
import "../Style.js" as S

Rectangle {
    id: pill
    property string text: ""
    property color pillColor: S.mint
    property bool showDot: true

    implicitHeight: 36
    implicitWidth: row.implicitWidth + 24
    radius: height/2
    color: Qt.rgba(pillColor.r, pillColor.g, pillColor.b, 0.20)

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 8
        Rectangle {
            visible: pill.showDot
            width: 10; height: 10; radius: 5
            color: pill.pillColor
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            text: pill.text
            color: S.text
            font.family: S.fontFamily
            font.pixelSize: 16
            font.bold: true
            anchors.verticalCenter: parent.verticalCenter
        }
    }
}
