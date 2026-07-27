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

    // 상세 모달 — 도서 1권 / 구역 1곳. 구역 모달의 책 목록은 검색 결과와 **다른 요청**이라
    // 응답 id 를 따로 들고 갈라 받는다(같은 신호를 쓰지만 목록을 서로 덮어쓰면 안 된다).
    property var pickedBook: null
    property var pickedZone: null
    property var zoneBooks: []
    property bool zoneLoading: false
    property bool zoneFailed: false
    property int zonePendingId: 0

    function openZone(name) {
        var list = controller.facilities();
        for (var i = 0; i < list.length; i++) {
            if (list[i].name !== name) continue;
            root.pickedZone = list[i];
            root.selectedHighlight = name;
            root.zoneBooks = []; root.zoneFailed = false;
            if (list[i].category === "") { root.zoneLoading = false; root.zonePendingId = 0; return; }
            // 과학 서가·인문학서가는 정점이 "과학-인문학서가" 하나라 zone 만으로는 안 갈린다
            // — 분야까지 함께 줘야 그 서가에 실제로 꽂힌 책만 나온다.
            root.zoneLoading = true;
            root.zonePendingId = controller.searchBooksAsync("", list[i].category, false, list[i].waypoint);
            return;
        }
    }

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
            if (id === root.zonePendingId) {     // 구역 모달의 서가 조회
                root.zoneLoading = false; root.zoneFailed = !ok; root.zoneBooks = ok ? res : [];
                return;
            }
            if (id !== root.pendingId) return;   // stale 응답 — 이미 다른 요청을 기다리는 중
            root.loading = false; root.serviceError = !ok; root.results = ok ? res : [];
        }
    }
    Component.onCompleted: root.requestNow()

    // 도서 카테고리 → 실제 지도 구역 이름(RobotController::facilities() 와 동일해야
    // 지도에서 하이라이트된다). 서가 이름 규칙이 분야마다 달라(" 서가" 공백 유무)
    // 단순 접미사 이어붙이기로는 안 맞는다.
    // 진열 상태 색 — 회원 앱과 같은 세 갈래(배치중/대출 중/대출 불가).
    function statusColor(status) {
        if (status === "배치중") return S.success;
        if (status === "대출 중") return S.textMuted;
        return S.danger;
    }

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
            // 패널은 정보 조회 전용이라 "대여"라는 말을 쓰지 않는다 — 여기서 대여를 못 한다.
            // 거르는 기준도 **서가에 있는가**(inStock)다. 대출 가능 여부(available)로 거르면
            // 훼손 처리된 책이 서가에 꽂혀 있는데도 목록에서 사라져 문구와 어긋난다.
            Chip { text: "지금 서가에 있는 책"; selected: root.onlyAvail; onClicked: { root.onlyAvail = !root.onlyAvail; root.requestNow() } }
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
                    // 진열 여부까지 한 낱말로 — 대출 중이거나 사서가 막아둔 책은 서가에 없다.
                    StatusPill {
                        visible: root.tab === "book"
                        pillColor: root.statusColor(modelData.status)
                        text: root.tab === "book" ? modelData.status : ""
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        if (root.tab === "book") {
                            root.selectedDetail = modelData.title;
                            root.selectedHighlight = root.categoryZone(modelData.category);
                            root.pickedBook = modelData;
                        } else {
                            root.selectedDetail = modelData.name;
                            root.openZone(modelData.name);
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

        // 지도 — 도면 비율 그대로 잡아야 위아래 빈 띠가 안 생긴다. 남는 세로 공간에
        // 붕 뜨지 않게 오른쪽 칸 가운데에 둔다.
        // 지도는 맨 위에 고정하고, 그 아래 설명은 **남는 높이 안에서** 스크롤한다.
        // 가운데 정렬로 두면 설명이 길어질 때 아래가 화면 밖으로 잘린다(실측).
        Item {
            id: rightCol2
            anchors { left: listView.right; right: parent.right; top: parent.top; bottom: parent.bottom
                      leftMargin: 20 }

            MapView {
                id: map
                anchors { left: parent.left; right: parent.right; top: parent.top }
                // 도면 비율이 기본이지만, 구역을 고르면 아래 설명이 들어갈 자리를 남긴다.
                // 안 그러면 책 알약이 화면 밖으로 잘린다(실측). 높이가 줄면 그림은
                // PreserveAspectFit 이라 가운데로 좁아질 뿐 탭 영역과 어긋나지 않는다.
                height: Math.min(width / mapAspect,
                                 parent.height - (root.pickedZone ? 200 : 40))
                highlight: root.selectedHighlight
                // 목록에서 고르지 않고 지도의 구역을 바로 탭해도 같은 설명이 뜬다.
                onFacilityClicked: (name) => { root.selectedDetail = name; root.openZone(name) }
            }

            Flickable {
                anchors { left: parent.left; right: parent.right; top: map.bottom; bottom: parent.bottom
                          topMargin: 16 }
                contentHeight: belowMap.height
                clip: true

            Column {
                id: belowMap
                width: parent.width
                spacing: 8
            // 아직 아무것도 안 골랐을 때
            Text {
                width: parent.width
                visible: root.pickedZone === null
                horizontalAlignment: Text.AlignHCenter
                text: "지도의 구역을 누르면 안내와 그 서가의 책이 나와요"
                font.family: S.fontFamily; font.pixelSize: 15; color: S.textMuted
                wrapMode: Text.WordWrap
            }

            // 구역 설명 — **모달로 덮지 않는다.** 구역을 고르는 일은 지도를 보면서 하는
            // 일이라, 화면을 가리면 방금 누른 자리와 설명을 같이 볼 수 없다.
            // 도서 상세만 모달로 남긴다(그건 지도와 상관없는 한 권짜리 정보다).
            Column {
                id: zonePanel
                width: parent.width
                spacing: 8
                visible: root.pickedZone !== null

                Row {
                    spacing: 10
                    Text {
                        text: root.pickedZone ? root.pickedZone.icon : ""
                        font.pixelSize: 20
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text: root.pickedZone ? root.pickedZone.name : ""
                        font.family: S.fontFamily; font.pixelSize: 20; font.bold: true; color: S.text
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    BigButton {
                        text: "여기로 안내"; color: S.primary
                        implicitWidth: 150; implicitHeight: 40
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: controller.startGuide(root.pickedZone.name)
                    }
                }
                Text {
                    width: parent.width
                    text: root.pickedZone ? root.pickedZone.desc : ""
                    font.family: S.fontFamily; font.pixelSize: 15; color: S.textSoft
                    wrapMode: Text.WordWrap
                }

                // 서가면 꽂힌 책까지. 서가가 아닌 구역(테이블·화장실)은 여기서 끝난다.
                Text {
                    visible: root.pickedZone && root.pickedZone.category !== ""
                    text: root.zoneLoading ? "이 서가의 책 불러오는 중…"
                         : root.zoneFailed ? "도서 목록을 불러오지 못했습니다"
                         : "이 서가의 책 (" + root.zoneBooks.length + ")"
                    font.family: S.fontFamily; font.pixelSize: 15; font.bold: true
                    color: root.zoneFailed ? S.danger : S.text
                }
                Flow {
                    width: parent.width
                    spacing: 8
                    visible: root.pickedZone && root.pickedZone.category !== "" && !root.zoneLoading
                    Repeater {
                        model: root.zoneBooks
                        // 제목 알약 하나로 줄인다 — 여기서 책을 고르는 게 아니라 "무엇이
                        // 꽂혀 있나"를 훑는 자리다. 자세한 건 눌러서 상세 모달로 본다.
                        delegate: Rectangle {
                            height: 34; radius: 17; width: bookChip.width + 26
                            color: chipMa.pressed ? S.primaryDim : S.surfaceSoft
                            border.color: root.statusColor(modelData.status); border.width: 1.5
                            Text {
                                id: bookChip
                                anchors.centerIn: parent
                                text: modelData.cover + " " + modelData.title
                                font.family: S.fontFamily; font.pixelSize: 14; color: S.text
                            }
                            MouseArea {
                                id: chipMa
                                anchors.fill: parent
                                onClicked: root.pickedBook = modelData
                            }
                        }
                    }
                }
            }
            }
            }
        }
    }

    BookDetailModal {
        book: root.pickedBook
        onClosed: root.pickedBook = null
        onGuideRequested: {
            controller.startGuide(root.categoryZone(root.pickedBook.category));
            root.pickedBook = null; root.pickedZone = null;
        }
    }
}
