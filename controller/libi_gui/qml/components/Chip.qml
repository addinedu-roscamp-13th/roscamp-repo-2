import QtQuick 2.15
import "../Style.js" as S

Rectangle {
    id: chip
    property string text: ""
    property string icon: ""
    property bool selected: false
    signal clicked()

    implicitHeight: 52
    implicitWidth: label.implicitWidth + (iconText.visible ? iconText.implicitWidth + 10 : 0) + 44
    radius: height/2
    color: selected ? S.primary : S.surface
    border.color: selected ? S.primary : S.borderStrong
    border.width: 1.5
    Behavior on color { ColorAnimation { duration: 120 } }

    Row {
        anchors.centerIn: parent
        spacing: 10
        Text {
            id: iconText
            visible: chip.icon !== ""
            text: chip.icon
            font.pixelSize: 22
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            id: label
            text: chip.text
            color: chip.selected ? S.onPrimary : S.textSoft
            font.family: S.fontFamily
            font.pixelSize: 19
            font.bold: chip.selected
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    scale: pressArea.pressed ? 0.96 : 1.0
    Behavior on scale { NumberAnimation { duration: 80 } }
    MouseArea { id: pressArea; anchors.fill: parent; onClicked: chip.clicked() }
}
