-- Rename existing chatbot tables to the cb_ prefix.
-- One-time migration. Do not rerun after the cb_ tables already exist.
-- Run against the existing labi database before deploying the prefixed backend.

USE labi;

RENAME TABLE
  admin_users TO cb_admin_users,
  books TO cb_books,
  robot_control_logs TO cb_robot_control_logs,
  members TO cb_members,
  robot_tasks TO cb_robot_tasks,
  test_no_comment TO cb_test_no_comment,
  conversations TO cb_conversations,
  messages TO cb_messages,
  ai_model_settings TO cb_ai_model_settings;
