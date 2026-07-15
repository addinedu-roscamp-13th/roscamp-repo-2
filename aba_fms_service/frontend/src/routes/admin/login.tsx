import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Bot, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { adminApi, ApiError, getToken, setToken } from "@/lib/admin-api";

export const Route = createFileRoute("/admin/login")({
  head: () => ({ meta: [{ title: "Labi Admin — 로그인" }] }),
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Already authenticated → skip straight to the dashboard.
  useEffect(() => {
    if (getToken()) void navigate({ to: "/admin" });
  }, [navigate]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await adminApi.login(username.trim(), password);
      setToken(result.access_token);
      await navigate({ to: "/admin" });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "로그인 중 오류가 발생했습니다. 서버 상태를 확인해주세요.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-2 text-center">
          <div className="mx-auto flex size-11 items-center justify-center rounded-xl bg-primary/10">
            <Bot className="size-6 text-primary" />
          </div>
          <CardTitle className="text-xl">Labi Admin</CardTitle>
          <CardDescription>관리자 계정으로 로그인하세요</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">아이디</Label>
              <Input
                id="username"
                value={username}
                autoComplete="username"
                autoFocus
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">비밀번호</Label>
              <Input
                id="password"
                type="password"
                value={password}
                autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {error && (
              <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading && <Loader2 className="size-4 animate-spin" />}
              로그인
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
