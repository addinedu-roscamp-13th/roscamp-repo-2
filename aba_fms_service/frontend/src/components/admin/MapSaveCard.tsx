import { useState } from "react";
import { Download, Loader2, RefreshCw, Save, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useMapManager } from "@/hooks/useMapManager";

interface Props {
  slamRunning?: boolean;   // SLAM 미실행 시 저장 버튼 비활성화
}

export function MapSaveCard({ slamRunning = true }: Props) {
  const { maps, loading, fetchError, supported, saving, saveMsg, deletingName, save, remove, download, refresh } = useMapManager();
  const [name, setName] = useState("");

  const handleSave = () => {
    save(name);
    setName("");
  };

  return (
    <>
      {/* 지도 저장 */}
      <Card className="shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">지도 저장</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Input
            placeholder="맵 이름 (비워두면 자동)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !saving && slamRunning && handleSave()}
            className="h-8 text-[12px]"
          />
          <Button
            size="sm"
            className="w-full"
            disabled={supported === false || saving || !slamRunning}
            onClick={handleSave}
          >
            {saving
              ? <Loader2 className="size-3 animate-spin" />
              : <Save className="size-3" />}
            현재 맵 저장
          </Button>
          {supported === false ? (
            <p className="text-[11px] text-red-600">로봇 서버에 지도 관리 API가 없습니다. 백엔드 업데이트/재시작이 필요합니다.</p>
          ) : !slamRunning && (
            <p className="text-[11px] text-amber-600">SLAM이 실행 중이어야 저장 가능합니다.</p>
          )}
          {saveMsg && (
            <p className={`text-[11px] ${saveMsg.ok ? "text-green-600" : "text-red-600"}`}>
              {saveMsg.text}
            </p>
          )}
        </CardContent>
      </Card>

      {/* 저장된 지도 목록 */}
      <Card className="shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center justify-between">
            저장된 지도 목록
            <Button size="sm" variant="ghost" onClick={refresh} disabled={supported === false} className="h-7 px-2">
              <RefreshCw className="size-3" />
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="flex items-center justify-center gap-2 py-4 text-[12px] text-slate-400">
              <Loader2 className="size-3 animate-spin" /> 불러오는 중…
            </p>
          ) : fetchError ? (
            <p className="py-4 text-center text-[11px] text-red-500">{fetchError}</p>
          ) : maps.length === 0 ? (
            <p className="py-4 text-center text-[12px] text-slate-400">저장된 지도가 없습니다.</p>
          ) : (
            <div className="divide-y divide-slate-100">
              {maps.map((m) => (
                <div key={m.name} className="flex items-center gap-2 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[12px] font-semibold text-slate-700">{m.name}</p>
                    <p className="text-[11px] text-slate-400">
                      {new Date(m.mtime * 1000).toLocaleString("ko-KR")} · {m.size_kb} KB
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    title="다운로드 (.yaml + .pgm ZIP)"
                    className="h-7 shrink-0 px-2 text-indigo-500 hover:bg-indigo-50 hover:text-indigo-700"
                    onClick={() => download(m.name)}
                  >
                    <Download className="size-3" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 shrink-0 px-2 text-red-500 hover:bg-red-50 hover:text-red-700"
                    disabled={deletingName === m.name}
                    onClick={() => remove(m.name)}
                  >
                    {deletingName === m.name
                      ? <Loader2 className="size-3 animate-spin" />
                      : <Trash2 className="size-3" />}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}
