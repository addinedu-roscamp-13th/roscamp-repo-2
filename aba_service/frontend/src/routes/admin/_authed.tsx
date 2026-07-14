import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";

import { getToken } from "@/lib/admin-api";

// Pathless layout: guards every nested /admin page. The app is a client SPA,
// so beforeLoad runs in the browser where localStorage is available.
export const Route = createFileRoute("/admin/_authed")({
  beforeLoad: () => {
    if (!getToken()) {
      throw redirect({ to: "/admin/login" });
    }
  },
  component: Outlet,
});
