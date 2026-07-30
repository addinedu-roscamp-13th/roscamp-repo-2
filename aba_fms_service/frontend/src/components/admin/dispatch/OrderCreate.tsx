import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { adminApi, type CreateOrderInput } from "@/lib/admin-api";

import { DROPOFF_GROUPS, PICKUP_GROUPS, type WaypointGroup } from "./waypoints";

/**
 * 주문 만들기 — 관제가 직접 task 를 만들어 큐에 넣는 창구.
 * (도서관 웹에서 들어오는 주문과 같은 `/api/fleet/order` 를 쓴다 — 창구는 하나다)
 *
 * 출발지/목적지는 waypoint.yaml 의 정점 이름에서 고른다(`waypoints.ts`). 직접 타이핑하면
 * 오타 하나로 주문이 실패하므로 목록에서만 고르게 한다.
 *
 * 도서는 DB(`cb_books`)에서 온다 — 제목을 고르면 그 책의 `zone`(= 서가 waypoint)이
 * 출발지에 자동으로 들어간다. 사서가 "이 책이 어느 서가더라"를 외울 필요를 없애는 게 목적이라,
 * 자동으로 채운 뒤에도 출발지는 여전히 바꿀 수 있게 둔다(예: 반납 수거함에서 집을 때).
 */

function WaypointSelect({
  id,
  value,
  onChange,
  groups,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  groups: WaypointGroup[];
  placeholder: string;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {groups.map((g) => (
          <SelectGroup key={g.group}>
            <SelectLabel>{g.group}</SelectLabel>
            {g.options.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
                <span className="ml-2 font-mono text-xs text-muted-foreground">
                  {o.value}
                </span>
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  );
}

export function OrderCreate({
  onSubmit,
  onSaveTemplate,
  pending,
}: {
  onSubmit: (input: CreateOrderInput) => void;
  onSaveTemplate: (t: {
    name: string;
    book: string;
    pickup: string;
    dropoff: string;
    requester: string;
    priority: number;
  }) => void;
  pending: boolean;
}) {
  const [book, setBook] = useState("");
  const [pickup, setPickup] = useState("");
  const [dropoff, setDropoff] = useState("");
  const [requester, setRequester] = useState("");
  const [priority, setPriority] = useState("0");
  const [templateName, setTemplateName] = useState("");

  // 카탈로그는 거의 안 바뀐다 — 큐처럼 폴링하지 않고 한 번 받아 캐시한다.
  const booksQuery = useQuery({
    queryKey: ["fleet", "books"],
    queryFn: () => adminApi.fleetBooks(),
    staleTime: 5 * 60_000,
  });
  const books = booksQuery.data?.books ?? [];

  /** 제목이 카탈로그와 정확히 일치하면 그 책의 서가를 출발지로 채운다.
   *
   * ⚠️ 동명이서(제목이 같은 다른 책)가 있으면 **채우지 않는다.** `cb_books.title_kr` 에는
   * unique 제약이 없어서 첫 항목의 서가를 넣으면 조용히 엉뚱한 서가로 보낸다 —
   * 그럴 땐 사람이 출발지를 직접 고르게 두는 편이 낫다. */
  const onBookChange = (v: string) => {
    setBook(v);
    const hits = books.filter((b) => b.title_kr === v);
    if (hits.length === 1 && hits[0].zone) setPickup(hits[0].zone);
  };

  const filled = !!book && !!pickup && !!dropoff;
  const canSubmit = filled && !pending;

  const submit = () => {
    onSubmit({
      book,
      pickup,
      dropoff,
      requester,
      priority: Number(priority) || 0,
    });
    setBook("");
    setPickup("");
    setDropoff("");
  };

  const saveTemplate = () => {
    onSaveTemplate({
      // 이름을 안 적으면 "출발지 → 목적지"로 자동 명명한다.
      name: templateName.trim() || `${pickup} → ${dropoff}`,
      book,
      pickup,
      dropoff,
      requester,
      priority: Number(priority) || 0,
    });
    setTemplateName("");
  };

  return (
    <section className="space-y-3 rounded-lg border p-4">
      <div>
        <h3 className="text-sm font-semibold">주문 만들기</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          접수되면 orchestrator 가 다리(주행→집기→주행→놓기)로 쪼개 큐에 넣는다.
          출발지·목적지는 로봇 내비 그래프(waypoint)의 정점이다.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="oc-book">도서</Label>
          {/* datalist — 80권을 셀렉트로 훑는 것보다 타이핑 검색이 빠르고,
              목록에 없는 책(신간·미등록)도 그대로 적어 넣을 수 있다. */}
          <Input
            id="oc-book"
            list="oc-book-list"
            value={book}
            onChange={(e) => onBookChange(e.target.value)}
            placeholder={
              booksQuery.isPending ? "도서 목록 불러오는 중…" : "제목 검색 또는 직접 입력"
            }
          />
          <datalist id="oc-book-list">
            {books.map((b) => (
              <option key={b.id} value={b.title_kr}>
                {b.author} · {b.zone}
                {b.unavailable ? " · 대출불가" : b.in_stock ? "" : " · 대출중"}
              </option>
            ))}
          </datalist>
          <p className="text-xs text-muted-foreground">
            {booksQuery.isError
              ? "도서 DB를 못 읽었다 — 제목을 직접 입력해도 주문은 접수된다."
              : books.length
                ? `카탈로그 ${books.length}권 · 고르면 출발지가 자동으로 채워진다`
                : " "}
          </p>
        </div>

        <div className="space-y-1">
          <Label htmlFor="oc-pickup">출발지 — 집을 곳</Label>
          <WaypointSelect
            id="oc-pickup"
            value={pickup}
            onChange={setPickup}
            groups={PICKUP_GROUPS}
            placeholder="서가 선택"
          />
        </div>

        <div className="space-y-1">
          <Label htmlFor="oc-dropoff">목적지 — 배달할 곳</Label>
          <WaypointSelect
            id="oc-dropoff"
            value={dropoff}
            onChange={setDropoff}
            groups={DROPOFF_GROUPS}
            placeholder="전달 위치 선택"
          />
        </div>

        <div className="space-y-1">
          <Label htmlFor="oc-requester">요청자</Label>
          <Input
            id="oc-requester"
            value={requester}
            onChange={(e) => setRequester(e.target.value)}
            placeholder="(선택)"
          />
        </div>

        <div className="space-y-1">
          <Label htmlFor="oc-priority">우선순위</Label>
          <Input
            id="oc-priority"
            type="number"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
        </div>

        <div className="flex items-end">
          <Button className="w-full" disabled={!canSubmit} onClick={submit}>
            주문 접수
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-2 border-t pt-3">
        <div className="min-w-[12rem] flex-1 space-y-1">
          <Label htmlFor="oc-tpl-name">템플릿 이름 (선택)</Label>
          <Input
            id="oc-tpl-name"
            value={templateName}
            onChange={(e) => setTemplateName(e.target.value)}
            placeholder="비우면 «출발지 → 목적지» 로 저장"
          />
        </div>
        <Button variant="outline" disabled={!filled} onClick={saveTemplate}>
          템플릿으로 저장
        </Button>
      </div>
      {!filled ? (
        <p className="text-xs text-muted-foreground">
          도서·출발지·목적지를 모두 채우면 접수와 템플릿 저장을 할 수 있습니다.
        </p>
      ) : null}
    </section>
  );
}
