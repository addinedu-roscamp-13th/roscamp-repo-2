import { useCallback, useEffect, useState } from "react";
import { buildRobotHttpUrl, useActiveRobotBase } from "@/lib/active-robot";
import { getToken } from "@/lib/admin-api";

export type MapEntry = { name: string; mtime: number; size_kb: number };

export function useMapManager() {
  const activeRobotBase = useActiveRobotBase();
  const [maps, setMaps] = useState<MapEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [supported, setSupported] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [deletingName, setDeletingName] = useState<string | null>(null);

  const request = useCallback(async (path: string, init: RequestInit = {}) => {
    const token = getToken();
    const headers: HeadersInit = {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    };
    const res = await fetch(buildRobotHttpUrl(activeRobotBase, path), { ...init, headers });
    if (res.status === 404) {
      setSupported(false);
      throw new Error("로봇 서버에 지도 관리 API가 없습니다. 로봇 백엔드를 업데이트/재시작하세요.");
    }
    if (!res.ok) throw new Error(`요청 실패 (${res.status})`);
    setSupported(true);
    return res;
  }, [activeRobotBase]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await request("/api/robot/map/list").then((r) => r.json());
      setMaps(r.maps ?? []);
      setFetchError(null);
    } catch (e) {
      setFetchError(String(e));
    } finally {
      setLoading(false);
    }
  }, [request]);

  // 마운트 시 즉시 + 10초마다 자동 갱신
  useEffect(() => {
    refresh();
    const t = setInterval(() => {
      if (supported !== false) void refresh();
    }, 10_000);
    return () => clearInterval(t);
  }, [refresh, supported]);

  const save = async (name: string) => {
    if (supported === false) {
      setSaveMsg({ ok: false, text: "로봇 서버에 지도 관리 API가 없습니다." });
      return;
    }
    setSaving(true);
    setSaveMsg(null);
    try {
      const r = await request("/api/robot/map/save", {
        method: "POST",
        body: JSON.stringify({ name }),
      }).then((r) => r.json());
      setSaveMsg(r.ok
        ? { ok: true, text: `저장됨: ${r.name}` }
        : { ok: false, text: r.error ?? "저장 실패" });
      if (r.ok) await refresh();
    } catch (e) {
      setSaveMsg({ ok: false, text: String(e) });
    }
    setSaving(false);
    setTimeout(() => setSaveMsg(null), 4_000);
  };

  const remove = async (name: string) => {
    if (supported === false) return;
    setDeletingName(name);
    await request(`/api/robot/map/${encodeURIComponent(name)}`, { method: "DELETE" });
    await refresh();
    setDeletingName(null);
  };

  const download = async (name: string) => {
    if (supported === false) return;
    const res = await request(`/api/robot/map/${encodeURIComponent(name)}/download`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  return { maps, loading, fetchError, supported, saving, saveMsg, deletingName, save, remove, download, refresh };
}
