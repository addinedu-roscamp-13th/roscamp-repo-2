-- cb_delivery_requests: 대여 신청 사서 승인 절차(R13-4)에 필요한 컬럼 추가.
--
-- ⚠️ 백엔드는 `Base.metadata.create_all()` 로 표를 만든다. create_all 은 **이미 있는 표에
--    컬럼을 더하지 않는다** — 그래서 기존 DB 에는 이 스크립트를 한 번 돌려야 한다.
--    (새로 만드는 DB 는 create_all 이 알아서 넣으므로 실행할 필요 없다.)
--
-- ⚠️ 이 표는 **도서관 웹 DB(`aba`)** 에 있다. FMS DB(`labi`) 가 아니다.
--    접두사로 구분한다: `cb_*` = aba_service(도서관 웹) / `rc_*` = aba_fms_service(관제).
--    (처음에 `USE labi` 로 적혀 있었다 — 그대로 돌리면 엉뚱한 DB 를 건드린다.)
--
-- 실행: mysql -u <user> -p aba < add_delivery_request_approval.sql
--
-- MariaDB 의 `IF NOT EXISTS` 덕분에 여러 번 돌려도 안전하다(멱등).

USE aba;

ALTER TABLE cb_delivery_requests
  ADD COLUMN IF NOT EXISTS approval VARCHAR(20) NOT NULL DEFAULT 'APPROVED'
    COMMENT '승인 상태 (PENDING_APPROVAL=사서 승인 대기 | APPROVED=승인·주문됨 | REJECTED=반려)',
  ADD COLUMN IF NOT EXISTS decided_at TIMESTAMP NULL
    COMMENT '사서가 승인/반려한 시각 (승인 불필요면 NULL)',
  ADD COLUMN IF NOT EXISTS decided_by VARCHAR(64) NULL
    COMMENT '승인/반려한 사서 계정',
  ADD COLUMN IF NOT EXISTS reject_reason VARCHAR(255) NULL
    COMMENT '반려 사유 (반려가 아니면 NULL)';

-- 승인 전에는 task_id 가 없다 — 기존 NOT NULL 은 유지하고 빈 문자열을 쓴다.
ALTER TABLE cb_delivery_requests
  MODIFY COLUMN fms_task_id VARCHAR(64) NOT NULL DEFAULT ''
    COMMENT 'FMS orchestrator 가 발급한 task_id (승인 전이면 빈 문자열)';

-- 기존 행은 이미 FMS 주문이 나간 것들이므로 APPROVED 로 둔다(DEFAULT 로 이미 채워진다).
-- 승인 대기 목록 조회가 자주 일어나므로 인덱스를 하나 준다.
CREATE INDEX IF NOT EXISTS ix_cb_delivery_requests_approval
  ON cb_delivery_requests (approval, created_at);
