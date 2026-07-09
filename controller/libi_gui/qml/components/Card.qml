import QtQuick 2.15
import "../Style.js" as S

// 둥근 흰색 패널. 자식은 직접 넣고 anchors/margins 로 배치.
Rectangle {
    radius: S.radCard
    color: S.surface
    border.color: S.border
    border.width: 1.5
}
