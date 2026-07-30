import QtQuick 2.15
import "../Style.js" as S

// 도서관 내부 지도 — 사서·관제팀이 그린 안내판 도면(artemap.png)을 그대로 띄운다.
// 구역 이름·아이콘은 **그림 안에 이미 그려져 있다**. 화면이 알약을 다시 그리지 않는 이유는
// 같은 것을 두 곳에서 그리면 반드시 어긋나기 때문이다(FollowScreen 의 bbox 와 같은 원칙).
// 화면이 얹는 것은 세 가지뿐 — 탭 영역, 목적지 강조, 로봇 위치.
//
// 좌표는 `RobotController::facilities()` 의 bx,by,bw,bh(그림 안 0..1 비율)에서 온다.
// 그림을 갈아끼우면 그 값을 다시 재야 한다 — 여기서 좌표를 지어내지 않는다.
Item {
    id: map
    property string highlight: ""
    /** 구역을 탭하면 그 이름을 올려보낸다 — 화면마다 원하는 동작(안내 시작/상세 표시)에 연결한다. */
    signal facilityClicked(string name)

    /** 도면 자체의 가로세로 비 — 담는 쪽이 높이를 이 비율로 잡으면 여백 없이 꽉 찬다. */
    readonly property real mapAspect: 1340 / 751

    Image {
        id: img
        anchors.fill: parent
        source: "qrc:/qml/assets/artemap.png"
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
    }

    // 그림이 실제로 그려진 사각형. PreserveAspectFit 이라 담는 상자와 다를 수 있어,
    // 탭 영역을 map 이 아니라 **이 안**에 놓아야 그림 위 알약과 어긋나지 않는다.
    Item {
        id: plane
        x: (map.width - img.paintedWidth) / 2
        y: (map.height - img.paintedHeight) / 2
        width: img.paintedWidth
        height: img.paintedHeight

        Repeater {
            model: controller.facilities()
            delegate: Item {
                id: zone
                property bool isTarget: map.highlight !== "" && modelData.name === map.highlight
                x: modelData.bx * plane.width
                y: modelData.by * plane.height
                width: modelData.bw * plane.width
                height: modelData.bh * plane.height
                z: isTarget ? 10 : 1

                // 눌린 동안만 옅게 — 그림 위 알약이 반응한다는 걸 보이게.
                Rectangle {
                    anchors.fill: parent
                    radius: Math.min(width, height) / 2
                    color: S.primary
                    opacity: zoneMa.pressed ? 0.28 : 0
                    Behavior on opacity { NumberAnimation { duration: 100 } }
                }

                // 목적지 강조 — 알약을 덮지 않고 테두리만 두른다(이름이 계속 읽혀야 한다).
                Rectangle {
                    id: ring
                    anchors.fill: parent
                    anchors.margins: -5
                    radius: Math.min(width, height) / 2
                    color: Qt.rgba(1, 0.56, 0.67, 0.18)
                    border.color: S.primary
                    border.width: 3
                    visible: zone.isTarget
                }

                Rectangle {
                    anchors.centerIn: ring
                    width: ring.width; height: ring.height; radius: ring.radius
                    color: "transparent"; border.color: S.primary; border.width: 2
                    visible: zone.isTarget
                    SequentialAnimation on scale {
                        running: zone.isTarget; loops: Animation.Infinite
                        NumberAnimation { from: 1.0; to: 1.25; duration: 1100; easing.type: Easing.OutQuad }
                        PropertyAction { value: 1.0 }
                    }
                    SequentialAnimation on opacity {
                        running: zone.isTarget; loops: Animation.Infinite
                        NumberAnimation { from: 0.7; to: 0.0; duration: 1100 }
                        PropertyAction { value: 0.7 }
                    }
                }

                // 탭 영역은 그려진 상자보다 **크다.** 세로로 긴 서가 알약은 실측 폭이
                // 28~34px 뿐이라(지도 폭 592~636px 기준) 손가락으로 정확히 누르기 어렵다.
                // 최소 44px 을 보장하되 넓히는 건 안 보이는 판정 영역뿐이라, 강조 테두리와
                // 그림은 그대로 둔다. 서로 겹칠 만큼 좁은 구역은 이 지도에 없다(가장 가까운
                // 예술서가/문학서가가 세로로 붙어 있어 가로만 넓어지는 이 방식이 안전하다).
                MouseArea {
                    id: zoneMa
                    anchors.centerIn: parent
                    width: Math.max(parent.width, 44)
                    height: Math.max(parent.height, 44)
                    onClicked: map.facilityClicked(modelData.name)
                }
            }
        }

        // 마커가 없을 때 왜 없는지 말한다. 아무것도 안 그리면 이용자는 "로봇이 어디 있지"를
        // 알 수 없고, 화면이 고장 난 것으로도 보인다.
        Rectangle {
            visible: !controller.poseValid
            anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom; bottomMargin: 10 }
            width: noPoseText.width + 24; height: 30; radius: 15
            color: "#FFFFFFE0"; border.color: S.borderStrong; border.width: 1
            Text {
                id: noPoseText
                anchors.centerIn: parent
                text: "로봇 위치 확인 중"
                font.family: S.fontFamily; font.pixelSize: 13; color: S.textSoft
            }
        }

        // 로봇(Libi) 마커 — `/amcl_pose` 실측 위치. 위치를 모르면(수신 없음·끊김) 안 그린다.
        Item {
            id: robot
            visible: controller.poseValid
            x: controller.mapX * plane.width - width / 2
            y: controller.mapY * plane.height - height / 2
            width: 38; height: 38
            z: 20
            Behavior on x { NumberAnimation { duration: 400 } }
            Behavior on y { NumberAnimation { duration: 400 } }

            // 진행 방향 화살표. 위를 향해 그리고 rotation 으로 돌린다 —
            // 각도 계산은 domain.h 의 mapYawToImageRotationDeg (부호 빼먹으면 좌우가 뒤집힌다).
            Canvas {
                width: 54; height: 54
                anchors.centerIn: parent
                rotation: controller.mapHeadingDeg
                Behavior on rotation { RotationAnimation { duration: 300; direction: RotationAnimation.Shortest } }
                onPaint: {
                    var ctx = getContext("2d");
                    ctx.clearRect(0, 0, width, height);
                    ctx.fillStyle = S.primary;
                    ctx.strokeStyle = "white";
                    ctx.lineWidth = 2;
                    ctx.beginPath();
                    ctx.moveTo(width / 2, 0);            // 꼭짓점이 정면
                    ctx.lineTo(width / 2 - 9, 15);
                    ctx.lineTo(width / 2 + 9, 15);
                    ctx.closePath();
                    ctx.fill();
                    ctx.stroke();
                }
            }

            Rectangle {
                anchors.fill: parent; radius: width / 2
                color: S.primary; border.color: "white"; border.width: 3
            }
            Text { anchors.centerIn: parent; text: "🤖"; font.pixelSize: 18 }
        }
    }
}
