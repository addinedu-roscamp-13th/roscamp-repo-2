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
import type { CreateOrderInput } from "@/lib/admin-api";

import { DROPOFF_GROUPS, PICKUP_GROUPS, type WaypointGroup } from "./waypoints";

/**
 * 주문 만들기 — 관제가 직접 task 를 만들어 큐에 넣는 창구.
 * (도서관 웹에서 들어오는 주문과 같은 `/api/fleet/order` 를 쓴다 — 창구는 하나다)
 *
 * 출발지/목적지는 waypoint.yaml 의 정점 이름에서 고른다(`waypoints.ts`). 직접 타이핑하면
 * 오타 하나로 주문이 실패하므로 목록에서만 고르게 한다.
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
          <Input
            id="oc-book"
            value={book}
            onChange={(e) => setBook(e.target.value)}
            placeholder="ISBN 또는 제목"
          />
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
