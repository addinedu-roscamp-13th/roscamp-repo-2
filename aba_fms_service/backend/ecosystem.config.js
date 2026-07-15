// pm2 프로세스 설정 — bot_ai_server 백엔드 (FastAPI/uvicorn) + ROS2 도메인 브릿지
// 실행: pm2 start ecosystem.config.js  (backend 디렉터리에서)
//
// [2026-07-07] 브릿지 추가: 로봇 텔레메트리를 서버 도메인으로 개명 중계(/pinky{N}/*).
// [2026-07-08] 서버 도메인을 87→86 으로 옮기고 로봇3(87)도 브릿지에 편입해 3대 통일.
//   각 브릿지는 fleet link 토픽(fleet_status/costmaps/cmd_result + 역방향 fleet_cmd)도 중계한다.
// 공유기 멀티캐스트 차단 → 로봇 쪽 ROS_STATIC_PEERS=192.168.0.19 로 유니캐스트 디스커버리.
const rosEnv = {
  ROS_AUTOMATIC_DISCOVERY_RANGE: "SUBNET",
  PYTHONUNBUFFERED: "1",
};

// pm2 는 shell 이 아니므로 ros2 환경을 bash 래퍼로 소싱한다.
const bridgeCommon = {
  script: "/usr/bin/bash",
  interpreter: "none",
  autorestart: true,
  max_restarts: 10,
  restart_delay: 5000,
  env: rosEnv,
};

module.exports = {
  apps: [
    {
      name: "bot-ai-backend",
      // venv 의 uvicorn 을 직접 실행 (node 가 아니므로 interpreter none)
      script: "./.venv/bin/uvicorn",
      args: "main:app --host 0.0.0.0 --port 9001",
      cwd: "/home/Aiprj/bot_ai_server/backend",
      interpreter: "none",
      autorestart: true,
      max_restarts: 10,
      // 짧은 시간 내 반복 크래시 시 재시작 간격
      restart_delay: 3000,
      env: {
        PYTHONUNBUFFERED: "1",
        // 레거시 ros_bridge(조이스틱 등)의 기존 동작 보존용 — 종전 실행 env 와 동일하게 88.
        // 플릿 텔레메트리 노드는 fleet_telemetry.py 가 자체 Context 로 도메인 86 고정이라 이 값과 무관.
        ROS_DOMAIN_ID: "88",
        ROS_AUTOMATIC_DISCOVERY_RANGE: "SUBNET",
      },
    },
    {
      ...bridgeCommon,
      name: "bridge-pinky1",
      args: [
        "-lc",
        "source /opt/ros/jazzy/setup.bash && exec ros2 run domain_bridge domain_bridge /home/Aiprj/bot_ai_server/config/domain_bridge_pinky1.yaml",
      ],
    },
    {
      ...bridgeCommon,
      name: "bridge-pinky2",
      args: [
        "-lc",
        "source /opt/ros/jazzy/setup.bash && exec ros2 run domain_bridge domain_bridge /home/Aiprj/bot_ai_server/config/domain_bridge_pinky2.yaml",
      ],
    },
    {
      ...bridgeCommon,
      name: "bridge-pinky3",
      args: [
        "-lc",
        "source /opt/ros/jazzy/setup.bash && exec ros2 run domain_bridge domain_bridge /home/Aiprj/bot_ai_server/config/domain_bridge_pinky3.yaml",
      ],
    },
  ],
};
