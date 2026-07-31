import QtQuick 2.15
import "../Style.js" as S
import "../components"

// 관리자 추종 화면 — AI 서버(perception_server)의 영상을 그대로 띄우고 등록/해제를 누른다.
// 그림과 글자의 담당이 다르다:
//   · bbox·OWNER 라벨·스켈레톤·가이드선 — 화면 좌표에 붙으므로 **서버가 JPEG 에 굽는다**
//   · 자세·비율·STATE·cmd_vel        — 좌표와 무관하므로 **POSE 로 값만 받아 여기서 그린다**
// 예전엔 글자도 구워 보냈는데, 영상이 축소되면 같이 뭉개져 안 읽혔다.
Item {
    id: root

    // ── 자세·비율 표시 ──────────────────────────────────────────────────
    // 서버가 보낸 POSE 값을 헤더에 띄울 글자로 만든다. 값이 없는 항목은 키가
    // 빠지거나 null 이라 매번 확인하고 쓴다 — 기본값을 채우면 "아직 없음"과
    // "0" 이 구분되지 않는다.
    readonly property var poseInfo: perception.pose
    //: 기준을 재는 동안에만 존재한다. 있으면 자세 대신 카운트다운을 띄운다.
    readonly property var calibInfo: (poseInfo && poseInfo.calibrating)
                                     ? poseInfo.calibrating : null

    readonly property string postureLabel: {
        if (calibInfo) return "기준 측정 중";
        if (!poseInfo || !poseInfo.posture) return "";
        var ko = { "Standing": "직립", "Side": "측면", "Lying": "누움",
                   "Calibrating": "기준 측정 중", "Unknown": "판정 불가" };
        var s = ko[poseInfo.posture] || poseInfo.posture;
        // 자세와 주행은 별개다 — 직립이어도 라이다나 게이트가 막을 수 있다.
        return poseInfo.motionOk === false ? s + " · 주행 차단" : s;
    }

    readonly property color postureColor: {
        if (calibInfo) return S.textMuted;
        var p = poseInfo ? poseInfo.posture : "";
        if (p === "Standing") return S.success;
        if (p === "Side")     return S.warning;
        if (p === "Lying")    return S.danger;
        return S.textMuted;
    }

    readonly property string ratioLabel: {
        if (calibInfo)
            return calibInfo.remainingSec.toFixed(1) + "초 남음  ("
                   + calibInfo.got + "/" + calibInfo.need + ")";
        if (!poseInfo || poseInfo.ratio === undefined || poseInfo.ratio === null)
            return "";
        // 지금 값 / 등록할 때 잰 기준 / 측면 판정선. 셋을 같이 보여야 "얼마나
        // 모자란지"를 사람이 암산하지 않는다.
        var t = "비율 " + poseInfo.ratio.toFixed(2);
        if (poseInfo.refRatio) t += "  / 기준 " + poseInfo.refRatio.toFixed(2);
        if (poseInfo.sideTrip) t += "  · 측면 ≥ " + poseInfo.sideTrip.toFixed(2);
        // 어느 축으로 쟀는지는 기본값(torso)이 아닐 때만 붙인다. 늘 붙이면 글자만
        // 길어지고, 정작 어긋남을 의심해야 할 때 눈에 안 띈다.
        if (poseInfo.axis && poseInfo.axis !== "torso") t += "  [" + poseInfo.axis + "]";
        return t;
    }

    // ── 주행 상태 (영상 위 왼쪽 위) ─────────────────────────────────────
    readonly property string driveState: (poseInfo && poseInfo.state) ? poseInfo.state : ""

    readonly property color driveStateColor: {
        var s = root.driveState;
        if (s === "FOLLOWING") return S.success;
        if (s === "PEEK")      return S.sky;
        if (s === "SEARCHING") return S.warning;
        if (s === "IDLE")      return S.textMuted;
        return S.danger;
    }

    readonly property string cmdVelLabel: {
        if (!poseInfo || poseInfo.linearX === undefined || poseInfo.linearX === null)
            return "";
        var a = (poseInfo.angularZ === undefined || poseInfo.angularZ === null)
                ? 0.0 : poseInfo.angularZ;
        return "lin.x " + poseInfo.linearX.toFixed(2) + "   ang.z " + a.toFixed(2);
    }

    // 화면이 살아있는 동안만 스트림을 받는다 — 추종을 안 하는데 영상을 계속 끌어오면
    // AI 서버와 네트워크만 쓴다.
    Component.onCompleted: perception.start()
    Component.onDestruction: perception.stop()

    Column {
        anchors.fill: parent
        anchors.margins: S.pad
        spacing: 14

        ScreenHeader {
            width: parent.width
            title: "관리자 추종"
            emoji: "🧑‍🤝‍🧑"

            // 제목 오른쪽 빈 자리에 자세·비율을 띄운다. 값은 AI 서버가 POSE 로
            // 보내고(perception_server._pose_payload) 여기서 글자로 만든다 —
            // 영상에 구워 보내면 축소될 때 뭉개져서 안 읽힌다.
            subtitle: root.postureLabel
            subtitleColor: root.postureColor
            detail: root.ratioLabel
            // 뒤로가기는 **추종도 같이 끊는다.**
            //
            // 예전엔 화면만 넘어가고 추종은 계속 돌았다. 영상이 사라지니 끝난 것처럼
            // 보이는데 로봇은 계속 사람을 따라온다 — 조작하는 사람에게 설명이 안 되고,
            // FMS 승인 기록도 남아 다음 추종이 거부된다.
            //
            // stopAdminFollow() 는 추종 중이 아니면 아무 일도 안 한다. 그래서 아래
            // setMode 는 그대로 둔다 — 추종이 이미 끝난 뒤 눌러도 화면은 넘어가야 한다.
            // (추종 중이었다면 following 이 꺼지며 Main.qml 의 Connections 가 같은
            //  화면으로 한 번 더 보내지만, 같은 값이라 아무 일도 안 일어난다.)
            onBack: {
                controller.stopAdminFollow();
                controller.setMode("adminControl");
            }
        }

        // 영상. 서버가 프레임에 구워 보내는 것은 **좌표에 붙는 그림뿐**이다 —
        // 3등분 가이드선·bbox·OWNER 라벨·스켈레톤. 상태 글자(STATE·자세·비율·
        // cmd_vel)는 POSE 로 값이 와서 여기 QML 이 그린다.
        Rectangle {
            width: parent.width
            height: parent.height - (lidarRow.visible ? 316 : 250)
            radius: S.radCard
            color: "#1E1A18"
            clip: true

            Image {
                id: view
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                cache: false
                asynchronous: false
                // frameCounter 가 바뀔 때마다 URL 이 달라져야 새 프레임을 읽는다.
                source: perception.frameCounter > 0
                        ? "image://perception/frame?" + perception.frameCounter
                        : ""
                visible: perception.frameCounter > 0
            }

            // 첫 프레임이 오기 전 / 끊겼을 때
            Column {
                anchors.centerIn: parent
                spacing: 12
                visible: !view.visible
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: perception.connected ? "📷" : "🔌"
                    font.pixelSize: 64
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: perception.connected ? "영상 대기 중…" : "AI 서버에 연결 중…"
                    color: "white"; font.pixelSize: 22; font.family: S.fontFamily
                }
            }

            // 주행 상태 — 예전에 서버가 JPEG 왼쪽 위에 굽던 STATE/cmd_vel 이다.
            // 자리는 그대로 두고 **그리는 쪽만** 옮겼다. 영상이 축소돼도 안 뭉개지고,
            // 값이 없으면(등록 전 등) 통째로 사라진다.
            Rectangle {
                anchors.top: parent.top; anchors.left: parent.left; anchors.margins: 12
                width: driveCol.implicitWidth + 20
                height: driveCol.implicitHeight + 12
                radius: S.radButton
                color: "#99000000"          // 영상 위라 반투명 배경이 없으면 안 읽힌다
                visible: root.driveState !== ""

                Column {
                    id: driveCol
                    anchors.centerIn: parent
                    spacing: 2
                    Text {
                        text: root.driveState
                        color: root.driveStateColor
                        font.pixelSize: 18; font.bold: true; font.family: S.fontFamily
                    }
                    Text {
                        text: root.cmdVelLabel
                        visible: text !== ""
                        color: "white"; font.pixelSize: 14; font.family: S.fontFamily
                    }
                }
            }

            // 연결 상태 배지
            Rectangle {
                anchors.top: parent.top; anchors.right: parent.right; anchors.margins: 12
                width: badge.implicitWidth + 24; height: 34; radius: S.radPill
                color: perception.connected ? S.success : S.danger
                Text {
                    id: badge
                    anchors.centerIn: parent
                    text: perception.connected ? "연결됨" : "끊김"
                    color: "white"; font.pixelSize: 15; font.bold: true; font.family: S.fontFamily
                }
            }
        }

        // 장애물 거리 — 영상의 3등분 방향 구역(좌/중앙/우)과 같은 순서로 놓는다.
        // 서버가 --lidar-ros 로 떴을 때만 값이 온다.
        Row {
            id: lidarRow
            width: parent.width
            height: 56
            spacing: 10
            visible: Object.keys(perception.lidar).length > 0
            property real cellW: (width - spacing * 2) / 3

            // cm -> 표시 문자열. -1(측정값 없음)은 숫자로 보여주면 안 된다.
            function fmt(cm) { return (cm === undefined || cm < 0) ? "—" : (cm / 100).toFixed(2) + " m" }
            // 가까울수록 붉게. 임계값은 표시용이며 회피 판단은 서버가 한다.
            function tone(cm) {
                if (cm === undefined || cm < 0) return S.textMuted;
                if (cm < 50) return S.danger;
                if (cm < 100) return S.warning;
                return S.text;
            }

            Repeater {
                model: [
                    { label: "◀ 좌", value: perception.lidar.frontLeft },
                    { label: "▲ 정면", value: perception.lidar.front },
                    { label: "우 ▶", value: perception.lidar.frontRight }
                ]
                Rectangle {
                    width: lidarRow.cellW; height: lidarRow.height
                    radius: S.radButton
                    color: S.surface; border.color: S.border; border.width: 1.5
                    Column {
                        anchors.centerIn: parent
                        spacing: 2
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: modelData.label
                            color: S.textMuted; font.pixelSize: 13; font.family: S.fontFamily
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: lidarRow.fmt(modelData.value)
                            color: lidarRow.tone(modelData.value)
                            font.pixelSize: 20; font.bold: true; font.family: S.fontFamily
                        }
                    }
                }
            }
        }

        // 상태 문구 (연결 오류 사유가 여기 뜬다)
        Text {
            width: parent.width
            text: perception.statusText
            color: S.textMuted; font.pixelSize: 15; font.family: S.fontFamily
            elide: Text.ElideRight
        }

        // 등록 / 해제
        Row {
            width: parent.width
            spacing: 14
            property real cellW: (width - spacing) / 2

            BigButton {
                implicitWidth: parent.cellW; implicitHeight: 84
                text: "🎯  등록"; color: S.success; textColor: "white"
                enabledLook: perception.connected
                onClicked: perception.registerTarget()
            }
            BigButton {
                implicitWidth: parent.cellW; implicitHeight: 84
                text: "↩  해제"; color: S.bgAlt; textColor: S.text
                enabledLook: perception.connected
                onClicked: perception.resetTarget()
            }
        }
    }
}
