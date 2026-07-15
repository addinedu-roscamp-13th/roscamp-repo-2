// PM2 루트 런처 — `pm2 start ecosystem.config.js`
// 전부 cwd:__dirname 기준 상대경로 → 레포를 어디로 옮겨도 동작(이식성).
// ROS2 필요 프로세스는 bash -lc 로 /opt/ros/jazzy 소싱 후 uvicorn 기동.
const VENV = __dirname + "/.venv/bin";
const ros = (cmd) =>
  `-lc "source /opt/ros/jazzy/setup.bash && set -a && source ./.env && set +a && exec ${cmd}"`;

module.exports = {
  apps: [
    {
      name: "aba-fms",
      cwd: __dirname + "/aba_fms_service/backend",
      interpreter: "/usr/bin/bash",
      args: ros(`${VENV}/uvicorn main:app --host 0.0.0.0 --port 9001`),
      env: { ROS_DOMAIN_ID: "88", ROS_AUTOMATIC_DISCOVERY_RANGE: "SUBNET" },
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: "aba-server",
      cwd: __dirname + "/aba_server/backend",
      interpreter: "/usr/bin/bash",
      args: ros(`${VENV}/uvicorn app.main:app --host 0.0.0.0 --port 8000`),
      env: { ROS_DOMAIN_ID: "88", ROS_AUTOMATIC_DISCOVERY_RANGE: "SUBNET" },
      autorestart: true,
      max_restarts: 10,
    },
    {
      name: "aba-ai-stub",
      cwd: __dirname + "/aba_ai_service",
      interpreter: VENV + "/python",
      args: "main.py",
      autorestart: true,
      max_restarts: 10,
    },
    // 프론트(회원/사서)는 다음 라운드에 빌드:
    // { name: "aba-server-web", cwd: __dirname+"/aba_server/frontend", script: "npm", args: "run dev" },   // :3000
    // { name: "aba-fms-web",    cwd: __dirname+"/aba_fms_service/frontend", script: "npm", args: "run dev" }, // :9002
  ],
};
