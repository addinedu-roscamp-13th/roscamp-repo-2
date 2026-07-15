-- cb_admin_users: admin accounts for the RobotChatAI admin area.
-- Run against the existing `labi` database. The backend's seed_admin.py
-- also creates this table via SQLAlchemy; this file is for manual setup.
--
-- Convention (see CLAUDE.md): every table and column MUST carry a COMMENT.
-- password is stored ONLY as a bcrypt hash, never in plaintext.

USE labi;

CREATE TABLE IF NOT EXISTS cb_admin_users (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT            COMMENT '기본키',
  username        VARCHAR(64)  NOT NULL                              COMMENT '로그인 아이디 (고유)',
  email           VARCHAR(255) NULL                                 COMMENT '이메일 (고유, 선택)',
  full_name       VARCHAR(128) NULL                                 COMMENT '표시 이름',
  hashed_password VARCHAR(255) NOT NULL                             COMMENT 'bcrypt 해시된 비밀번호 (평문 저장 금지)',
  role            ENUM('superadmin', 'admin') NOT NULL DEFAULT 'admin' COMMENT '권한 등급: superadmin | admin',
  is_active       TINYINT(1)   NOT NULL DEFAULT 1                   COMMENT '활성 여부 (0=비활성, 1=활성)',
  last_login_at   TIMESTAMP    NULL                                 COMMENT '마지막 로그인 시각',
  created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP   COMMENT '생성 시각',
  updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP                     COMMENT '수정 시각 (자동 갱신)',
  PRIMARY KEY (id),
  UNIQUE KEY uq_cb_admin_users_username (username),
  UNIQUE KEY uq_cb_admin_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='관리자 계정: 로그인 및 관리자 목록 CRUD 대상';
