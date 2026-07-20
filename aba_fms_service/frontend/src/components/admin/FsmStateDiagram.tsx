import mermaid from "mermaid";
import { useEffect, useId, useMemo, useRef } from "react";

import type { FsmModel, FsmSnapshot } from "@/lib/admin-api";

// theme:"base" + 밝은 fill/어두운 text.
//
// 예전에는 theme:"dark" 였는데, 관제 화면은 밝은 배경이라 다크 테마의 밝은 라벨 글씨가
// 흰 바탕에 묻혔다. 거기에 dim 을 opacity 로 걸어서 글씨까지 35% 로 흐려졌고, 스냅샷이
// 없을 때는 모든 상태가 dim 대상이라 다이어그램 전체가 빈 회색 박스로 보였다.
// dim 은 아래 classDef 에서 opacity 가 아니라 fill/color 로 준다.
mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
  theme: "base",
  themeVariables: {
    primaryColor: "#f8fafc",
    primaryTextColor: "#0f172a",
    primaryBorderColor: "#cbd5e1",
    lineColor: "#94a3b8",
    fontSize: "13px",
  },
});

interface Props {
  model: FsmModel;
  snapshot: FsmSnapshot | null;
}

/**
 * 상태 다이어그램.
 *
 * 노드 목록·간선·mermaid 소스 전부 model(= GET /api/fsm/model)에서 온다. 이 파일에 상태
 * 이름을 적으면 전이 박스가 바뀌었을 때 화면만 옛 정의로 남으므로 금지한다.
 */
export function FsmStateDiagram({ model, snapshot }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const reactId = useId();
  // mermaid.render 의 id 는 DOM id 로 쓰이므로 콜론 등 CSS 선택자 특수문자를 뺀다.
  const renderId = useMemo(
    () => `fsm-${reactId.replace(/[^a-zA-Z0-9_-]/g, "")}`,
    [reactId],
  );

  const source = useMemo(() => {
    const lines = [model.mermaid];
    const current = snapshot?.current_state;
    const previous = snapshot?.previous_state;

    // 현재 상태는 채움색으로, 직전 상태는 주황 테두리로, 나머지는 옅은 회색으로.
    // 어느 쪽이든 글씨는 항상 읽힌다.
    lines.push(
      "    classDef current fill:#2563eb,stroke:#1d4ed8,stroke-width:3px,color:#ffffff,font-weight:bold",
    );
    lines.push(
      "    classDef previous fill:#fff7ed,stroke:#f97316,stroke-width:2px,color:#9a3412",
    );
    lines.push("    classDef dimmed fill:#f8fafc,stroke:#cbd5e1,color:#475569");

    // 직전 전이는 **간선**이 아니라 출발 상태에 표시한다.
    // mermaid 의 stateDiagram-v2 는 linkStyle 을 지원하지 않는다 — 넣으면 "linkStyle",
    // 인덱스, 스타일 문자열이 각각 떠 있는 상태 노드로 파싱돼 다이어그램에 쓰레기가 뜬다.
    for (const state of model.states) {
      const tone =
        state === current
          ? "current"
          : state === previous
            ? "previous"
            : "dimmed";
      lines.push(`    class ${state} ${tone}`);
    }
    return lines.join("\n");
  }, [model, snapshot?.current_state, snapshot?.previous_state]);

  useEffect(() => {
    let cancelled = false;
    if (!containerRef.current) return;
    mermaid
      .render(renderId, source)
      .then(({ svg }) => {
        if (!cancelled && containerRef.current)
          containerRef.current.innerHTML = svg;
      })
      .catch(() => {
        if (!cancelled && containerRef.current) {
          containerRef.current.textContent =
            "상태 다이어그램을 렌더링하지 못했습니다.";
        }
      });
    return () => {
      cancelled = true;
    };
  }, [source, renderId]);

  // 카드가 좁은 세로 컬럼이라 다이어그램(약 400px)이 컬럼보다 넓어질 수 있다.
  // max-w-full 로 우선 축소해 맞추고, 그래도 넘치면 이 컨테이너만 가로 스크롤한다
  // — 페이지 본문이 가로로 밀리면 3열을 한 화면에서 훑는다는 목적이 깨진다.
  return (
    <div
      ref={containerRef}
      className="overflow-x-auto [&_svg]:h-auto [&_svg]:max-w-full"
    />
  );
}
