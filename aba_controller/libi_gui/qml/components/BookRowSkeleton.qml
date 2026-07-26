import QtQuick 2.15
import "../Style.js" as S

// 도서 목록을 불러오는 동안 보여줄 자리표시자 (aba_service/frontend BookRowSkeleton 과 같은 역할).
Rectangle {
    width: parent ? parent.width : 300
    height: 100
    radius: 16
    color: S.surface
    border.color: S.border; border.width: 1.5

    Rectangle {
        anchors { left: parent.left; top: parent.top; bottom: parent.bottom; margins: 16 }
        width: 8; radius: 4
        color: S.bgAlt
    }
    Column {
        anchors { left: parent.left; leftMargin: 40; right: parent.right; rightMargin: 20; verticalCenter: parent.verticalCenter }
        spacing: 10
        Rectangle { width: parent.width * 0.55; height: 18; radius: 6; color: S.bgAlt }
        Rectangle { width: parent.width * 0.35; height: 14; radius: 6; color: S.bgAlt }
    }
    SequentialAnimation on opacity {
        loops: Animation.Infinite
        NumberAnimation { to: 0.45; duration: 550; easing.type: Easing.InOutQuad }
        NumberAnimation { to: 0.9; duration: 550; easing.type: Easing.InOutQuad }
    }
}
