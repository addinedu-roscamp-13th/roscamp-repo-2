import QtQuick 2.15
import QtQuick.Controls 2.15   // ScrollBar — 목적지 목록이 넘칠 때만 보인다
import "../Style.js" as S
import "../components"

Item {
    id: root
    property bool guiding: controller.guidePhase === "guiding" || controller.guidePhase === "requesterLost"
    property bool requesting: controller.guidePhase === "requesting"
    // 고르는 것과 출발하는 것을 나눈다 — 터치패널에서 한 번 잘못 누르면 곧바로 로봇이
    // 출발하던 것을 막는다. 지도든 칩이든 누르면 여기 담기고, 출발은 아래 버튼 하나뿐이다.
    property string picked: ""

    ScreenHeader {
        id: header
        anchors { left: parent.left; top: parent.top; margins: 28 }
        width: parent.width - 56
        emoji: "🧭"; title: "길잡이"
        // 화면을 나가면 감시 세션도 닫는다 — 안 닫으면 아무도 안 보는데 카메라가 켜져 있다.
        onBack: { if (root.guiding) controller.cancelGuide();
                  controller.cancelGuideRegistration(); controller.setMode("home") }
    }

    // 왼쪽: 지도
    MapView {
        id: map
        anchors { left: parent.left; top: header.bottom; margins: 28; topMargin: 12 }
        width: parent.width * 0.52
        // 도면 비율 그대로 — 세로로 늘리면 위아래가 빈 띠로 남는다(PreserveAspectFit).
        height: width / mapAspect
        highlight: root.guiding ? controller.guideDestination : root.picked
        // 탭은 **선택만** 한다. 출발은 아래 버튼.
        onFacilityClicked: (name) => {
            if (root.guiding) return
            root.picked = name
            controller.startGuideRegistration()      // 등록 화면 = 카메라 켜기
            perception.start()                       // 추종 화면과 같은 영상 통로를 쓴다
        }
    }

    // 오른쪽 패널
    Item {
        id: rightPanel
        anchors { left: map.right; right: parent.right; top: header.bottom; bottom: parent.bottom
                  leftMargin: 24; rightMargin: 28; topMargin: 12; bottomMargin: 28 }

        // (1) 목적지 선택 — 고르기만 하고, 출발은 맨 아래 버튼 하나로 모은다.
        Item {
            visible: !root.guiding && controller.guidePhase !== "completed"
            anchors.fill: parent

            // 목적지 목록은 **남는 높이 안에서만** 그린다.
            //
            // 예전에는 이 Column 이 top 에만 걸려 아래로 자랐고, 아래쪽 등록 영역(regCol)은
            // 버튼에서 위로 자랐다. 둘은 형제라 서로를 모른다 — 시설이 많으면 그대로
            // 겹쳐서, 마지막 줄 칩이 "자세를 재고 있어요" 문구와 카메라 상자에 가려
            // **누를 수 없게** 됐다(실측 2026-07-28).
            //
            // 목록을 숨기지 않고 Flickable 로 가둔다. 등록 중에도 목적지를 바꿀 수 있어야
            // 하기 때문이다 — 숨기면 다시 고르려고 화면을 나갔다 들어와야 한다.
            Flickable {
                id: pickArea
                anchors { left: parent.left; right: parent.right; top: parent.top
                          bottom: regCol.visible ? regCol.top : startBtn.top
                          bottomMargin: 14 }
                contentHeight: pickCol.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                // 넘칠 때만 스크롤바가 보인다 — 안 넘치면 화면이 예전과 똑같다.
                ScrollBar.vertical: ScrollBar { policy: pickArea.contentHeight > pickArea.height
                                                        ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }

                Column {
                    id: pickCol
                    width: pickArea.width
                    spacing: 16
                    Text {
                        text: "어디로 안내해드릴까요?"
                        font.family: S.fontFamily; font.pixelSize: 24; font.bold: true; color: S.text
                    }
                    // 지도가 그림뿐이라 "누를 수 있다"가 안 보인다 — 검색 화면에는 같은 안내가
                    // 이미 있는데 여기만 없었다.
                    Text {
                        text: "지도의 구역을 눌러도 고를 수 있어요"
                        font.family: S.fontFamily; font.pixelSize: 15; color: S.textMuted
                    }
                    Flow {
                        width: parent.width
                        spacing: 12
                        Repeater {
                            model: controller.facilities()
                            delegate: Chip {
                                icon: modelData.icon
                                text: modelData.name
                                selected: root.picked === modelData.name
                                onClicked: {
                                    root.picked = modelData.name
                                    controller.startGuideRegistration()
                                    perception.start()
                                }
                            }
                        }
                }
            }
            }

            // 목적지를 고른 뒤 **이용자 등록**을 거친다. 로봇이 누구를 안내하는지
            // 모르면 뒷캠으로 확인할 대상이 없어, 안내 중 아무나 따라와도 알 수 없다.
            //
            // 등록은 **앞캠**이다 — 이용자는 지금 이 패널을 누르고 있으므로 로봇 앞에
            // 있다. 뒷캠으로 바뀌는 건 출발해서 로봇이 앞장서기 시작할 때다.
            Column {
                id: regCol
                visible: root.picked !== "" && controller.guideRegPhase !== "ready"
                anchors { left: parent.left; right: parent.right; bottom: startBtn.top
                          bottomMargin: 14 }
                spacing: 10

                Text {
                    text: controller.guideRegPhase === "calibrating"
                          ? "자세를 재고 있어요 — 잠시 그대로 서 주세요"
                          : "안내받으실 분을 화면에서 눌러 주세요"
                    font.family: S.fontFamily; font.pixelSize: 18; font.bold: true; color: S.text
                }

                Rectangle {
                    width: parent.width; height: 180; radius: 12; color: S.bgAlt
                    clip: true
                    Image {
                        anchors.fill: parent
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        source: controller.guideRegPhase === "idle"
                                ? "" : "image://perception/frame?" + perception.frameCounter
                    }
                    // 어느 캠을 보고 있는지 항상 보여준다. 전환하면 시점이 통째로
                    // 뒤집혀 보이는데, 라벨이 없으면 보는 사람이 무엇이 잘못됐는지 모른다.
                    Rectangle {
                        anchors { left: parent.left; top: parent.top; margins: 8 }
                        width: camLabel.width + 16; height: camLabel.height + 8
                        radius: 6; color: "#000000"; opacity: 0.55
                        Text {
                            id: camLabel; anchors.centerIn: parent
                            text: controller.currentCamera === "front" ? "앞 카메라"
                                : controller.currentCamera === "back"  ? "뒤 카메라" : "카메라 꺼짐"
                            font.family: S.fontFamily; font.pixelSize: 14; color: "#ffffff"
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        enabled: controller.guideRegPhase === "registering"
                        onClicked: { perception.registerTarget(); controller.confirmGuideRegistration() }
                    }
                }
            }

            BigButton {
                id: startBtn
                anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom }
                enabledLook: controller.guideRegPhase === "ready" && !root.requesting
                text: root.requesting ? "관제 승인 기다리는 중…"
                    : root.picked === "" ? "목적지를 골라주세요"
                    : controller.guideRegPhase === "calibrating" ? "자세 측정 중…"
                    : controller.guideRegPhase !== "ready" ? "안내받으실 분을 등록해 주세요"
                    : root.picked + " (으)로 안내 시작"
                color: controller.guideRegPhase === "ready" ? S.primary : S.bgAlt
                textColor: controller.guideRegPhase === "ready" ? S.onPrimary : S.textMuted
                implicitWidth: parent.width; implicitHeight: 88
                onClicked: if (controller.guideRegPhase === "ready" && !root.requesting)
                               controller.startGuide(root.picked)
            }
        }

        // (2) 안내 중
        Card {
            visible: root.guiding
            anchors.fill: parent
            Column {
                anchors.centerIn: parent
                width: parent.width - 48
                spacing: 18
                // 안내 중에는 로봇 얼굴 대신 **뒷캠 미니뷰**를 보여준다.
                // 사람을 놓쳐 로봇이 멈췄을 때 "왜 멈췄지"가 화면에 바로 드러난다.
                Item {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 260; height: 150
                    RobotFace {
                        anchors.fill: parent
                        visible: controller.currentCamera === "none"
                    }
                    Rectangle {
                        anchors.fill: parent; radius: 12; color: S.bgAlt; clip: true
                        visible: controller.currentCamera !== "none"
                        Image {
                            anchors.fill: parent
                            fillMode: Image.PreserveAspectFit
                            cache: false
                            source: "image://perception/frame?" + perception.frameCounter
                        }
                        Rectangle {
                            anchors { left: parent.left; top: parent.top; margins: 6 }
                            width: guideCamLabel.width + 14; height: guideCamLabel.height + 6
                            radius: 6; color: "#000000"; opacity: 0.55
                            Text {
                                id: guideCamLabel; anchors.centerIn: parent
                                text: controller.currentCamera === "front" ? "앞 카메라" : "뒤 카메라"
                                font.family: S.fontFamily; font.pixelSize: 13; color: "#ffffff"
                            }
                        }
                    }
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    text: controller.guideDestination + " (으)로 안내 중"
                    font.family: S.fontFamily; font.pixelSize: 24; font.bold: true; color: S.text
                    wrapMode: Text.WordWrap
                }
                // 상태 문구 (시나리오 정확 문구)
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: controller.guidePhase === "requesterLost" ? "요청자를 찾는 중입니다" : "안내 시작 — 따라오세요!"
                    font.family: S.fontFamily; font.pixelSize: 18
                    color: controller.guidePhase === "requesterLost" ? S.warning : S.textSoft
                }
                // 남은 거리 — 실제 위치와 목적지 좌표로 잰 값이다. 위치를 모르면(-1)
                // 숫자를 지어내지 않고 그렇게 말한다.
                Column {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 2
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: controller.distanceToGoal >= 0
                              ? controller.distanceToGoal.toFixed(1) + " m" : "— m"
                        font.family: S.fontFamily; font.pixelSize: 56; font.bold: true; color: S.primary
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: controller.distanceToGoal >= 0 ? "남은 거리" : "위치 확인 중"
                        font.family: S.fontFamily; font.pixelSize: 15; color: S.textMuted
                    }
                }
                BigButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "안내 취소"; color: S.bgAlt; textColor: S.textSoft
                    implicitWidth: 200; implicitHeight: 76
                    onClicked: controller.cancelGuide()
                }
            }
        }

        // (3) 도착 완료
        Card {
            visible: controller.guidePhase === "completed"
            anchors.fill: parent
            Column {
                anchors.centerIn: parent
                spacing: 18
                Text { anchors.horizontalCenter: parent.horizontalCenter; text: "🎉"; font.pixelSize: 72 }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "목적지 도착 / 안내 종료"
                    font.family: S.fontFamily; font.pixelSize: 26; font.bold: true; color: S.text
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "이용해 주셔서 감사합니다!"
                    font.family: S.fontFamily; font.pixelSize: 18; color: S.textSoft
                }
                BigButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "처음으로"; color: S.primary
                    implicitWidth: 220; implicitHeight: 80
                    onClicked: controller.setMode("home")
                }
            }
        }
    }
}
