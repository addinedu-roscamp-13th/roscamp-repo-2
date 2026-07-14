"""Create tables and seed an initial superadmin.

Run from chatbot/backend:  python scripts/seed_admin.py
Credentials come from SEED_ADMIN_USERNAME / SEED_ADMIN_PASSWORD (.env).
A blank password is auto-generated and printed once.
"""

import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import AdminRole, AdminUser
from app.security import hash_password


def main() -> None:
    settings = get_settings()
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        username = settings.seed_admin_username or "admin"
        existing = db.scalar(select(AdminUser).where(AdminUser.username == username))
        if existing is not None:
            print(f"[seed] admin '{username}' already exists (id={existing.id}); leaving as-is.")
            return

        generated = not settings.seed_admin_password
        password = settings.seed_admin_password or secrets.token_urlsafe(12)

        admin = AdminUser(
            username=username,
            full_name="Super Admin",
            role=AdminRole.superadmin,
            is_active=True,
            hashed_password=hash_password(password),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        print("=" * 52)
        print(f"[seed] created superadmin (id={admin.id})")
        print(f"  username: {username}")
        suffix = "  (generated — change after first login)" if generated else ""
        print(f"  password: {password}{suffix}")
        print("=" * 52)
    finally:
        db.close()


if __name__ == "__main__":
    main()
