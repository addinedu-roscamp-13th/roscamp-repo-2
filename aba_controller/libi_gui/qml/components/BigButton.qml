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

        // 내용은 버튼 밖으로 못 나간다.
        //   - 세로: 아이콘(48px 고정)+간격+글자 26px 는 높이 64 짜리 버튼에서 이미 넘쳤다.
        //     아이콘·간격을 높이에 종속시켜, 낮은 버튼에서는 아이콘이 먼저 줄고 사라진다.
        //   - 가로: 글자에 폭 제약이 없으면 fontSizeMode 가 맞출 기준 자체가 없다.
        //     padding 을 뺀 폭을 주고, 그 안에서 줄여 맞추다 하한(minimumPixelSize)에
        //     닿으면 그때만 말줄임한다.
        Column {
            id: content
            anchors.centerIn: parent
            width: parent.width - 28
            readonly property real iconSize: Math.max(0, Math.min(48, btn.height - 46))
            spacing: iconSize > 0 ? Math.min(10, iconSize / 4) : 0

            Text {
                visible: btn.icon !== "" && content.iconSize > 0
                height: visible ? content.iconSize : 0
                anchors.horizontalCenter: parent.horizontalCenter
                text: btn.icon
                font.pixelSize: content.iconSize
                verticalAlignment: Text.AlignVCenter
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: btn.text
                color: btn.textColor
                font.family: S.fontFamily
                font.pixelSize: 26
                font.bold: true
                fontSizeMode: Text.HorizontalFit
                minimumPixelSize: 15
                elide: Text.ElideRight
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
