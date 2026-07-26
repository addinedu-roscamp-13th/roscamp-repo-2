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

    // 도서 검색은 원격(ABA Service) 조회라 비동기로 뺐다 — 동기 호출을 프로퍼티 바인딩에
    // 두면 키 입력마다 UI 스레드가 최대 1.5초 멈춘다. 시설 검색은 로컬 계산이라 그대로 동기.
    property var results: []
    property bool loading: false
    property bool serviceError: false   // 네트워크/타임아웃 — "결과 없음"과 다른 문구
    property int pendingId: 0           // 0 = 대기 중인 도서 요청 없음

    function requestNow() {
        if (root.tab !== "book") {
            // 로컬 동기 계산이라 로딩/에러 상태가 있을 수 없다 — 도서 탭에서 넘어오며
            // 남아있던 값(예: 요청 중이던 스켈레톤)을 여기서 반드시 꺼야 시설 목록이 가려지지 않는다.
            root.loading = false; root.serviceError = false;
            root.results = controller.searchFacilities(root.query);
            return;
        }
        root.loading = true; root.serviceError = false;
        root.pendingId = controller.searchBooksAsync(root.query, root.category, root.onlyAvail);
    }
    function scheduleBookSearch() { debounce.restart(); }

    Timer { id: debounce; interval: 250; onTriggered: root.requestNow() }
    Connections {
        target: controller
        function onSearchBooksReady(id, res, ok) {
            if (id !== root.pendingId) return;   // stale 응답 — 이미 다른 요청을 기다리는 중
            root.loading = false; root.serviceError = !ok; root.results = ok ? res : [];
        }
    }
    Component.onCompleted: root.requestNow()

    // 도서 카테고리 → 실제 지도 구역 이름(RobotController::facilities() 와 동일해야
    // 지도에서 하이라이트된다). 서가 이름 규칙이 분야마다 달라(" 서가" 공백 유무)
    // 단순 접미사 이어붙이기로는 안 맞는다.
    function categoryZone(category) {
        switch (category) {
        case "과학": return "과학 서가";
        case "예술": return "예술서가";
        case "문학": return "문학서가";
        case "인문학": return "인문학서가";
        default: return category;
        }
    }

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
            // 시설 탭으로 넘어갈 땐 pendingId 를 0으로 리셋 — 넘어가기 전에 나간 도서 검색
            // 응답이 늦게 와도(id!==0) 무시되어 시설 목록을 덮어쓰지 못한다.
            Chip { text: "도서"; icon: "📚"; selected: root.tab === "book";     onClicked: { root.tab = "book"; root.selectedHighlight = ""; root.selectedDetail = ""; root.requestNow() } }
            Chip { text: "시설"; icon: "🏛"; selected: root.tab === "facility"; onClicked: { root.tab = "facility"; root.selectedHighlight = ""; root.selectedDetail = ""; root.pendingId = 0; root.requestNow() } }
        }

        TextField {
            width: parent.width
            placeholderText: root.tab === "book" ? "도서명 · 저자 검색" : "시설 이름 검색"
            font.family: S.fontFamily; font.pixelSize: 18
            color: S.text
            leftPadding: 18; rightPadding: 18; topPadding: 12; bottomPadding: 12
            onTextChanged: { root.query = text; root.tab === "book" ? root.scheduleBookSearch() : root.requestNow() }
            background: Rectangle { radius: 14; color: S.surface; border.color: S.borderStrong; border.width: 1.5 }
        }

        Row {
            visible: root.tab === "book"
            spacing: 10
            Chip { text: "전체"; selected: root.category === "전체"; onClicked: { root.category = "전체"; root.requestNow() } }
            Chip { text: "과학"; selected: root.category === "과학"; onClicked: { root.category = "과학"; root.requestNow() } }
            Chip { text: "예술"; selected: root.category === "예술"; onClicked: { root.category = "예술"; root.requestNow() } }
            Chip { text: "문학"; selected: root.category === "문학"; onClicked: { root.category = "문학"; root.requestNow() } }
            Chip { text: "인문학"; selected: root.category === "인문학"; onClicked: { root.category = "인문학"; root.requestNow() } }
            Chip { text: "대여 가능만"; selected: root.onlyAvail; onClicked: { root.onlyAvail = !root.onlyAvail; root.requestNow() } }
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
            visible: !root.loading && root.results.length > 0

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
                            root.selectedHighlight = root.categoryZone(modelData.category);
                        } else {
                            root.selectedDetail = modelData.name;
                            root.selectedHighlight = modelData.name;
                        }
                    }
                }
            }
        }

        // 불러오는 중 — 스켈레톤 3개
        Column {
            anchors { left: parent.left; top: parent.top }
            width: listView.width
            spacing: 12
            visible: root.loading
            BookRowSkeleton {}
            BookRowSkeleton {}
            BookRowSkeleton {}
        }

        // 결과 없음 / 서비스 오류
        Text {
            anchors { left: parent.left; top: parent.top; topMargin: 48 }
            width: listView.width
            horizontalAlignment: Text.AlignHCenter
            visible: !root.loading && root.results.length === 0
            text: root.serviceError
                  ? "도서 서비스를 불러올 수 없습니다"
                  : (root.tab === "book" ? "검색 결과가 없습니다" : "일치하는 시설이 없습니다")
            font.family: S.fontFamily; font.pixelSize: 16
            color: root.serviceError ? S.danger : S.textMuted
        }

        // 지도 + 상세
        MapView {
            id: map
            anchors { left: listView.right; right: parent.right; top: parent.top; leftMargin: 20 }
            height: parent.height * 0.62
            highlight: root.selectedHighlight
            // 목록에서 고르지 않고 지도의 알약을 바로 탭해도 같은 상세가 뜬다.
            onFacilityClicked: (name) => { root.selectedDetail = name; root.selectedHighlight = name }
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
