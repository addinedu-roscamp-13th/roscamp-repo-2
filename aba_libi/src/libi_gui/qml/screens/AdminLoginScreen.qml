import QtQuick 2.15
import "../Style.js" as S
import "../components"

Item {
    id: root
    property string pin: ""

    function press(d) { if (pin.length < 4) pin += d }
    function enter() {
        if (controller.login(pin)) {
            pin = "";
            controller.setMode("adminControl");
        } else {
            pin = "";
            shake.restart();
        }
    }

    ScreenHeader {
        anchors { left: parent.left; top: parent.top; margins: 28 }
        width: parent.width - 56
        emoji: "🔧"; title: "관리자 로그인"
        onBack: controller.setMode("home")
    }

    Card {
        id: panel
        anchors.centerIn: parent
        width: 460; height: 600

        Column {
            anchors.centerIn: parent
            spacing: 26

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "🔒 PIN 입력"
                font.family: S.fontFamily; font.pixelSize: 24; font.bold: true; color: S.text
            }

            // PIN 표시
            Row {
                id: dots
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 18
                Repeater {
                    model: 4
                    delegate: Rectangle {
                        width: 22; height: 22; radius: 11
                        color: index < root.pin.length ? S.primary : "transparent"
                        border.color: S.borderStrong; border.width: 2
                    }
                }
                SequentialAnimation {
                    id: shake
                    NumberAnimation { target: dots; property: "anchors.horizontalCenterOffset"; to: -14; duration: 60 }
                    NumberAnimation { target: dots; property: "anchors.horizontalCenterOffset"; to: 14;  duration: 60 }
                    NumberAnimation { target: dots; property: "anchors.horizontalCenterOffset"; to: 0;   duration: 60 }
                }
            }

            // 넘패드
            Grid {
                anchors.horizontalCenter: parent.horizontalCenter
                columns: 3
                spacing: 16
                Repeater {
                    model: ["1","2","3","4","5","6","7","8","9","C","0","⏎"]
                    delegate: Rectangle {
                        width: 96; height: 80; radius: 18
                        color: modelData === "⏎" ? S.primary : (modelData === "C" ? S.bgAlt : S.surface)
                        border.color: S.borderStrong; border.width: 1.5
                        Text {
                            anchors.centerIn: parent
                            text: modelData
                            font.family: S.fontFamily; font.pixelSize: 30; font.bold: true
                            color: modelData === "⏎" ? S.onPrimary : S.text
                        }
                        scale: km.pressed ? 0.94 : 1.0
                        Behavior on scale { NumberAnimation { duration: 70 } }
                        MouseArea {
                            id: km
                            anchors.fill: parent
                            onClicked: {
                                if (modelData === "C") root.pin = "";
                                else if (modelData === "⏎") root.enter();
                                else root.press(modelData);
                            }
                        }
                    }
                }
            }

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "데모 PIN: 1234"
                font.family: S.fontFamily; font.pixelSize: 14; color: S.textMuted
            }
        }
    }
}
