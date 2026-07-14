#!/usr/bin/env python3
"""센서 직접 읽기 (pinkylib) — root 권한으로 실행.

Usage:
  sudo python3 sensor_ctrl.py ultrasonic
  sudo python3 sensor_ctrl.py battery
  sudo python3 sensor_ctrl.py ir
  sudo python3 sensor_ctrl.py imu

Output: JSON to stdout
"""
import json
import sys
import time


def main() -> None:
    sensor = sys.argv[1] if len(sys.argv) > 1 else ""

    if sensor == "ultrasonic":
        from pinkylib import Ultrasonic
        us = Ultrasonic()
        dist_m = us.get_dist()
        print(json.dumps({
            "sensor": "ultrasonic",
            "distance_m": round(float(dist_m), 4),
            "distance_cm": round(float(dist_m) * 100, 1),
        }))

    elif sensor == "battery":
        from pinkylib import Battery
        bat = Battery()
        voltage = bat.get_voltage()
        pct = bat.battery_percentage()
        print(json.dumps({
            "sensor": "battery",
            "voltage": round(float(voltage), 2),
            "percentage": round(float(pct), 1),
        }))
        bat.close()

    elif sensor == "ir":
        from pinkylib import IR
        ir = IR()
        ir_1, ir_2, ir_3 = ir.read_ir()
        print(json.dumps({
            "sensor": "ir",
            "left": int(ir_1),
            "center": int(ir_2),
            "right": int(ir_3),
            "obstacle": bool(ir_1 or ir_2 or ir_3),
        }))
        ir.close()

    elif sensor == "imu":
        from pinkylib import IMU
        imu = IMU()
        data = imu.read_imu_data()
        print(json.dumps({
            "sensor": "imu",
            "acc": [round(float(v), 3) for v in data.get("acc", [])],
            "mag": [round(float(v), 3) for v in data.get("mag", [])],
            "gyro": [round(float(v), 3) for v in data.get("gyro", [])],
            "euler": [round(float(v), 3) for v in data.get("euler", [])],
        }))
        imu.close()

    elif sensor == "daemon":
        # 상주 모드: 초음파+IR 을 한 번만 초기화하고 ~0.1초마다 JSON 한 줄씩 출력.
        # 서버가 명령마다 sudo python3 를 띄우지 않도록 하여 센서 응답을 3.2초 → ~0.1초로 줄인다.
        from pinkylib import Ultrasonic, IR
        us = Ultrasonic()
        ir = IR()
        try:
            while True:
                rec = {"sensor": "combo", "ts": round(time.time(), 3)}
                try:
                    dist_m = float(us.get_dist())
                    rec["distance_m"] = round(dist_m, 4)
                    rec["distance_cm"] = round(dist_m * 100, 1)
                except Exception as exc:
                    rec["us_error"] = str(exc)
                try:
                    ir_1, ir_2, ir_3 = ir.read_ir()
                    rec["left"] = int(ir_1)
                    rec["center"] = int(ir_2)
                    rec["right"] = int(ir_3)
                except Exception as exc:
                    rec["ir_error"] = str(exc)
                print(json.dumps(rec), flush=True)
                time.sleep(0.1)
        finally:
            try:
                ir.close()
            except Exception:
                pass

    else:
        print(json.dumps({"error": f"unknown sensor: {sensor}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
