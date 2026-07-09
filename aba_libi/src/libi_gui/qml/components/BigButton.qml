import QtQuick 2.15
import "../Style.js" as S

Item {
    id: btn
    property string text: ""
    property string icon: ""
    property color color: S.primary
    property color textColor: S.onPrimary
    property color borderColor: "transparent"
    property real borderWidth: 0
    property bool enabledLook: true
    signal clicked()

    implicitWidth: 220
    implicitHeight: 150
    opacity: enabledLook ? 1.0 : 0.45

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: S.radButton
        color: btn.color
        border.color: btn.borderColor
        border.width: btn.borderWidth
        scale: ma.pressed ? 0.96 : 1.0
        Behavior on scale { NumberAnimation { duration: 90; easing.type: Easing.OutQuad } }

        Column {
            anchors.centerIn: parent
            spacing: 10
            Text {
                visible: btn.icon !== ""
                anchors.horizontalCenter: parent.horizontalCenter
                text: btn.icon
                font.pixelSize: 48
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: btn.text
                color: btn.textColor
                font.family: S.fontFamily
                font.pixelSize: 26
                font.bold: true
            }
        }

        MouseArea {
            id: ma
            anchors.fill: parent
            enabled: btn.enabledLook
            onClicked: btn.clicked()
        }
    }
}
