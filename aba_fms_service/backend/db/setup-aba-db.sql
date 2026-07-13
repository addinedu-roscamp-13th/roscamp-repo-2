-- ABA DB 부트스트랩 (MariaDB) — walking-skeleton
-- 공유 DB(aba): 회원(aba_server)=cb_*, 관제(aba_fms_service)=rc_*.
-- FMS(중앙 서버)가 DB 생성/부트스트랩 소유 (보일러플레이트 fms setup-labi-db.sql 대응).
-- 사용: sudo mariadb < aba_fms_service/backend/db/setup-aba-db.sql

CREATE DATABASE IF NOT EXISTS aba CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'aba'@'localhost' IDENTIFIED BY 'change-me';
CREATE USER IF NOT EXISTS 'aba'@'127.0.0.1' IDENTIFIED BY 'change-me';
GRANT ALL PRIVILEGES ON aba.* TO 'aba'@'localhost';
GRANT ALL PRIVILEGES ON aba.* TO 'aba'@'127.0.0.1';
FLUSH PRIVILEGES;

USE aba;
CREATE TABLE IF NOT EXISTS cb_books (
  id INT PRIMARY KEY AUTO_INCREMENT COMMENT '도서 ID',
  title VARCHAR(255) COMMENT '제목'
) COMMENT '도서(회원/aba_server)';

CREATE TABLE IF NOT EXISTS rc_robots (
  id VARCHAR(32) PRIMARY KEY COMMENT '로봇 ID',
  name VARCHAR(64) COMMENT '이름'
) COMMENT '로봇대장(관제/aba_fms_service)';
