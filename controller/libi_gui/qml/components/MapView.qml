import QtQuick 2.15
import "../Style.js" as S

// 도서관 내부 약식 지도: 서가 블록 + 시설 마커 + 로봇(Libi) 위치 + 강조 목적지
Rectangle {
    id: map
    property string highlight: ""
    property real robotX: 0.50
    property real robotY: 0.90

    radius: S.radCard
    color: "#F3EFE6"
    border.color: S.border
    border.width: 1.5
    clip: true

    // 서가 블록
    Repeater {
        model: [
            {x:0.16, y:0.45, w:0.20, h:0.12, fill:"#D7ECF5", line:S.sky,      label:"과학"},
            {x:0.44, y:0.42, w:0.20, h:0.12, fill:"#E7DDF5", line:S.lavender, label:"예술"},
            {x:0.72, y:0.45, w:0.18, h:0.12, fill:"#DBF3E8", line:S.mint,     label:"문학"}
        ]
        delegate: Rectangle {
            x: modelData.x*map.width
            y: modelData.y*map.height
            width: modelData.w*map.width
            height: modelData.h*map.height
            radius: 8
            color: modelData.fill
            border.color: modelData.line
            border.width: 1.5
            Text {
                anchors.centerIn: parent
                text: modelData.label
                font.family: S.fontFamily; font.pixelSize: 14; font.bold: true
                color: S.textSoft
            }
        }
    }

    // 시설 마커
    Repeater {
        model: controller.facilities()
        delegate: Item {
            id: marker
            property bool isTarget: map.highlight !== "" && modelData.name === map.highlight
            x: modelData.x*map.width - width/2
            y: modelData.y*map.height - height/2
            width: 34; height: 34

            Rectangle {
                anchors.centerIn: parent
                width: marker.isTarget ? 40 : 28
                height: width; radius: width/2
                color: marker.isTarget ? S.primary : S.surface
                border.color: marker.isTarget ? S.primary : S.borderStrong
                border.width: 2
                Behavior on width { NumberAnimation { duration: 150 } }
            }
            Text { anchors.centerIn: parent; text: modelData.icon; font.pixelSize: marker.isTarget ? 20 : 15 }

            Text {
                visible: marker.isTarget
                text: modelData.name
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.bottom; anchors.topMargin: 4
                font.family: S.fontFamily; font.pixelSize: 13; font.bold: true; color: S.primary
            }

            Rectangle {
                visible: marker.isTarget
                anchors.centerIn: parent
                width: 40; height: 40; radius: 20
                color: "transparent"; border.color: S.primary; border.width: 2
                SequentialAnimation on scale {
                    running: marker.isTarget; loops: Animation.Infinite
                    NumberAnimation { from: 1.0; to: 2.4; duration: 1100; easing.type: Easing.OutQuad }
                    PropertyAction { value: 1.0 }
                }
                SequentialAnimation on opacity {
                    running: marker.isTarget; loops: Animation.Infinite
                    NumberAnimation { from: 0.7; to: 0.0; duration: 1100 }
                    PropertyAction { value: 0.7 }
                }
            }
        }
    }

    // 로봇(Libi) 마커
    Item {
        x: map.robotX*map.width - width/2
        y: map.robotY*map.height - height/2
        width: 38; height: 38
        Behavior on x { NumberAnimation { duration: 400 } }
        Behavior on y { NumberAnimation { duration: 400 } }
        Rectangle {
            anchors.fill: parent; radius: width/2
            color: S.primary; border.color: "white"; border.width: 3
        }
        Text { anchors.centerIn: parent; text: "🤖"; font.pixelSize: 18 }
    }
}
