import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/**
 * 전체 중 비율(part-of-whole) 강조용 도넛 — 조각이 몇 개 안 될 때만 쓴다.
 * 차트 박스를 정사각형으로 고정해 원이 항상 박스를 꽉 채우게 한다. 각도만으로는
 * 못 읽으니 옆에 값 목록을 항상 같이 보여준다(범례 겸 직접 라벨).
 */
export function MiniDonut({
  title,
  data,
}: {
  title: string;
  data: { label: string; value: number; color: string }[];
}) {
  const total = data.reduce((sum, d) => sum + d.value, 0);
  return (
    <div className="flex h-full min-h-0 flex-1 flex-col rounded-lg border p-2">
      <p className="mb-1 shrink-0 text-sm font-semibold whitespace-nowrap text-muted-foreground">
        {title}
      </p>
      {total === 0 ? (
        <p className="text-xs text-muted-foreground">데이터 없음</p>
      ) : (
        <div className="flex min-h-0 flex-1 items-center justify-center gap-12">
          <ul className="grid shrink-0 grid-cols-[auto_auto_auto] items-center gap-x-2 gap-y-0.5 text-xs">
            {data.map((d) => (
              <li key={d.label} className="contents">
                <span
                  className="size-2.5 shrink-0 rounded-full"
                  style={{ background: d.color }}
                />
                <span className="text-muted-foreground">{d.label}</span>
                <span className="text-right font-semibold tabular-nums">
                  {d.value}
                </span>
              </li>
            ))}
          </ul>
          <div className="aspect-square h-full min-w-0 max-w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="value"
                  nameKey="label"
                  innerRadius="55%"
                  outerRadius="100%"
                  paddingAngle={2}
                  stroke="none"
                >
                  {data.map((d) => (
                    <Cell key={d.label} fill={d.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "var(--color-card)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value: number, name: string) => [
                    `${value}건`,
                    name,
                  ]}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * 최근 7일 작업 성공/실패 — 하루 하나의 막대에 완료(good)/실패(critical) 두 상태를
 * 쌓아 올린다. 2개 시리즈라 범례는 항상 붙인다(색만으로 구분하게 두지 않음).
 */
export function WeeklyTaskBars({
  data,
}: {
  data: { date: string; completed: number; failed: number }[];
}) {
  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border p-2">
      <div className="mb-1 flex shrink-0 items-center justify-between">
        <p className="text-sm font-semibold whitespace-nowrap text-muted-foreground">
          최근 7일 작업 성공/실패
        </p>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <span
              className="size-2 rounded-full"
              style={{ background: "var(--chart-status-good)" }}
            />
            완료
          </span>
          <span className="flex items-center gap-1">
            <span
              className="size-2 rounded-full"
              style={{ background: "var(--chart-status-critical)" }}
            />
            실패
          </span>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 4, right: 4, bottom: 0, left: -20 }}
          >
            <XAxis
              dataKey="date"
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}
            />
            <YAxis
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}
            />
            <Tooltip
              cursor={{ fill: "var(--color-muted)" }}
              contentStyle={{
                background: "var(--color-card)",
                border: "1px solid var(--color-border)",
                borderRadius: 8,
                fontSize: 12,
              }}
            />
            <Bar
              dataKey="completed"
              name="완료"
              stackId="a"
              fill="var(--chart-status-good)"
              radius={[0, 0, 0, 0]}
              maxBarSize={28}
            />
            <Bar
              dataKey="failed"
              name="실패"
              stackId="a"
              fill="var(--chart-status-critical)"
              radius={[4, 4, 0, 0]}
              maxBarSize={28}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/**
 * 카테고리별 상태 구성비 — 가로 스택 막대. `rows`가 하나면(members 상태 요약처럼)
 * 단일 막대, 여러 개면(books 서가 배치처럼) 항목마다 한 줄. `segments` 순서가 곧
 * 스택 쌓는 순서이자 범례 순서.
 */
export function StackedStatusBar({
  rows,
  segments,
  unit = "",
}: {
  rows: { label: string; values: Record<string, number> }[];
  segments: { key: string; label: string; color: string }[];
  unit?: string;
}) {
  const data = rows.map((r) => ({
    label: r.label,
    total: segments.reduce((sum, s) => sum + (r.values[s.key] ?? 0), 0),
    ...r.values,
  }));
  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border p-2">
      <ResponsiveContainer
        width="100%"
        height={Math.max(64, data.length * 44)}
      >
        <BarChart
          data={data}
          layout="vertical"
          margin={{ left: 8, right: 28, top: 4, bottom: 4 }}
        >
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="label"
            width={72}
            axisLine={false}
            tickLine={false}
            tick={{ fontSize: 12, fill: "var(--color-muted-foreground)" }}
          />
          <Tooltip
            cursor={{ fill: "var(--color-muted)" }}
            contentStyle={{
              background: "var(--color-card)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value: number, name: string) => [
              `${value}${unit}`,
              segments.find((s) => s.key === name)?.label ?? name,
            ]}
          />
          {segments.map((s, i) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              stackId="s"
              fill={s.color}
              radius={
                i === 0
                  ? [4, 0, 0, 4]
                  : i === segments.length - 1
                    ? [0, 4, 4, 0]
                    : [0, 0, 0, 0]
              }
              maxBarSize={18}
            >
              {i === segments.length - 1 ? (
                <LabelList
                  dataKey="total"
                  position="right"
                  formatter={(v: number) => `${v}${unit}`}
                  className="fill-muted-foreground text-xs"
                />
              ) : null}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
        {segments.map((s) => (
          <span key={s.key} className="flex items-center gap-1">
            <span
              className="size-2.5 rounded-full"
              style={{ background: s.color }}
            />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
