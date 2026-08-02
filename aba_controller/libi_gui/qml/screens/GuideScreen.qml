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
    // [2026-08-02] 이 세션에서 자동 출발을 이미 쐈나. **한 번만** 쏜다 —
    // `pose` 는 프레임마다 갱신되므로 가드가 없으면 매 프레임 startGuide 가 나간다.
    property bool autoStarted: false

    // 뒷캠에 **등록된 그 사람**이 잡혔나.
    //
    // ⚠️ **구버전 AI 서버에서도 동작해야 한다.** `isOwner` 는 2026-08-02 에 추가한
    //    필드라, 서버를 재기동하지 않으면 **안 온다**. `isOwner === true` 만 보면
    //    그때 자동 출발이 **조용히 영영 안 걸린다** — 실제로 그렇게 됐다(사용자 실측:
    //    뒤에 서 있는데 앞의 패널을 누르라는 꼴이 됐다).
    //
    //    그래서 없으면 예전 신호로 떨어진다: 검출이 있으면 `isPredicted` 가 bool 이고
    //    없으면 null 이다(perception_server.py `_pose_payload`). 지금 파이프라인은
    //    owner 만 내보내므로(pipeline.py 의 유일한 `Detection(...)` 이 `is_owner=True`
    //    고정) 이 폴백이 현재 코드에서는 정확하다. 서버를 올리면 `isOwner` 가 이긴다.
    readonly property bool poseHasOwnerField:
        perception.pose !== undefined && perception.pose !== null
        && perception.pose.isOwner !== undefined
    readonly property bool someoneDetected:
        perception.pose !== undefined && perception.pose !== null
        && (poseHasOwnerField
            ? perception.pose.isOwner === true
            : (perception.pose.isPredicted !== undefined
               && perception.pose.isPredicted !== null))
    readonly property bool ownerOnBackCam:
        controller.currentCamera === "back" && someoneDetected

    // 안내가 끝나거나 취소·실패하면 가드를 되감는다.
    //
    // ⚠️ 이게 없으면 **한 번 쓰고 죽는다.** `autoStarted` 는 목적지를 새로 고를 때만
    //    풀렸는데(아래 `onFacilityClicked`), 같은 목적지로 다시 시도하는 것이 가장 흔한
    //    경우다 — 취소했다가 다시, 실패했다가 다시. 그때 자동 출발이 조용히 안 걸리고
    //    이용자는 또 로봇 뒤에서 앞의 패널을 눌러야 한다.
    // ── 등록이 **진짜로 됐는지** 확인해 준다 ────────────────────────────────
    //
    // 학습 3초 동안 AI 서버가 `isOwner=true` 를 한 번이라도 보내면 등록이 성립한 것이다
    // (`matcher.register()` 가 그 사람을 owner 로 잡았다는 뜻). 못 보면
    // `confirmGuideRegistration` 의 타이머가 등록을 되돌린다 — 근거는 그쪽 ⚠️ 주석.
    //
    // ⚠️ "검출이 있나" 로는 부족하다. 지나가던 사람도 bbox 는 쳐진다. owner 여야 한다.
    Connections {
        target: perception
        function onPoseChanged() {
            if (controller.guideRegPhase !== "learning") return
            if (perception.pose && perception.pose.isOwner === true)
                controller.reportRegistrationOwnerSeen()
        }
    }

    Connections {
        target: controller
        function onGuidePhaseChanged() {
            var p = controller.guidePhase
            if (p === "idle" || p === "failed" || p === "cancelled" || p === "completed")
                root.autoStarted = false
        }
    }

    // ── 뒷캠에 보이면 **버튼 없이 바로 출발한다** ────────────────────────────
    //
    // 출발 조건은 이미 로봇 쪽이 쥐고 있다 — `GuideExec._never_confirmed()` 가 뒷캠
    // 확인 전에는 goal 을 안 낸다. 그러면 화면의 "안내 시작" 버튼은 **사람이 뒤로 온
    // 뒤에 한 번 더 눌러야 하는 군더더기**다. 이용자는 로봇 뒤에 서 있어서 패널을
    // 볼 수도 없다(사용자 지적 2026-08-02).
    //
    // ⚠️ 버튼은 남겨 둔다. 뒷캠이 안 잡히는 상황(조명·각도)에서 사람이 강제로 시작할
    //    수단이 없어지면 안 되기 때문이다. 자동은 **빠른 길**이지 유일한 길이 아니다.
    onOwnerOnBackCamChanged: {
        if (!ownerOnBackCam) return
        if (autoStarted || root.requesting || root.guiding) return
        if (controller.guideRegPhase !== "ready" || root.picked === "") return
        autoStarted = true
        controller.startGuide(root.picked)
    }

    ScreenHeader {
        id: header
        anchors { left: parent.left; top: parent.top; margins: 28 }
        width: parent.width - 56
        emoji: "🧭"; title: "길잡이"
        // 화면을 나가면 감시 세션도 닫는다 — 안 닫으면 아무도 안 보는데 카메라가 켜져 있다.
        // 순서가 있다: 진행 중이면 안내를 끊고 → 감시 세션을 닫고 → **단계를 되감고**
        // → 화면을 나간다. 되감기가 화면 전환보다 **먼저**여야 한다 — 뒤에 두면
        // 다음에 길잡이로 들어올 때 아직 `completed` 라 목적지 화면이 숨는다.
        onBack: { if (root.guiding) controller.cancelGuide();
                  controller.cancelGuideRegistration();
                  controller.resetGuidePhase();
                  controller.setMode("home") }
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
            root.autoStarted = false                 // 새로 고르면 자동 출발도 새로
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
            // 등록(학습)이 끝난 순간의 안내 문구. `regCol` 은 ready 가 되면 사라지므로
            // 그 자리에 이걸 대신 띄운다 — 안 그러면 화면이 갑자기 버튼만 남아서,
            // 이용자는 자기가 등록됐는지, 이제 무엇을 해야 하는지 알 수 없다.
            //
            // ⚠️ 출발 **전** 문구다. 출발 뒤 "안내 시작 — 따라오세요!" 는 아래 (2) 안내 중
            //    카드에 따로 있다. 둘을 한 곳으로 합치지 말 것 — 등록 직후와 주행 중은
            //    이용자가 해야 할 일이 다르다(여기서는 로봇 뒤로 서는 것).
            Column {
                id: readyCol
                visible: root.picked !== "" && controller.guideRegPhase === "ready"
                anchors { left: parent.left; right: parent.right; bottom: startBtn.top
                          bottomMargin: 14 }
                spacing: 6

                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "등록 완료 — 제 뒤로 와 주세요!"
                    font.family: S.fontFamily; font.pixelSize: 22; font.bold: true
                    color: S.primary
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    text: root.ownerOnBackCam
                          ? "확인했습니다 — 출발합니다"
                          : controller.currentCamera !== "back"
                            ? "뒤 카메라로 바꾸는 중…"
                            : "뒤 카메라에 보이면 바로 출발합니다."
                    font.family: S.fontFamily; font.pixelSize: 15; color: S.textMuted
                    wrapMode: Text.WordWrap
                }
                // 뒷캠 미리보기. 등록이 끝나면 `RobotController` 가 카메라를 뒤로 바꾼다.
                // 이게 없으면 "뒤로 오라" 는 글자뿐이라, 이용자는 자기가 프레임에
                // 들어왔는지 알 수 없다 — 출발 조건이 정확히 그것인데도.
                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width; height: 150; radius: 12; color: S.bgAlt
                    clip: true
                    Image {
                        anchors.fill: parent
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        source: "image://perception/frame?" + perception.frameCounter
                    }
                    Rectangle {
                        anchors { left: parent.left; top: parent.top; margins: 8 }
                        width: readyCamLabel.width + 16; height: readyCamLabel.height + 8
                        radius: 6; color: "#000000"; opacity: 0.55
                        Text {
                            id: readyCamLabel; anchors.centerIn: parent
                            text: controller.currentCamera === "back" ? "뒤 카메라"
                                : controller.currentCamera === "front" ? "앞 카메라" : "카메라 꺼짐"
                            font.family: S.fontFamily; font.pixelSize: 14; color: "#ffffff"
                        }
                    }
                }
            }

            Column {
                id: regCol
                visible: root.picked !== "" && controller.guideRegPhase !== "ready"
                anchors { left: parent.left; right: parent.right; bottom: startBtn.top
                          bottomMargin: 14 }
                spacing: 10

                // [2026-08-02] "자세를 재고 있어요" 를 지웠다 — 길잡이는 자세를 안 쓴다
                // (`RobotController::confirmGuideRegistration` 주석). 쓰지도 않을 값을
                // 재느라 이용자를 4초 세워 두고 있었다.
                Text {
                    // 학습 중에는 **움직이지 말라**고 말해야 한다. 이 3초가 출발 조건을
                    // 만드는 시간이라(RobotController::confirmGuideRegistration 주석),
                    // 여기서 사람이 돌아서거나 나가면 앞모습을 못 배운다.
                    text: controller.guideRegPhase === "learning"
                          ? "얼굴을 익히고 있어요 — 3초만 그대로 서 주세요"
                          : "아래 등록 버튼을 눌러 주세요"
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

                // [2026-08-02] **등록 버튼을 따로 둔다.**
                //
                //   등록은 지금까지 "영상을 탭한다" 뿐이었다. 화면 어디에도 그렇게 쓰여
                //   있지 않고, 아래 큰 버튼은 회색으로 "안내받으실 분을 등록해 주세요"
                //   라고만 해서 **누를 곳이 없어 보인다**(사용자 지적 2026-08-02).
                //   영상 탭은 그대로 두고(빠르다) 명시적인 버튼을 같이 둔다.
                BigButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    enabledLook: controller.guideRegPhase === "registering"
                    text: controller.guideRegPhase === "learning" ? "익히는 중…" : "등록"
                    color: controller.guideRegPhase === "registering" ? S.primary : S.bgAlt
                    textColor: controller.guideRegPhase === "registering" ? S.onPrimary : S.textMuted
                    implicitWidth: parent.width; implicitHeight: 64
                    onClicked: if (controller.guideRegPhase === "registering") {
                                   perception.registerTarget()
                                   controller.confirmGuideRegistration()
                               }
                }
            }

            BigButton {
                id: startBtn
                anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom }
                // [2026-08-02] **등록이 끝나면 이 버튼은 사라진다.**
                //
                //   출발 조건은 로봇이 쥔다 — 뒷캠에 요청자가 잡히면 자동으로 간다
                //   (`onOwnerOnBackCamChanged`). 그때 버튼을 남겨 두면 이용자는 로봇
                //   **뒤**에 서 있는데 앞의 패널을 누르라는 꼴이 된다.
                //
                //   ⚠️ `visible:false` 라도 QML 은 기하를 유지하므로, 이 항목에
                //      `bottom` 을 걸어 둔 `regCol`·`readyCol` 의 배치는 안 깨진다.
                //
                //   ⚠️ 대신 **자동 출발이 안 걸리면 시작할 수단이 없다.** 뒷캠에
                //      안 잡히는 상황(조명·각도·미등록)에서는 뒤로가기로 나갔다가
                //      다시 등록해야 한다.
                visible: controller.guideRegPhase !== "ready"
                enabledLook: controller.guideRegPhase === "ready" && !root.requesting
                text: root.requesting ? "관제 승인 기다리는 중…"
                    : root.picked === "" ? "목적지를 골라주세요"
                    : controller.guideRegPhase !== "ready" ? "안내받으실 분을 등록해 주세요"
                    : root.picked + " (으)로 지금 출발"
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
                //
                // [2026-08-02] **앞캠에서 찾았을 때는 다른 말을 한다.**
                //   회복 트리는 요청자를 놓치면 반대 캠(앞캠)도 훑는다. 거기서 사람이
                //   잡혔다는 건 "그 사람이 로봇 **앞**에 와 있다" 는 뜻이다. 그때
                //   "요청자를 찾는 중" 은 거짓이고, 이용자는 뭘 해야 할지 모른다.
                //
                //   ⚠️ 로봇은 이 경우 **회전하지 않는다**(recovery_bt.py:181 — 목적지
                //      방향과 사람 방향이 겹치면 회전·재계획·재포착이 무한 진동한다).
                //      움직이는 쪽은 사람이다. 그래서 화면이 그걸 부탁한다.
                //
                //   "앞캠에 사람이 있나" 는 AI 서버가 프레임마다 보내는 POSE JSON 으로
                //   판정한다 — 검출이 없으면 그 필드들이 통째로 null 이다
                //   (perception_server.py `_pose_line`). 새 신호를 만들지 않는다.
                Text {
                    id: guideStatus
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    // ⚠️ **등록된 그 사람인지**를 본다. "검출이 있나" 로는 부족하다 —
                    //    지나가던 사람을 보고 "뒤로 오세요" 를 띄우면 화면이 거짓말한다.
                    //    `isOwner` 는 AI 서버가 POSE JSON 에 싣는다
                    //    (perception_server.py `_pose_payload`). 검출이 없으면 null 이다.
                    readonly property bool someoneSeen:
                        perception.pose !== undefined && perception.pose !== null
                        && perception.pose.isOwner === true
                    readonly property bool aheadOfRobot:
                        controller.guidePhase === "requesterLost"
                        && controller.currentCamera === "front" && someoneSeen
                    text: aheadOfRobot ? "또 같이 뒤로 이동해주세요!"
                        : controller.guidePhase === "requesterLost" ? "요청자를 찾는 중입니다"
                        : "안내 시작 — 따라오세요!"
                    font.family: S.fontFamily; font.pixelSize: 18
                    font.bold: guideStatus.aheadOfRobot
                    color: guideStatus.aheadOfRobot ? S.primary
                         : controller.guidePhase === "requesterLost" ? S.warning : S.textSoft
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

        // [2026-08-02] **도착 완료 카드를 없앴다.** (사용자 지시: "그냥 안 뜨게 해줘")
        //
        //   안내가 끝나면 `finishGuideIfLeftWorking` 이 곧바로 홈으로 되돌리므로
        //   (RobotController.cpp) 이 카드는 원래 한 프레임 스칠 뿐이었다.
        //
        //   ⚠️ 그런데 실제로는 **길잡이를 두 번 못 쓰게 막고 있었다.** 완료 뒤
        //      이용자가 길잡이로 다시 들어오면 `guidePhase` 가 아직 `"completed"` 라
        //      이 카드가 뜨고, 목적지 고르는 화면은 위 `:108` 조건
        //      (`guidePhase !== "completed"`)에 걸려 숨는다 — 「처음으로」를 눌러도
        //      다시 들어오면 같은 화면이었다(실측 2026-08-02).
        //
        //   ⚠️ 카드를 지우는 것만으로는 부족하다. 들어올 때 단계를 되감아야 목적지
        //      화면이 다시 보인다 — `RobotController::setMode` 의 guide 갈래 참고.
    }
}
