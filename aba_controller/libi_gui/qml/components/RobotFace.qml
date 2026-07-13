import QtQuick 2.15
import "../Style.js" as S

// Libi 로봇 얼굴 — pinky_pro 표정 GIF 재생 (검은 OLED 배경 + 마젠타 이목구비).
// 공개 인터페이스: width / height / emotion (기존과 동일).
Item {
    id: face
    width: 360
    height: 360

    // 감정: 전역 controller 와 연동 (부모가 덮어쓸 수 있음)
    property string emotion: (typeof controller !== "undefined") ? controller.emotion : "basic"

    // 살짝 둥실 (idle bob)
    property real idleBob: 0.0
    SequentialAnimation on idleBob {
        running: true; loops: Animation.Infinite
        NumberAnimation { to: -6; duration: 1600; easing.type: Easing.InOutSine }
        NumberAnimation { to:  0; duration: 1600; easing.type: Easing.InOutSine }
    }

    // 보유 GIF 로 매핑 (없는 감정은 근사치/기본값)
    function gifFor(e) {
        switch (e) {
        case "happy": case "hello": case "fun": case "interest":
        case "sad": case "angry": case "basic": case "bored":
            return e;
        case "thinking": return "interest";
        case "sleep":    return "bored";
        default:         return "basic";
        }
    }

    // 둥근 OLED 디바이스 (검정 — GIF 배경과 동일)
    Rectangle {
        id: device
        anchors.fill: parent
        radius: width * 0.16
        color: "#000000"
        clip: true
        transform: Translate { y: face.idleBob }

        AnimatedImage {
            anchors.fill: parent
            anchors.margins: parent.width * 0.05
            source: "../assets/emotion/" + face.gifFor(face.emotion) + ".gif"
            fillMode: Image.PreserveAspectFit
            playing: true
            cache: true
            smooth: true
        }
    }
}
