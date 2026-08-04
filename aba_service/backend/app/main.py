from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import (
    approvals,
    auth,
    books,
    circulation,
    dashboard,
    delivery,
    dev,
    loans,
    member_auth,
    ocr,
    ops,
    ops_extra,
    robot_control,
    users,
    voice,
)

settings = get_settings()

# Serve OpenAPI/Swagger under the same-origin /api prefix so nginx proxies them
# and the Dev Center "API 문서" page can embed /api/docs.
app = FastAPI(
    title="RobotChatAI Admin API",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(dashboard.router)
app.include_router(dev.router)
app.include_router(books.router)
app.include_router(robot_control.router)
app.include_router(ocr.router)
app.include_router(member_auth.router)
app.include_router(loans.router)
app.include_router(delivery.router)
app.include_router(circulation.router)
app.include_router(ops.router)
app.include_router(ops_extra.router)
app.include_router(approvals.router)
app.include_router(voice.router)


@app.on_event("startup")
def startup_event():
    from .database import Base, engine
    # 모델을 import 해야 metadata 에 등록되어 create_all 이 표를 만든다.
    from .models import (  # noqa: F401
        DeliveryRequest,
        IntrusionEvent,
        Loan,
        Member,
        OpsSetting,
        Reservation,
        RobotControlLog,
        TaskLog,
        Wishlist,
    )
    Base.metadata.create_all(bind=engine)

    from .migrations import run_migrations

    run_migrations(engine)

    # OCR 예열은 하지 않는다 — 회원 UI에서 스캔/OCR 진입점을 걷어내 호출자가 없다.
    # 엔드포인트(`routers/ocr.py`)는 로봇팔·사서 기능이 나중에 쓸 수 있어 남겨 둔다.


@app.get("/api/health", tags=["health"])
def health():
    return {"status": "ok"}
