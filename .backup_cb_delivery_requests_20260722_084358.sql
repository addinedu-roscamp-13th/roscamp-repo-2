-- 마이그레이션 직전 백업
CREATE TABLE `cb_delivery_requests` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '기본키',
  `member_id` bigint(20) NOT NULL COMMENT '요청한 회원',
  `book_id` bigint(20) NOT NULL COMMENT '요청 도서',
  `kind` varchar(20) NOT NULL COMMENT '요청 종류 (read=열람·테이블 배달 | borrow=대여·안내데스크 이송)',
  `pickup` varchar(50) NOT NULL COMMENT '집는 waypoint (도서의 zone)',
  `dropoff` varchar(50) NOT NULL COMMENT '전달 waypoint (테이블 또는 안네데스크)',
  `fms_task_id` varchar(64) NOT NULL COMMENT 'FMS orchestrator 가 발급한 task_id',
  `created_at` datetime NOT NULL DEFAULT current_timestamp() COMMENT '접수 시각',
  PRIMARY KEY (`id`),
  KEY `member_id` (`member_id`),
  KEY `book_id` (`book_id`),
  CONSTRAINT `cb_delivery_requests_ibfk_1` FOREIGN KEY (`member_id`) REFERENCES `cb_members` (`id`) ON DELETE CASCADE,
  CONSTRAINT `cb_delivery_requests_ibfk_2` FOREIGN KEY (`book_id`) REFERENCES `cb_books` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=9 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='회원 로봇 배달 요청 접수 기록 (FMS 주문과 대응)';

INSERT INTO cb_delivery_requests (`id`, `member_id`, `book_id`, `kind`, `pickup`, `dropoff`, `fms_task_id`, `created_at`) VALUES ('1', '1', '1', 'read', '문학-1', '테이블-1번-상', 't1', '2026-07-21 13:01:41');
INSERT INTO cb_delivery_requests (`id`, `member_id`, `book_id`, `kind`, `pickup`, `dropoff`, `fms_task_id`, `created_at`) VALUES ('2', '1', '2', 'borrow', '문학-1', '안네데스크', 't2', '2026-07-21 13:01:41');
INSERT INTO cb_delivery_requests (`id`, `member_id`, `book_id`, `kind`, `pickup`, `dropoff`, `fms_task_id`, `created_at`) VALUES ('3', '1', '1', 'read', '문학-1', '테이블-1번-상', 't1', '2026-07-21 13:16:02');
INSERT INTO cb_delivery_requests (`id`, `member_id`, `book_id`, `kind`, `pickup`, `dropoff`, `fms_task_id`, `created_at`) VALUES ('4', '1', '1', 'read', '문학-1', '테이블-1번-상', 't1', '2026-07-21 13:17:57');
INSERT INTO cb_delivery_requests (`id`, `member_id`, `book_id`, `kind`, `pickup`, `dropoff`, `fms_task_id`, `created_at`) VALUES ('5', '1', '1', 'read', '문학-1', '테이블-1번-상', 't2', '2026-07-21 13:40:03');
INSERT INTO cb_delivery_requests (`id`, `member_id`, `book_id`, `kind`, `pickup`, `dropoff`, `fms_task_id`, `created_at`) VALUES ('6', '1', '3', 'read', '문학-1', '테이블-2번-하', 't3', '2026-07-21 13:57:09');
INSERT INTO cb_delivery_requests (`id`, `member_id`, `book_id`, `kind`, `pickup`, `dropoff`, `fms_task_id`, `created_at`) VALUES ('7', '1', '1', 'read', '문학-1', '테이블-1번-상', 't1', '2026-07-21 14:05:53');
INSERT INTO cb_delivery_requests (`id`, `member_id`, `book_id`, `kind`, `pickup`, `dropoff`, `fms_task_id`, `created_at`) VALUES ('8', '1', '27', 'read', '유아', '테이블-1번-상', 't2', '2026-07-21 14:13:51');
