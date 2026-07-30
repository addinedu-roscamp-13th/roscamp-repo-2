import os
from os import environ

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, Shutdown
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, Command, TextSubstitution
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
from launch.substitutions import PathJoinSubstitution, PythonExpression

def generate_launch_description():
    ld = LaunchDescription()

    namespace_arg = DeclareLaunchArgument("namespace", default_value="")
    is_sim = DeclareLaunchArgument("is_sim", default_value="false")
    cam_tilt_deg = DeclareLaunchArgument("cam_tilt_deg", default_value="0")

    namespace = PythonExpression([
        "'", LaunchConfiguration('namespace'), "' + ('/' if '", LaunchConfiguration('namespace'), "' != '' else '')"
    ])

    rsp_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        namespace=LaunchConfiguration('namespace'),
        parameters=[{
            'ignore_timestamp': False,
            "use_sim_time": LaunchConfiguration('is_sim'),
            'robot_description':
                Command([
                    'xacro ',
                    PathJoinSubstitution([
                        get_package_share_directory('pinky_description'),
                        'urdf/robot.urdf.xacro',
                    ]),
                    ' namespace:=', namespace,
                    ' is_sim:=', LaunchConfiguration('is_sim'),
                    ' cam_tilt_deg:=', LaunchConfiguration('cam_tilt_deg')
                ]),
            'frame_prefix': [namespace],
        }]
    )

    # [2026-07-28] 실물에서는 **띄우지 않는다** (`is_sim` 일 때만).
    #
    # 실물은 `pinky_bringup/bringup.py` 가 이미 30Hz 로 `/joint_states` 를 낸다
    #   (bringup.py:21 JOINT_PUB_TOPIC_NAME, :92 publisher, :95 30Hz 타이머, :212 발행).
    # `robot_state_publisher` 는 그 토픽을 직접 먹으므로 이 노드가 없어도 TF 는 그대로다.
    #
    # 그런데 아래 설정은 그냥 중복이 아니라 **순환**이었다:
    #   source_list=['joint_states'] 로 `/joint_states` 를 구독하고,
    #   joint_state_publisher 는 결과를 다시 `/joint_states` 로 발행한다.
    #   → 자기가 낸 메시지를 자기가 되받는다. 20Hz 재발행 + 그만큼의 역직렬화가
    #     아무 소득 없이 돈다. 2026-07-28 Pi 실측에서 `joint_state` 프로세스가
    #     코어의 22% 를 쓰고 있었는데(4코어 91% 포화 상황) 이게 유력한 원인이다.
    #
    # ⚠️ 시뮬레이션은 `bringup` 이 없어 `/joint_states` 발행자가 없을 수 있으므로 남긴다.
    #    Nav2 가 쓰는 `base_link → rplidar_link` 는 전부 fixed joint 라
    #    (`pinky.urdf.xacro` base_footprint→base_link / base_link→rplidar_mount→rplidar_link)
    #    joint_states 없이도 `/tf_static` 으로 나온다. 사라지는 건 바퀴·캐스터의
    #    continuous joint TF 뿐이고, 그건 RViz 애니메이션용이다.
    jsp_node = Node(
        package='joint_state_publisher',
        executable='joint_state_publisher',
        name='joint_state_publisher',
        namespace=LaunchConfiguration('namespace'),
        condition=IfCondition(LaunchConfiguration('is_sim')),
        parameters=[{
            "source_list": ['joint_states'],
            "rate": 20.0,
            "use_sim_time": LaunchConfiguration('is_sim'),
        }],
        remappings=[
            ('/robot_descrption', 'robot_descrpition'),
        ],
        output='screen'
    )

    ld.add_action(namespace_arg)
    ld.add_action(is_sim)
    ld.add_action(cam_tilt_deg)
    ld.add_action(rsp_node)
    ld.add_action(jsp_node)

    return ld