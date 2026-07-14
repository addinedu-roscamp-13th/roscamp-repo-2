# Routes

TanStack Router uses file-based routing. Every `.tsx` file in this directory is a route. Do not create `src/pages/`, `src/routes/_app/index.tsx`, or `app/layout.tsx`.

## Conventions

| File | URL |
| --- | --- |
| `index.tsx` | `/` |
| `about.tsx` | `/about` |
| `users/index.tsx` | `/users` |
| `users/$id.tsx` | `/users/:id` |
| `posts/{-$category}.tsx` | `/posts/:category?` |
| `files/$.tsx` | `/files/*` |
| `_layout.tsx` | layout route that renders children via `<Outlet />` |
| `__root.tsx` | app shell for every page |

`routeTree.gen.ts` is auto-generated. Do not edit it by hand.

## Admin Routes

Admin pages live under `src/routes/admin/_authed/` and are exposed at `/admin/*`.

| File | URL |
| --- | --- |
| `admin/login.tsx` | `/admin/login` |
| `admin/_authed/index.tsx` | `/admin` |
| `admin/_authed/users.tsx` | `/admin/users` |
| `admin/_authed/robot.tsx` | `/admin/robot` |
| `admin/_authed/buzzer.tsx` | `/admin/buzzer` |
| `admin/_authed/camera.tsx` | `/admin/camera` |
| `admin/_authed/dev/api-docs.tsx` | `/admin/dev/api-docs` |
| `admin/_authed/dev/tables.tsx` | `/admin/dev/tables` |
| `admin/_authed/dev/erd.tsx` | `/admin/dev/erd` |
| `admin/_authed/dev/architecture.tsx` | `/admin/dev/architecture` |
| `admin/_authed/dev/server-ops.tsx` | `/admin/dev/server-ops` |
