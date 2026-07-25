import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { z } from "zod";

import { AppShell } from "@/components/AppShell";
import { memberApi, setToken } from "@/lib/member";

// 로그인 전 하려던 일(예: 도서 상세에서 요청하기)을 잃지 않기 위한 복귀 경로.
// 없으면 /me 로 간다. 임의 URL을 열어주는 open-redirect 가 되지 않도록
// 반드시 "/" 로 시작하는 내부 경로만 허용한다.
const loginSearchSchema = z.object({
  redirect: z.string().startsWith("/").optional().catch(undefined),
});

export const Route = createFileRoute("/login")({
  validateSearch: loginSearchSchema,
  head: () => ({ meta: [{ title: "LiBi — 로그인" }] }),
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const { redirect } = Route.useSearch();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await memberApi.login(username.trim(), password);
      setToken(res.access_token);
      navigate({ to: redirect || "/me" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "로그인에 실패했습니다");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <div className="px-5 pb-10 pt-8">
        <h1 className="text-xl font-bold text-foreground">회원 로그인</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          도서 요청·대출 현황을 보려면 로그인해 주세요
        </p>

        <form onSubmit={submit} className="mt-6 space-y-3">
          <div className="space-y-1">
            <label
              htmlFor="mid"
              className="text-xs font-medium text-foreground"
            >
              아이디
            </label>
            <input
              id="mid"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              placeholder="member1"
              className="h-12 w-full rounded-xl border border-border bg-card px-4 text-sm outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          <div className="space-y-1">
            <label
              htmlFor="mpw"
              className="text-xs font-medium text-foreground"
            >
              비밀번호
            </label>
            <input
              id="mpw"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="h-12 w-full rounded-xl border border-border bg-card px-4 text-sm outline-none focus:ring-2 focus:ring-primary"
            />
          </div>

          {error ? (
            <p className="rounded-xl bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={busy || !username || !password}
            className="h-12 w-full rounded-xl bg-primary text-sm font-bold text-primary-foreground disabled:opacity-50"
          >
            {busy ? "로그인 중..." : "로그인"}
          </button>
        </form>
      </div>
    </AppShell>
  );
}
