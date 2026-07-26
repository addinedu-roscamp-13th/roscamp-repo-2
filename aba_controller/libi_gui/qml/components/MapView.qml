import QtQuick 2.15
import "../Style.js" as S

// 도서관 내부 지도 — 회원 앱(aba_service LibraryMap.tsx)과 같은 안내판 스타일 배치.
// 정점 이름·위치는 `RobotController::facilities()`(= 실제 waypoint.yaml)에서 그대로 온다 —
// 여기서 좌표나 이름을 새로 지어내지 않는다.
Rectangle {
    id: map
    property string highlight: ""
    property real robotX: 0.50
    property real robotY: 0.90
    /** 알약을 탭하면 그 구역 이름을 올려보낸다 — 화면마다 원하는 동작(안내 시작/상세 표시)에 연결한다. */
    signal facilityClicked(string name)

    radius: S.radCard
    color: S.bg
    border.color: "#1c3a5e"
    border.width: 3
    clip: true

    // 안내판 느낌의 점 그리드 배경.
    Canvas {
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            ctx.fillStyle = "rgba(28,58,94,0.12)";
            var step = 22;
            for (var gx = step / 2; gx < width; gx += step) {
                for (var gy = step / 2; gy < height; gy += step) {
                    ctx.beginPath();
                    ctx.arc(gx, gy, 1.2, 0, Math.PI * 2);
                    ctx.fill();
                }
            }
        }
    }

    // 구역 알약 — 아이콘 + 이름, 탭하면 facilityClicked 를 올린다.
    Repeater {
        model: controller.facilities()
        delegate: Item {
            id: marker
            property bool isTarget: map.highlight !== "" && modelData.name === map.highlight
            x: modelData.x * map.width - width / 2
            y: modelData.y * map.height - height / 2
            width: pill.width
            height: pill.height
            z: isTarget ? 10 : 1

            Rectangle {
                id: pill
                width: row.implicitWidth + 20
                height: row.implicitHeight + 12
                radius: height / 2
                color: marker.isTarget ? S.primary : "#FFFFFFE0"
                border.color: marker.isTarget ? S.primary : S.borderStrong
                border.width: marker.isTarget ? 2 : 1.5
                scale: marker.isTarget ? 1.12 : 1.0
                Behavior on scale { NumberAnimation { duration: 150 } }
                Behavior on color { ColorAnimation { duration: 150 } }

                Row {
                    id: row
                    anchors.centerIn: parent
                    spacing: 5
                    Text { text: modelData.icon; font.pixelSize: 16 }
                    Text {
                        // pillName 은 지도 알약 전용 짧은 표기다(없으면 name) — 탭했을 때
                        // 올려보내는 값은 항상 전체 이름(name)이라 동작엔 영향 없다.
                        text: modelData.pillName !== undefined ? modelData.pillName : modelData.name
                        font.family: S.fontFamily; font.pixelSize: 13; font.bold: true
                        color: marker.isTarget ? S.onPrimary : S.textSoft
                    }
                }
            }

            // 안내 목적지로 잡히면 은은하게 퍼지는 링 — 지도 위에서도 "지금 여기로 간다"가 보이게.
            Rectangle {
                visible: marker.isTarget
                anchors.centerIn: pill
                width: pill.width; height: pill.height; radius: pill.radius
                color: "transparent"; border.color: S.primary; border.width: 2
                SequentialAnimation on scale {
                    running: marker.isTarget; loops: Animation.Infinite
                    NumberAnimation { from: 1.0; to: 1.6; duration: 1100; easing.type: Easing.OutQuad }
                    PropertyAction { value: 1.0 }
                }
                SequentialAnimation on opacity {
                    running: marker.isTarget; loops: Animation.Infinite
                    NumberAnimation { from: 0.7; to: 0.0; duration: 1100 }
                    PropertyAction { value: 0.7 }
                }
            }

            MouseArea {
                anchors.fill: pill
                onClicked: map.facilityClicked(modelData.name)
            }
        }
    }

    // 로봇(Libi) 마커.
    Item {
        x: map.robotX * map.width - width / 2
        y: map.robotY * map.height - height / 2
        width: 38; height: 38
        z: 20
        Behavior on x { NumberAnimation { duration: 400 } }
        Behavior on y { NumberAnimation { duration: 400 } }
        Rectangle {
            anchors.fill: parent; radius: width / 2
            color: S.primary; border.color: "white"; border.width: 3
        }
        Text { anchors.centerIn: parent; text: "🤖"; font.pixelSize: 18 }
    }
}
