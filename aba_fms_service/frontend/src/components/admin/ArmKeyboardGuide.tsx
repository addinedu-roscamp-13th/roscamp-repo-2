export function ArmKeyboardGuide() {
  return (
    <div className="rounded-xl border border-dashed border-indigo-200 bg-indigo-50/50 p-3">
      <p className="text-[11px] font-semibold text-indigo-700 mb-2">⌨️ 키보드 단축키</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] text-indigo-700 font-mono">
        <span><kbd className="bg-white border rounded px-1">← →</kbd> J1 베이스</span>
        <span><kbd className="bg-white border rounded px-1">↑ ↓</kbd> J2 숄더</span>
        <span><kbd className="bg-white border rounded px-1">W S</kbd> J3 엘보</span>
        <span><kbd className="bg-white border rounded px-1">A D</kbd> J4 리스트1</span>
        <span><kbd className="bg-white border rounded px-1">Q E</kbd> J5 리스트2</span>
        <span><kbd className="bg-white border rounded px-1">Z X</kbd> J6 리스트3</span>
        <span><kbd className="bg-white border rounded px-1">O</kbd> 그리퍼 열기</span>
        <span><kbd className="bg-white border rounded px-1">P</kbd> 그리퍼 닫기</span>
        <span className="col-span-2"><kbd className="bg-white border rounded px-1">H</kbd> 홈 포지션</span>
      </div>
    </div>
  );
}
