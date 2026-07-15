# RobotChatAI — Admin Backend (FastAPI + JWT)

FastAPI service backing the `/admin` area of the frontend. JWT auth, admin-account
CRUD, and dashboard stats over the existing `labi` MariaDB.

## Setup

```bash
cd chatbot/backend
python -m venv .venv && source .venv/bin/activate    # optional
pip install -r requirements.txt

cp .env.example .env
# edit .env: real DATABASE_URL (labi_user creds) and a random JWT_SECRET
python -c "import secrets;print(secrets.token_urlsafe(48))"   # -> JWT_SECRET

python scripts/seed_admin.py     # creates cb_admin_users table + initial superadmin (prints password)
uvicorn app.main:app --reload --port 8000
```

Docs at http://localhost:8000/docs.

## Endpoints

| Method | Path                          | Auth   | Purpose                          |
|--------|-------------------------------|--------|----------------------------------|
| GET    | `/api/health`                 | —      | health check                     |
| POST   | `/api/admin/auth/login`       | —      | login → JWT + admin              |
| GET    | `/api/admin/auth/me`          | Bearer | current admin                    |
| GET    | `/api/admin/dashboard/stats`  | Bearer | counts + 14-day chart + recents  |
| GET    | `/api/admin/users`            | Bearer | list admins (`q`, `skip`, `limit`) |
| POST   | `/api/admin/users`            | Bearer | create admin                     |
| GET    | `/api/admin/users/{id}`       | Bearer | get admin                        |
| PUT    | `/api/admin/users/{id}`       | Bearer | update admin                     |
| DELETE | `/api/admin/users/{id}`       | Bearer | delete admin                     |

## Notes

- Passwords stored as bcrypt hashes only. Real secrets live in `.env` (git-ignored).
- Self-delete / self-deactivate and removing the last superadmin are blocked.
- CORS allows the frontend dev origin (`CORS_ORIGINS`, default `http://localhost:3000`).
