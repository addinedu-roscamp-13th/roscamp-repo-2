-- cb_books: 훼손/분실 등으로 인한 "대출 불가" 상태를 위한 `unavailable` 컬럼 추가.
--
-- ⚠️ 백엔드는 `Base.metadata.create_all()` 로 표를 만든다. create_all 은 **이미 있는 표에
--    컬럼을 더하지 않는다** — 그래서 기존 DB 에는 이 스크립트를 한 번 돌려야 한다.
--    (새로 만드는 DB 는 create_all 이 알아서 넣으므로 실행할 필요 없다.)
--
-- in_stock(대출가능/대출중)과 별개 축이다 — in_stock 은 대출/반납이 뒤집는 값이고,
-- unavailable 은 사서가 훼손/분실을 확인했을 때 직접 켜는 값이다. 둘을 합쳐 화면에는
-- 대출가능/대출중/대출불가능 3상태로 보여준다(unavailable 이 최우선).
--
-- 실행: mysql -u <user> -p aba < add_book_unavailable_column.sql
--
-- MariaDB 의 `IF NOT EXISTS` 덕분에 여러 번 돌려도 안전하다(멱등).

USE aba;

ALTER TABLE cb_books
  ADD COLUMN IF NOT EXISTS unavailable TINYINT(1) NOT NULL DEFAULT 0
    COMMENT '훼손/분실 등으로 사서가 대출 불가 처리했는지 (in_stock 과 별개)';
