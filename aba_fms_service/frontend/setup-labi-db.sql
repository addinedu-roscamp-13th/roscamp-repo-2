-- labi MariaDB 부트스트랩 스크립트.
-- 백엔드(SQLAlchemy) 는 이 rc_ 접두사 테이블을 그대로 사용한다.
-- create_all 이 없는 테이블만 생성하므로, 이 스크립트는 신규 환경 세팅용.

CREATE DATABASE IF NOT EXISTS labi
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'labi_user'@'localhost'
  IDENTIFIED BY '106a1752c19b1f58429b7a6c131dfedb';

GRANT ALL PRIVILEGES ON labi.* TO 'labi_user'@'localhost';
FLUSH PRIVILEGES;

USE labi;

-- 관리자 계정 (로그인 및 관리자 목록)
CREATE TABLE IF NOT EXISTS rc_admins (
  id INT NOT NULL AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  email VARCHAR(255) NULL,
  full_name VARCHAR(128) NULL,
  role VARCHAR(16) NOT NULL DEFAULT 'admin',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  last_login_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_rc_admins_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 로봇 레지스트리 (로봇팔/핑키봇 등 접속 정보)
CREATE TABLE IF NOT EXISTS rc_robots (
  id INT NOT NULL AUTO_INCREMENT,
  name VARCHAR(64) NOT NULL,
  robot_type VARCHAR(16) NOT NULL DEFAULT 'arm',
  ip_address VARCHAR(64) NOT NULL,
  port INT NOT NULL DEFAULT 9001,
  domain_id INT NULL,
  ai_server_url VARCHAR(255) NULL,
  description VARCHAR(255) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_rc_robots_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 대화 세션
CREATE TABLE IF NOT EXISTS rc_conversations (
  id INT NOT NULL AUTO_INCREMENT,
  session_id VARCHAR(128) NOT NULL,
  title VARCHAR(255) NULL,
  model_name VARCHAR(128) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_rc_conversations_session_id (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 대화 메시지
CREATE TABLE IF NOT EXISTS rc_messages (
  id INT NOT NULL AUTO_INCREMENT,
  conversation_id INT NOT NULL,
  role VARCHAR(16) NOT NULL,
  content TEXT NOT NULL,
  model_name VARCHAR(128) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_rc_messages_conversation_created (conversation_id, created_at),
  CONSTRAINT fk_rc_messages_conversation
    FOREIGN KEY (conversation_id) REFERENCES rc_conversations(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- AI 모델 설정 (단일 행, id=1)
CREATE TABLE IF NOT EXISTS rc_ai_model_settings (
  id INT NOT NULL DEFAULT 1,
  selected_model VARCHAR(128) NOT NULL DEFAULT 'qwen3:1.7b',
  ollama_url VARCHAR(255) NOT NULL DEFAULT 'http://127.0.0.1:11434',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  CONSTRAINT chk_rc_ai_model_settings_singleton CHECK (id = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO rc_ai_model_settings (id, selected_model, ollama_url)
VALUES (1, 'qwen3:1.7b', 'http://127.0.0.1:11434')
ON DUPLICATE KEY UPDATE
  selected_model = VALUES(selected_model),
  ollama_url = VALUES(ollama_url);

-- LCD 표시용 업로드 이미지 메타
CREATE TABLE IF NOT EXISTS rc_lcd_images (
  id INT NOT NULL AUTO_INCREMENT,
  filename VARCHAR(255) NOT NULL,
  original_name VARCHAR(255) NOT NULL,
  content_type VARCHAR(128) NULL,
  size_bytes INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_rc_lcd_images_filename (filename)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 저장된 모션 시퀀스 (waypoint JSON)
CREATE TABLE IF NOT EXISTS rc_motion_sequences (
  id INT NOT NULL AUTO_INCREMENT,
  name VARCHAR(128) NOT NULL,
  description VARCHAR(255) NULL,
  waypoints TEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_rc_motion_sequences_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
