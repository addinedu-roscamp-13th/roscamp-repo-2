import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState } from "react";

export type SortDirection = "asc" | "desc";

/**
 * 헤더 클릭 정렬 — 클릭할 때마다 없음 → 오름차순 → 내림차순 → 없음으로 순환한다.
 * `comparators`는 정렬 가능한 칼럼 키만 등록한다(작업/관리 같은 칼럼은 등록하지 않으면
 * 자연히 정렬 불가).
 */
export function useSortableTable<T>(
  rows: T[],
  comparators: Record<string, (a: T, b: T) => number>,
) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [direction, setDirection] = useState<SortDirection>("asc");

  const toggle = (key: string) => {
    if (sortKey !== key) {
      setSortKey(key);
      setDirection("asc");
      return;
    }
    if (direction === "asc") {
      setDirection("desc");
      return;
    }
    setSortKey(null);
  };

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const cmp = comparators[sortKey];
    if (!cmp) return rows;
    const arr = [...rows].sort(cmp);
    return direction === "desc" ? arr.reverse() : arr;
  }, [rows, sortKey, direction, comparators]);

  return { sorted, sortKey, direction, toggle };
}

export function SortIcon({
  active,
  direction,
}: {
  active: boolean;
  direction: SortDirection;
}) {
  const Icon = !active
    ? ArrowUpDown
    : direction === "asc"
      ? ArrowUp
      : ArrowDown;
  return (
    <Icon
      className={`ml-1 inline size-3.5 ${active ? "text-foreground" : "text-muted-foreground/50"}`}
    />
  );
}
