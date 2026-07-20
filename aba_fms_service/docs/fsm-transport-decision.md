# FSM 전이 요청 전송 방식 결정

## 배경

INSTRUCTION.md 2단계는 전이 요청을 ROS2 **서비스**로 정의하라고 지시한다
("요청/응답이 필요하므로 토픽 부적합"). 그러나 FMS(도메인 86)와 미션 PC(별도 도메인)는
`ros2 domain_bridge`로만 연결된다.

## 조사 결과 (ROS2 Jazzy, domain_bridge 0.5.0)

### 1. YAML 스키마에 `services` 키가 없다

`/opt/ros/jazzy/include/domain_bridge/parse_domain_bridge_yaml_config.hpp`가 문서화한
최상위 허용 키는 `name`, `from_domain`, `to_domain`, `topics` 뿐이다.

```
41: * - name: Name of the domain bridge
42: * - from_domain: The default 'from_domain' used for bridged topics.
44: * - to_domain: The default 'to_domain' used for bridged topics
46: * - topics: A map of topic names to a map of topic bridge information
```

`services` 를 언급하는 줄이 없다.

### 2. 서비스 브릿지는 C++ 컴파일 타임 템플릿 API 로만 존재한다

`/opt/ros/jazzy/include/domain_bridge/domain_bridge.hpp:144`

```cpp
  template<typename ServiceT>
  void bridge_service(
    const std::string & service,
    size_t from_domain_id,
    size_t to_domain_id,
    const ServiceBridgeOptions & options = ServiceBridgeOptions());
```

서비스 타입이 **컴파일 타임 템플릿 인자**다. 타입을 런타임 YAML 로 받는
`ros2 run domain_bridge domain_bridge <yaml>` 실행 파일로는 임의 타입을 인스턴스화할 수 없다.

### 3. 실측 — `services:` 블록은 조용히 무시된다

```yaml
name: probe
from_domain: 91
to_domain: 92
services:
  request_transition:
    type: std_srvs/srv/Trigger
```

```
$ timeout 8 ros2 run domain_bridge domain_bridge /tmp/bridge_service_probe.yaml
Terminated                       # 파싱 에러 없이 정상 기동, timeout 이 종료시킴 (exit 143)

$ ROS_DOMAIN_ID=92 ros2 service list | grep -c request_transition
0                                # 중계되지 않음
```

파싱 에러조차 나지 않는다 — 인식하지 못하는 키라 그냥 버려진다. 잘못 설정해도 조용히
동작하지 않으므로 더 위험하다.

### 4. 레포의 기존 설정도 전부 토픽 전용이다

`aba_fms_service/config/domain_bridge_pinky{1,2,3}.yaml` — 전부 `topics:` 만 사용한다.

## 결정

- `.srv` 계약 파일은 **그대로 작성**한다. `libi_modes`는 미션 PC에서 실제 서비스 서버를
  띄우므로 **같은 도메인 안**(rqt, `ros2 service call`, 온보드 디버깅)에서는 지시대로 동작한다.
- **도메인을 넘는 FMS → 미션 PC 구간만** 상관관계 ID 기반 요청/응답 토픽으로 처리한다.
  이 패턴은 이 레포에서 이미 운영 중이다 —
  `aba_fms_service/backend/app/fleet_telemetry.py:160 send_command()`
  (`{"id","ts","action","args"}` 발행 → `threading.Event` 대기 → 결과 토픽 수신,
  **같은 domain_bridge 를 통과함**).

즉 INSTRUCTION.md 의 **의도**(요청/응답 시맨틱, 지정된 요청/응답 필드)는 지키고,
전송 계층만 실제로 가능한 방식으로 바꾼다.

## 대안 (채택하지 않음)

`bridge_service<RequestTransition>()` 를 호출하는 전용 C++ 노드를 새로 작성하면 진짜 서비스
중계가 가능하다. 새 ament_cmake 패키지 + C++ 빌드가 추가되는데, 이미 검증된 토픽 패턴이
같은 결과를 주므로 비용 대비 이득이 없다. 나중에 서비스 중계가 꼭 필요해지면 이 문서를
근거로 재검토한다.
