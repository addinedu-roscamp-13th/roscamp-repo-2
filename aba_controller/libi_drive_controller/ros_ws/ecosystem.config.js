module.exports = {
  apps: [
    {
      name: 'robot-hw',                                     // 터미널 1: 하드웨어 (제일 먼저)
      script: '/home/robotPrj/controller/drive/ros_ws/pm2_start_robot.sh',
      interpreter: 'bash',
      autorestart: true,
      max_restarts: 10,
      time: true,
    },
    {
      name: 'nav2',                                         // 터미널 2: nav2 + 웹서버
      script: '/home/robotPrj/controller/drive/ros_ws/pm2_start_nav.sh',     // /scan 대기 후 시작
      interpreter: 'bash',
      autorestart: true,
      max_restarts: 10,
      time: true,
    },
    {
      name: 'robot_agent',                                  // 터미널 3: 온보드 에이전트(:9001)
      script: '/home/robotPrj/controller/drive/ros_ws/pm2_start_agent.sh',
      interpreter: 'bash',
      autorestart: true,
      max_restarts: 10,
      time: true,
    },
  ],
};
