#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
import math
import time

from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState
from tf2_ros import TransformBroadcaster
from tf_transformations import quaternion_from_euler

from .cmd_watchdog import CMD_VEL_TIMEOUT_SEC, command_expired
from .dynamixel_driver import DynamixelDriver

TWIST_SUB_TOPIC_NAME = "cmd_vel"

ODOM_PUB_TOPIC_NAME = "odom"
JOINT_PUB_TOPIC_NAME = "joint_states"
ODOM_FRAME_ID = "odom"
ODOM_CHILD_FRAME_ID = "base_footprint"

SERIAL_PORT_NAME = "/dev/ttyAMA5"
BAUDRATE = 1000000
DYNAMIXEL_IDS = [1, 2] # [왼쪽 바퀴 ID, 오른쪽 바퀴 ID]

JOINT_NAME_WHEEL_L = "left_wheel_joint"
JOINT_NAME_WHEEL_R = "right_wheel_joint"

PULSE_PER_ROT = 4096
RPM2RAD = 2 * math.pi / 60

# [2026-07-30] 저전압 경고를 여기서 뺐다 (`battery/voltage` 구독 + 임계 6.8V + 5초마다 WARN).
#
# 셋 다 이유가 있다:
#   · 임계 6.8V 가 PinkyPro 보일러플레이트 유산인데 이 팩의 **실측 전 구간이 6.8V 이하**라
#     (5.88~6.78V) 늘 참이었다 — 항상 켜진 경고는 경고가 아니다
#   · 5초마다 찍혀서 진짜 경고(바로 아래 cmd_vel 워치독)가 로그에서 안 보였다
#   · 전압을 소유한 곳은 `battery_publisher` 다. 판단도 거기 있어야 한다
# 지금은 거기서 **퍼센트 기준**으로 1분에 한 번만 낸다(battery_publisher.py 머리말).

#: `/cmd_vel` 발행자 감시 주기(초). 첫 검사는 DDS 탐색이 끝나야 의미가 있어 한 주기 뒤다.
CMD_VEL_PUBLISHER_AUDIT_SEC = 30.0
#: 바퀴로 가는 문을 여는 유일한 정당한 발행자.
EXPECTED_CMD_VEL_PUBLISHER = "twist_mux"

class Pinky(Node):
    def __init__(self):
        super().__init__('pinky_bringup')
        self.is_initialized = False
        
        self.get_logger().info('Initializing Pinky Bringup Node with Dynamixel...')
        
        self.declare_parameter('wheel_radius', 0.027)
        self.declare_parameter('wheel_separation', 0.0961)
        
        self.wheel_radius = self.get_parameter('wheel_radius').get_parameter_value().double_value
        self.wheel_separation = self.get_parameter('wheel_separation').get_parameter_value().double_value
        
        self.get_logger().info(f'Wheel radius: {self.wheel_radius}')
        self.get_logger().info(f'Wheel separation: {self.wheel_separation}')
        
        self.circumference = 2 * math.pi * self.wheel_radius
        self.driver = DynamixelDriver(SERIAL_PORT_NAME, BAUDRATE, DYNAMIXEL_IDS)

        self.get_logger().info("1. Opening serial port...")
        if not self.driver.begin():
            self.get_logger().error("Failed to open serial port! Shutting down.")
            return

        self.get_logger().info("2. Initializing motors...")
        if not self.driver.initialize_motors():
            self.get_logger().error("Failed to initialize motors! Shutting down.")
            self.driver.terminate()
            return
        
        self.get_logger().info("Waiting for motors to be ready...")
        time.sleep(1.0)

        self.get_logger().info("3. Setting initial RPM to zero...")
        if not self.driver.set_double_rpm(0, 0):
            self.get_logger().error("Failed to set initial RPM! Shutting down.")
            self.driver.terminate()
            return

        self.get_logger().info("4. Reading initial encoder values...")
        _, _, self.last_encoder_l, self.last_encoder_r = self.driver.get_feedback()
        if self.last_encoder_l is None:
            self.get_logger().error("Failed to read initial encoder position! Shutting down.")
            self.driver.terminate()
            return

        self.get_logger().info(f"Initial Encoder read: L={self.last_encoder_l}, R={self.last_encoder_r}. Controller is responsive.")
            
        # ⚠️ 아래 구독·타이머를 만들기 **전에** 둔다. 타이머 콜백이 이 값을 읽는데,
        #    생성 뒤에 초기화하면 첫 tick 이 AttributeError 로 죽는다.
        self.last_cmd_time = None      # 아직 한 번도 못 받았다 (CMD_VEL_TIMEOUT_SEC 주석)
        self.stopped_by_watchdog = False

        self.odom_pub = self.create_publisher(Odometry, ODOM_PUB_TOPIC_NAME, 10)
        self.joint_pub = self.create_publisher(JointState, JOINT_PUB_TOPIC_NAME, 10)
        self.twist_sub = self.create_subscription(Twist, TWIST_SUB_TOPIC_NAME, self.twist_callback, 10)
        self.tf_broadcaster = TransformBroadcaster(self)
        self.timer = self.create_timer(1.0 / 30.0, self.update_and_publish)

        self._known_cmd_vel_publishers = None
        self.cmd_vel_audit_timer = self.create_timer(
            CMD_VEL_PUBLISHER_AUDIT_SEC, self._audit_cmd_vel_publishers)

        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.last_time = self.get_clock().now()
        self.is_initialized = True
        self.get_logger().info('Pinky Bringup with Dynamixel has been started successfully.')

    def twist_callback(self, msg: Twist):
        self.last_cmd_time = self.get_clock().now().nanoseconds / 1e9
        linear_x = msg.linear.x
        angular_z = msg.angular.z

        v_l = linear_x - (angular_z * self.wheel_separation / 2.0)
        v_r = linear_x + (angular_z * self.wheel_separation / 2.0)

        wheel_rads_l = v_l / self.wheel_radius
        wheel_rads_r = v_r / self.wheel_radius

        rpm_l = wheel_rads_l * 60.0 / (2 * math.pi)
        rpm_r = -wheel_rads_r * 60.0 / (2 * math.pi)

        max_val = max(abs(rpm_l), abs(rpm_r))
        MAX_RPM = 100.0
        if max_val > MAX_RPM:
            scale = MAX_RPM / max_val
            rpm_l *= scale
            rpm_r *= scale

        if not self.driver.set_double_rpm(rpm_l, rpm_r):
            self.get_logger().warn("Failed to send motor command.")

    def _check_cmd_watchdog(self):
        """명령이 끊기면 모터를 세운다. 한 번만 보내고, 명령이 돌아오면 리셋한다.

        매 tick 정지 명령을 쏘면 시리얼 대역을 잡아먹어 정작 새 명령이 밀린다.
        """
        now = self.get_clock().now().nanoseconds / 1e9
        if not command_expired(now, self.last_cmd_time):
            self.stopped_by_watchdog = False
            return
        if self.stopped_by_watchdog:
            return
        self.stopped_by_watchdog = True
        self.get_logger().warn(
            f"{CMD_VEL_TIMEOUT_SEC}s 동안 {TWIST_SUB_TOPIC_NAME} 명령이 없어 모터를 세웁니다 "
            f"(발행자가 죽었을 수 있습니다)")
        if not self.driver.set_double_rpm(0, 0):
            self.get_logger().error("워치독 정지 실패 — 모터가 계속 돌 수 있습니다")

    def _audit_cmd_vel_publishers(self):
        """`cmd_vel` 발행자가 twist_mux 하나인지 본다. 아니면 **이름을 대고** 경고한다.

        ## 왜 여기인가

        `config/twist_mux.yaml` 이 "Publisher count 가 1(twist_mux)이어야 한다"를 불변식으로
        적어 뒀지만, 지키는 코드가 없어서 **사람이 `ros2 topic info` 를 칠 때만** 드러났다.
        2026-07-30 순회 중 실제로 2개였다(twist_mux + fastapi_ros_bridge). 이 노드는
        `cmd_vel` 의 **유일한 구독자**라 누가 미는지 물어볼 자격이 있는 유일한 자리다.

        ## 왜 죽이지 않고 경고만 하나

        · 남의 노드 발행자는 **없앨 수 없다.** DDS 에 그런 연산이 없다.
        · 죽으면 로봇이 통째로 못 움직인다. 정체도 모르는 항목 하나로 로봇을 못 쓰게
          만드는 건 과잉이다.
        그래서 할 수 있는 최대는 "이름을 대고 시끄럽게 구는 것"이다.

        ## 왜 GID 까지 찍나 — 2026-07-30 에 이걸 몰라서 못 가렸다

        그날 발행자가 2개였는데(`twist_mux` + `fastapi_ros_bridge`) 셋 중 무엇인지 끝내
        못 가렸다. 이름만으로는 구별이 안 되기 때문이다:

          ① 죽은 프로세스의 DDS 잔재 (유령 writer)
          ② `git pull` 전에 떠서 **옛 코드를 들고 있는** 프로세스
             (지금 소스에는 발행자가 없다 — robot_agent/app/core/ros_bridge.py `_cmd_pub`
              은 None 이고 대입하는 곳이 없다)
          ③ 같은 노드 이름을 쓰는 **다른 호스트**의 프로세스
             (aba_fms_service/backend/app/ros_bridge.py 도 노드 이름이 같다. 그쪽은
              도메인 88 이라 119 에서는 안 보여야 하지만, 도메인 설정이 어긋나면 보인다)

        GID 는 참가자마다 유일하다. **두 주기 뒤에도 같은 GID 가 남아 있으면 유령이 아니라
        살아 있는 것**이고, 그러면 그 GID 로 프로세스를 찾으면 된다:

            ros2 daemon stop && ros2 topic info /cmd_vel -v     # GID 재확인
            ss -tunap | grep <포트>                              # 다른 호스트인지

        ## 왜 한 번이 아니라 주기적인가

        나중에 뜬 노드가 끼어드는 경우를 한 번짜리 검사는 못 잡는다. 대신 **목록이 바뀔
        때만** 찍어서, 상태가 그대로면 로그를 더럽히지 않는다.
        """
        pubs = self.get_publishers_info_by_topic(TWIST_SUB_TOPIC_NAME)
        seen = tuple(sorted(
            (f"{p.node_namespace.rstrip('/')}/{p.node_name}",
             bytes(p.endpoint_gid).hex())
            for p in pubs))
        if seen == self._known_cmd_vel_publishers:
            return
        self._known_cmd_vel_publishers = seen

        strays = [f"{name} (gid={gid})" for name, gid in seen
                  if not name.endswith(f"/{EXPECTED_CMD_VEL_PUBLISHER}")]
        if not strays:
            return
        self.get_logger().error(
            f"{TWIST_SUB_TOPIC_NAME} 발행자가 {len(seen)}개다 — 중재를 우회하는 것: "
            f"{'; '.join(strays)} (정상: {EXPECTED_CMD_VEL_PUBLISHER} 하나). "
            f"마지막에 도착한 메시지가 이기므로 누가 바퀴를 돌렸는지 알 수 없다. "
            f"근거: config/twist_mux.yaml")

    def update_and_publish(self):
        self._check_cmd_watchdog()
        current_time = self.get_clock().now()
        dt = (current_time - self.last_time).nanoseconds / 1e9
        if dt <= 0: return

        feedback = self.driver.get_feedback()
        if feedback[0] is None:
            self.get_logger().warn("Failed to read motor data. Skipping update cycle.")
            return
        rpm_l, rpm_r, encoder_l, encoder_r = feedback

        delta_l = encoder_l - self.last_encoder_l
        delta_r = -(encoder_r - self.last_encoder_r)
        
        self.last_encoder_l = encoder_l
        self.last_encoder_r = encoder_r

        dist_l = (delta_l / PULSE_PER_ROT) * self.circumference
        dist_r = (delta_r / PULSE_PER_ROT) * self.circumference

        delta_distance = (dist_r + dist_l) / 2.0
        delta_theta = (dist_r - dist_l) / self.wheel_separation
        
        self.theta += delta_theta
        self.x += delta_distance * math.cos(self.theta)
        self.y += delta_distance * math.sin(self.theta)
        
        v_x = delta_distance / dt if dt > 0 else 0.0
        vth = delta_theta / dt if dt > 0 else 0.0

        self._publish_tf(current_time)
        self._publish_odometry(current_time, v_x, vth)
        self._publish_joint_states(current_time, rpm_l, rpm_r)

        self.last_time = current_time

    def _publish_tf(self, current_time):
        t = TransformStamped()
        t.header.stamp = current_time.to_msg()
        t.header.frame_id = ODOM_FRAME_ID
        t.child_frame_id = ODOM_CHILD_FRAME_ID
        t.transform.translation.x = self.x
        t.transform.translation.y = self.y
        q = quaternion_from_euler(0, 0, self.theta)
        t.transform.rotation.x, t.transform.rotation.y, t.transform.rotation.z, t.transform.rotation.w = q
        self.tf_broadcaster.sendTransform(t)

    def _publish_odometry(self, current_time, v_x, vth):
        odom_msg = Odometry()
        odom_msg.header.stamp = current_time.to_msg()
        odom_msg.header.frame_id = ODOM_FRAME_ID
        odom_msg.child_frame_id = ODOM_CHILD_FRAME_ID
        odom_msg.pose.pose.position.x, odom_msg.pose.pose.position.y = self.x, self.y
        q = quaternion_from_euler(0, 0, self.theta)
        odom_msg.pose.pose.orientation.x, odom_msg.pose.pose.orientation.y, odom_msg.pose.pose.orientation.z, odom_msg.pose.pose.orientation.w = q
        odom_msg.twist.twist.linear.x, odom_msg.twist.twist.angular.z = v_x, vth
        self.odom_pub.publish(odom_msg)

    def _publish_joint_states(self, current_time, rpm_l, rpm_r):
        joint_msg = JointState()
        joint_msg.header.stamp = current_time.to_msg()
        joint_msg.name = [JOINT_NAME_WHEEL_L, JOINT_NAME_WHEEL_R]
        
        pos_l_rad = (self.last_encoder_l / PULSE_PER_ROT) * (2 * math.pi)
        pos_r_rad = (self.last_encoder_r / PULSE_PER_ROT) * (2 * math.pi)
        joint_msg.position = [pos_l_rad, pos_r_rad]
        joint_msg.velocity = [rpm_l * RPM2RAD, rpm_r * RPM2RAD]

        self.joint_pub.publish(joint_msg)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = Pinky()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.driver.terminate()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()