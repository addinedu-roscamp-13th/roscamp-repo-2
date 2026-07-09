import QtQuick 2.15
import QtQuick.Controls 2.15
import "../Style.js" as S
import "../components"

Item {
    id: root
    property string tab: "book"          // book | facility
    property string category: "전체"
    property bool onlyAvail: false
    property string query: ""
    property string selectedHighlight: ""
    property string selectedDetail: ""

    property var results: tab === "book"
        ? controller.searchBooks(query, category, onlyAvail)
        : controller.searchFacilities(query)

    ScreenHeader {
        id: header
        anchors { left: parent.left; top: parent.top; margins: 28 }
        width: parent.width - 56
        emoji: "🔎"; title: "검색"
        onBack: controller.setMode("home")
    }

    // 필터
    Column {
        id: filters
        anchors { left: parent.left; right: parent.right; top: header.bottom
                  leftMargin: 28; rightMargin: 28; topMargin: 12 }
        spacing: 12

        Row {
            spacing: 12
            Chip { text: "도서"; icon: "📚"; selected: root.tab === "book";     onClicked: { root.tab = "book"; root.selectedHighlight = ""; root.selectedDetail = "" } }
            Chip { text: "시설"; icon: "🏛"; selected: root.tab === "facility"; onClicked: { root.tab = "facility"; root.selectedHighlight = ""; root.selectedDetail = "" } }
        }

        TextField {
            width: parent.width
            placeholderText: root.tab === "book" ? "도서명 · 저자 검색" : "시설 이름 검색"
            font.family: S.fontFamily; font.pixelSize: 18
            color: S.text
            leftPadding: 18; rightPadding: 18; topPadding: 12; bottomPadding: 12
            onTextChanged: root.query = text
            background: Rectangle { radius: 14; color: S.surface; border.color: S.borderStrong; border.width: 1.5 }
        }

        Row {
            visible: root.tab === "book"
            spacing: 10
            Chip { text: "전체"; selected: root.category === "전체"; onClicked: root.category = "전체" }
            Chip { text: "과학"; selected: root.category === "과학"; onClicked: root.category = "과학" }
            Chip { text: "예술"; selected: root.category === "예술"; onClicked: root.category = "예술" }
            Chip { text: "문학"; selected: root.category === "문학"; onClicked: root.category = "문학" }
            Chip { text: "대여 가능만"; selected: root.onlyAvail; onClicked: root.onlyAvail = !root.onlyAvail }
        }
    }

    // 결과 + 지도
    Item {
        anchors { left: parent.left; right: parent.right; top: filters.bottom; bottom: parent.bottom
                  leftMargin: 28; rightMargin: 28; topMargin: 16; bottomMargin: 24 }

        // 결과 리스트
        ListView {
            id: listView
            anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
            width: parent.width * 0.54
            clip: true
            spacing: 12
            model: root.results

            delegate: Rectangle {
                width: listView.width - 8
                height: 100
                radius: 16
                color: S.surface
                border.color: root.tab === "book" && root.selectedDetail === modelData.title ? S.primary : S.border
                border.width: root.tab === "book" && root.selectedDetail === modelData.title ? 2 : 1.5

                Rectangle {
                    id: stripe
                    visible: root.tab === "book"
                    width: 8; radius: 4
                    anchors { left: parent.left; top: parent.top; bottom: parent.bottom; margins: 12 }
                    color: root.tab === "book" ? S.categoryColor(modelData.category) : S.primary
                }

                Column {
                    anchors { left: stripe.right; leftMargin: 16; verticalCenter: parent.verticalCenter; right: rightCol.left; rightMargin: 10 }
                    spacing: 4
                    Text {
                        text: root.tab === "book" ? modelData.title : (modelData.icon + "  " + modelData.name)
                        font.family: S.fontFamily; font.pixelSize: 20; font.bold: true; color: S.text
                        elide: Text.ElideRight; width: parent.width
                    }
                    Text {
                        visible: root.tab === "book"
                        text: root.tab === "book" ? (modelData.author + " · " + modelData.call) : ""
                        font.family: S.fontFamily; font.pixelSize: 15; color: S.textMuted
                    }
                    Text {
                        text: root.tab === "book" ? ("📍 " + modelData.location) : "📍 지도에서 위치 보기"
                        font.family: S.fontFamily; font.pixelSize: 14; color: S.textSoft
                    }
                }

                Column {
                    id: rightCol
                    anchors { right: parent.right; rightMargin: 16; verticalCenter: parent.verticalCenter }
                    spacing: 6
                    StatusPill {
                        visible: root.tab === "book"
                        pillColor: modelData.available ? S.success : S.textMuted
                        text: modelData.available ? "대여 가능" : "대여 중"
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        if (root.tab === "book") {
                            root.selectedDetail = modelData.title;
                            root.selectedHighlight = modelData.category + " 섹션";
                        } else {
                            root.selectedDetail = modelData.name;
                            root.selectedHighlight = modelData.name;
                        }
                    }
                }
            }
        }

        // 지도 + 상세
        MapView {
            id: map
            anchors { left: listView.right; right: parent.right; top: parent.top; leftMargin: 20 }
            height: parent.height * 0.62
            highlight: root.selectedHighlight
        }

        Card {
            anchors { left: listView.right; right: parent.right; top: map.bottom; bottom: parent.bottom; leftMargin: 20; topMargin: 16 }
            Column {
                anchors.centerIn: parent
                width: parent.width - 40
                spacing: 8
                visible: root.selectedDetail !== ""
                Text {
                    text: root.selectedHighlight
                    font.family: S.fontFamily; font.pixelSize: 20; font.bold: true; color: S.primary
                    anchors.horizontalCenter: parent.horizontalCenter
                }
                Text {
                    text: "선택: " + root.selectedDetail
                    font.family: S.fontFamily; font.pixelSize: 16; color: S.textSoft
                    anchors.horizontalCenter: parent.horizontalCenter
                    wrapMode: Text.WordWrap; width: parent.width; horizontalAlignment: Text.AlignHCenter
                }
                BigButton {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "여기로 안내"; icon: "🧭"; color: S.primary
                    implicitWidth: 200; implicitHeight: 64
                    onClicked: controller.startGuide(root.selectedHighlight)
                }
            }
            Text {
                visible: root.selectedDetail === ""
                anchors.centerIn: parent
                text: "결과를 선택하면\n위치를 지도에 표시합니다"
                horizontalAlignment: Text.AlignHCenter
                font.family: S.fontFamily; font.pixelSize: 16; color: S.textMuted
            }
        }
    }
}
