import smbus2
import time

class Battery:
    DEFAULT_I2C_BUS = 1
    DEFAULT_I2C_ADDRESS = 0x08

    REG_BATTERY = 0xF8

    def __init__(self, i2c_bus=DEFAULT_I2C_BUS, i2c_address=DEFAULT_I2C_ADDRESS, wait_time=0.001):
        self.i2c_address = i2c_address
        self.bus = None
        self.wait_time = wait_time
        try:
            self.bus = smbus2.SMBus(i2c_bus)
        except FileNotFoundError:
            print(f"Error: I2C 버스 {i2c_bus}를 찾을 수 없습니다.")
            print("I2C가 활성화되어 있는지 확인하세요")
            raise

    def _read_adc_channel(self, register_cmd):
        if not self.bus:
            raise IOError("I2C 버스가 초기화되지 않았습니다.")
        
        try:
            self.bus.write_byte(self.i2c_address, register_cmd)
            time.sleep(self.wait_time) # ADC 변환 대기
            data = self.bus.read_i2c_block_data(self.i2c_address, 0, 2)
            return (data[0] << 4) | (data[1] >> 4)
        except OSError as e:
            print(f"I2C 통신 오류 (주소: 0x{self.i2c_address:02x}): {e}")
            return None

    def get_voltage(self):
        readings = []
        for _ in range(20):  # 10번 읽기
            adc_val = self._read_adc_channel(self.REG_BATTERY)
            if adc_val is not None:
                readings.append(adc_val)
    
        if not readings:  # 유효한 값이 하나도 없으면 None 반환
            return None
    
        avg_adc_val = sum(readings) / len(readings)
    
        voltage_divider_ratio = (13.0 / 28.0)
        voltage = (avg_adc_val / 4096.0) * 4.096 / voltage_divider_ratio
        return voltage

    def battery_percentage(self, voltage=None):
        """전압 → 퍼센트. `voltage` 를 주면 그 값을 쓰고, 안 주면 새로 읽는다.

        ⚠️ [2026-07-30] `voltage` 인자를 받게 바꿨다. 예전에는 무조건 `get_voltage()` 를
        **다시** 불렀고, 그래서 `battery/voltage` 와 `battery/percent` 가 **서로 다른 I2C
        읽기**에서 나왔다. 모터 부하로 전압이 오르내리는 구간에서는 두 토픽이 어긋난다
        (실측: 로그가 6.55V 인데 percent 는 25.4% = 약 7.0V 에 해당).
        호출측이 한 번 읽어 넘기면 두 값이 같은 표본에서 나온다.

        None(I2C 실패)이면 None 을 돌려준다 — 예전에는 `None - 6.8` 로 TypeError 가 났다.
        """
        # [2026-07-30] empty 를 6.8 → 6.5 로 내렸다.
        #
        # 왜: 6.8 은 PinkyPro 보일러플레이트에서 물려받은 값이고(09c9b7f, 07-14) 이 레포에
        # 근거가 없었다. 그런데 실측 전압이 **전 구간 6.8V 이하**(5.88~6.78V)라 percent 가
        # 계속 0% 로 클램프됐다 — 아직 주행이 되는 배터리인데 게이지는 0% 였다.
        # 6.5V = 3.25V/셀 로, 리튬 손상 기준(3.0V/셀)보다 위라 팩을 보호하는 바닥이다.
        #
        # [2026-07-30 저녁] full 7.6 → **7.8** (사용자 지정). 경계는 6.5 ~ 7.8 이다.
        #
        # 예전 주석이 지적한 "충전 직후 100% 에 오래 머문다"를 7.6→7.8 이 일부 덜어 준다.
        # 측정 회로 상한은 여전히 8.82V(= 4.096V × 28/13, get_voltage 의 분압비)라
        # 2S 리튬(만충 8.4V)이면 8.4→7.8 구간은 아직 100% 로 뭉친다. 팩 화학·셀 수가
        # 확인되면 8.4 로 맞춰라. 확인 방법: 완충 후 **정지 상태**에서 `/battery/voltage`
        # 를 한 번 읽으면 그 값이 곧 만충 전압이다.
        #
        # 🚨 **이 변경은 자동 복귀 임계를 건드린다 — 실물에서 확인할 것.**
        #    퍼센트 = (V - 6.5) / (full - 6.5) × 100 이므로 full 을 올리면 같은 전압이
        #    **더 낮은 퍼센트**로 나온다. 2026-07-30 실측 팩(6.68~6.75V) 기준:
        #
        #        V       full=7.6      full=7.8
        #      6.68        16.4%         13.8%
        #      6.75        22.7%         19.2%
        #      6.80        27.3%         23.1%
        #
        #    `aba_fms_service/backend/app/fsm_model.py:104-106` 이 `battery <= 15%` 에서
        #    PATROL/IDLE → RETURNING 자동 복귀를 건다. 즉 **이 팩은 순회 중 임계를 밟을 수
        #    있다.** 사표대(voltage_filter, 0.03V ≈ 2.3%p)가 떨림은 막지만 가로지르는 것은
        #    못 막는다. 로봇이 순회 중 갑자기 복귀하면 여기부터 의심하라.
        full_voltage = 7.8   # 100%
        empty_voltage = 6.5  # 0%  (3.25V/셀 — 리튬 컷오프 3.0V/셀보다 위)

        if voltage is None:
            voltage = self.get_voltage()
        if voltage is None:
            return None

        percent = (voltage - empty_voltage) / (full_voltage - empty_voltage) * 100
        percent = max(0, min(100, percent))  # 0~100% 제한
        return round(percent, 2)

    def close(self):
        if self.bus:
            self.bus.close()