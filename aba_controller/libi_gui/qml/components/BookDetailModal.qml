import QtQuick 2.15
import "../Style.js" as S

// 도서 상세 — 회원 앱 `BookDetailSheet.tsx` 와 같은 내용(표지·설명·태그·상태 문장)을
// 터치패널 크기로 옮긴 모달. 목록이 이미 들고 있는 도서 객체를 그대로 받으므로 단건
// 조회를 다시 하지 않는다.
Item {
    id: root
    /** null 이 아니면 열린다. RobotController::bookFromJson() 이 만든 맵. */
    property var book: null
    signal closed()
    // "여기로 안내". 목적지 이름은 화면이 정한다 — 책의 `location` 은 waypoint 이름
    // ("과학-인문학서가")이라 지도 구역 표시 이름("과학 서가")과 다르다.
    signal guideRequested()

    anchors.fill: parent
    visible: book !== null
    z: 50

    function statusColor(status) {
        if (status === "배치중") return S.success;
        if (status === "대출 중") return S.textMuted;
        return S.danger;
    }

    // 바깥을 누르면 닫힌다. 이 MouseArea 가 아래 목록으로 가는 탭도 함께 막는다.
    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.45)
        MouseArea { anchors.fill: parent; onClicked: root.closed() }
    }

    Rectangle {
        width: Math.min(parent.width - 80, 760)
        height: Math.min(parent.height - 60, 560)
        anchors.centerIn: parent
        radius: S.radCard
        color: S.surface
        border.color: S.border; border.width: 1.5
        MouseArea { anchors.fill: parent }   // 카드 안쪽 탭은 닫기로 새지 않게

        Item {
            anchors.fill: parent
            anchors.margins: 28

            // 머리 — 표지/제목/저자/위치
            Row {
                id: head
                anchors { left: parent.left; right: parent.right; top: parent.top }
                spacing: 18
                Rectangle {
                    width: 92; height: 92; radius: 20
                    color: S.categoryColor(root.book ? root.book.category : "")
                    Text { anchors.centerIn: parent; text: root.book ? root.book.cover : ""; font.pixelSize: 44 }
                }
                Column {
                    width: parent.width - 92 - 18
                    spacing: 6
                    Text {
                        width: parent.width
                        text: root.book ? root.book.title : ""
                        font.family: S.fontFamily; font.pixelSize: 26; font.bold: true; color: S.text
                        wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                    }
                    Text {
                        text: root.book ? root.book.author : ""
                        font.family: S.fontFamily; font.pixelSize: 17; color: S.textSoft
                    }
                    Row {
                        spacing: 10
                        Text {
                            text: root.book ? ("📍 " + root.book.location + " · " + root.book.shelf) : ""
                            font.family: S.fontFamily; font.pixelSize: 15; color: S.primary
                        }
                        StatusPill {
                            pillColor: root.statusColor(root.book ? root.book.status : "")
                            text: root.book ? root.book.status : ""
                        }
                    }
                }
            }

            // 진열 상태 한 줄 — "지금 서가에 있나"를 가장 먼저 답한다.
            Rectangle {
                id: statusBox
                anchors { left: parent.left; right: parent.right; top: head.bottom; topMargin: 18 }
                height: 48
                radius: 14
                color: S.bgAlt
                Text {
                    anchors { left: parent.left; leftMargin: 16; verticalCenter: parent.verticalCenter }
                    text: root.book ? root.book.statusSentence : ""
                    font.family: S.fontFamily; font.pixelSize: 16; font.bold: true
                    color: root.statusColor(root.book ? root.book.status : "")
                }
            }

            // 줄거리 + 태그
            Flickable {
                anchors { left: parent.left; right: parent.right; top: statusBox.bottom; bottom: buttons.top
                          topMargin: 16; bottomMargin: 16 }
                contentHeight: body.height
                clip: true
                Column {
                    id: body
                    width: parent.width
                    spacing: 14
                    Text {
                        width: parent.width
                        visible: text !== ""
                        text: root.book ? root.book.summary : ""
                        font.family: S.fontFamily; font.pixelSize: 17; color: S.text
                        lineHeight: 1.35
                        wrapMode: Text.WordWrap
                    }
                    // 이 패널은 **정보 조회 전용**이다 — 예약 API 는 회원 로그인이 필요해서
                    // (get_current_member) 익명 터치패널에서는 부를 수 없다. 못 하는 일을
                    // 버튼으로 두지 않고, 어디서 할 수 있는지만 알려준다.
                    Text {
                        width: parent.width
                        visible: root.book && root.book.status !== "배치중"
                        text: root.book && root.book.status === "대출 중"
                              ? "예약은 회원 앱에서 할 수 있어요."
                              : "이 책은 사서에게 문의해 주세요."
                        font.family: S.fontFamily; font.pixelSize: 15; color: S.textMuted
                        wrapMode: Text.WordWrap
                    }
                    Column {
                        spacing: 8
                        visible: root.book && root.book.tags.length > 0
                        Text {
                            text: "이런 분께"
                            font.family: S.fontFamily; font.pixelSize: 14; font.bold: true; color: S.primary
                        }
                        Flow {
                            width: body.width
                            spacing: 8
                            Repeater {
                                model: root.book ? root.book.tags : []
                                delegate: Rectangle {
                                    height: 34; radius: 17; width: tagText.width + 26
                                    color: S.primaryDim
                                    Text {
                                        id: tagText
                                        anchors.centerIn: parent; text: modelData
                                        font.family: S.fontFamily; font.pixelSize: 14; font.bold: true; color: S.text
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Row {
                id: buttons
                anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom }
                spacing: 14
                // 대출 중·대출 불가인 책은 서가에 없다 — 없는 책 앞으로 데려가지 않는다.
                BigButton {
                    visible: root.book && root.book.status === "배치중"
                    text: "🧭 여기로 안내"; color: S.primary
                    implicitWidth: 220; implicitHeight: 64
                    onClicked: root.guideRequested()
                }
                BigButton {
                    text: "닫기"; color: S.bgAlt; textColor: S.textSoft
                    implicitWidth: 160; implicitHeight: 64
                    onClicked: root.closed()
                }
            }
        }
    }
}
