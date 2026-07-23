-- cb_task_logs: 작업로그 soft-delete 를 위한 `hidden` 컬럼 추가.
--
-- ⚠️ 백엔드는 `Base.metadata.create_all()` 로 표를 만든다. create_all 은 **이미 있는 표에
--    컬럼을 더하지 않는다** — 그래서 기존 DB 에는 이 스크립트를 한 번 돌려야 한다.
--    (새로 만드는 DB 는 create_all 이 알아서 넣으므로 실행할 필요 없다.)
--
-- ⚠️ 이 표는 **도서관 웹 DB(`aba`)** 에 있다. FMS DB(`labi`) 가 아니다.
--    접두사로 구분한다: `cb_*` = aba_service(도서관 웹) / `rc_*` = aba_fms_service(관제).
--
-- 실행: mysql -u <user> -p aba < add_task_log_hidden_column.sql
--
-- MariaDB 의 `IF NOT EXISTS` 덕분에 여러 번 돌려도 안전하다(멱등).

USE aba;

ALTER TABLE cb_task_logs
  ADD COLUMN IF NOT EXISTS hidden TINYINT(1) NOT NULL DEFAULT 0
    COMMENT '사서 삭제(soft) 여부 — 감사 보존용';
