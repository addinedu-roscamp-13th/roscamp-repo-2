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

    # Warm up the EasyOCR model in the background so it doesn't block boot and
    # the first /api/ocr request doesn't pay the model load/download cost.
    import threading

    threading.Thread(target=ocr.warmup, name="ocr-warmup", daemon=True).start()


@app.get("/api/health", tags=["health"])
def health():
    return {"status": "ok"}
