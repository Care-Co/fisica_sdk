from .port_scanner import find_all_com_ports


def scan():
    devices = find_all_com_ports()
    return devices


class ATCommands:
    """
    Fisica Scale AT Command Protocol
    문서 기반으로 완성된 AT 명령어 클래스
    """

    # === 기본 상수 ===
    _STX = 0x24          # 시작 바이트
    _ETX_HIGH = 0xFF     # 종료 바이트 (상위)
    _ETX_LOW = 0xF0      # 종료 바이트 (하위)

    # TYPE 정의
    _SET = 0x53          # "S"et - 설정 명령
    _GET = 0x47          # "G"et - 조회 명령

    # RET 정의 (응답용)
    _RET_FAIL = 0x00     # 실패/오류
    _RET_SUCCESS = 0x01  # 성공

    # === HEADER 정의 (문서 참조) ===

    # 체중 관련 (0x10~0x18)
    _HEADER_WEIGHT_SCALE = 0x10      # 체중 스케일
    _HEADER_WEIGHT_ZERO = 0x11       # 체중 영점 설정
    _HEADER_MIN_WEIGHT = 0x12        # 최소 측정 무게
    _HEADER_WEIGHT_UNIT = 0x13       # 체중 무게 단위
    _HEADER_CURRENT_WEIGHT = 0x14    # 측정 무게 조회
    _HEADER_NORMAL_WEIGHT_CONFIRM = 0x15    # 일반인 무게 확정 Threshold
    _HEADER_NORMAL_WEIGHT_CHANGE = 0x16     # 일반인 무게 변경 Threshold
    _HEADER_PATIENT_WEIGHT_CONFIRM = 0x17   # 환자 무게 확정 Threshold
    _HEADER_PATIENT_WEIGHT_CHANGE = 0x18    # 환자 무게 변경 Threshold

    # 족저압 관련 (0x19~0x23)
    _HEADER_MAX_PRESSURE = 0x19      # 족저압 최대 압력 Threshold
    _HEADER_MEASUREMENT_COUNT = 0x20 # 족저압 측정 횟수
    _HEADER_AUTO_MODE = 0x21         # 자동 모드
    _HEADER_DEVICE_SETTING = 0x22    # 측정 디바이스 설정
    _HEADER_MEASUREMENT_INTERVAL = 0x23  # 족저압 측정 간격

    # 배터리/시스템 관련 (0x30~0x34)
    _HEADER_LOW_BATTERY = 0x30       # Low 배터리 Threshold
    _HEADER_BATTERY_LEVEL = 0x31     # 배터리 잔량 조회
    _HEADER_WAKEUP_SENSITIVITY = 0x32 # Wakeup 민감도 Threshold
    _HEADER_ACTIVE_TIME = 0x33       # 장치 활성화 시간
    _HEADER_UTC_TIMEZONE = 0x34      # UTC Time zone

    # 가변 길이 명령 (0x40~0x43)
    _HEADER_URL_DOMAIN = 0x40        # URL Domain
    _HEADER_AUTO_USER = 0x41         # Auto User
    _HEADER_BLE_NAME = 0x42          # Bluetooth Name
    _HEADER_FIRMWARE_VERSION = 0x43  # Firmware Version

    # WiFi 설정 (0x49)
    _HEADER_WIFI_SETTING = 0x49      # WiFi 설정

    # 족저압 측정 (0x50~0x51)
    _HEADER_SINGLE_MEASUREMENT = 0x50    # 단일(일반) 측정
    _HEADER_CONTINUOUS_MEASUREMENT = 0x51 # 연속 측정

    # OTA 업데이트 (0x60)
    _HEADER_OTA_UPDATE = 0x60        # F/W Update

    # === 디바이스 설정 값 ===
    _DEVICE_STOP = 0x00      # 측정 종료
    _DEVICE_BLE = 0x01       # BLE 모드
    _DEVICE_USB = 0x02       # USB 모드

    # === 측정 제어 값 ===
    _MEASUREMENT_STOP = 0x00    # 측정 중지
    _MEASUREMENT_START = 0x01   # 측정 시작

    # === 무게 단위 ===
    _UNIT_KG = 0x01         # Kg
    _UNIT_LBS = 0x02        # lbs

    # === 자동 모드 ===
    _AUTO_MODE_OFF = 0x00   # 자동 모드 비활성화
    _AUTO_MODE_ON = 0x01    # 자동 모드 활성화

    @classmethod
    def build_command(cls, header, cmd_type, data_high=0x00, data_low=0x00):
        """
        8바이트 고정 길이 AT Command 생성

        Args:
            header: 명령 헤더 (0x10~0x60)
            cmd_type: 명령 타입 (SET=0x53, GET=0x47)
            data_high: 데이터 상위 바이트
            data_low: 데이터 하위 바이트

        Returns:
            list: 8바이트 명령 배열
        """
        return [
            cls._STX,        # STX
            header,         # HEADER
            cmd_type,       # TYPE
            0x00,           # RET (앱에서는 항상 0x00)
            data_high,      # DATA (HIGH)
            data_low,       # DATA (LOW)
            cls._ETX_HIGH,   # ETX (HIGH)
            cls._ETX_LOW     # ETX (LOW)
        ]

    @classmethod
    def build_variable_command(cls, header, cmd_type, data_bytes):
        """
        가변 길이 AT Command 생성 (WiFi 설정, 사용자 정보 등)

        Args:
            header: 명령 헤더
            cmd_type: 명령 타입
            data_bytes: 가변 데이터 바이트 배열

        Returns:
            list: 가변 길이 명령 배열
        """
        command = [
            cls._STX,        # STX
            header,         # HEADER
            cmd_type,       # TYPE
            0x00,           # RET
        ]
        command.extend(data_bytes)  # 가변 데이터 추가
        command.extend([cls._ETX_HIGH, cls._ETX_LOW])  # ETX
        return command

    # === 자주 사용하는 명령들 (프로퍼티로 정의) ===

    # 측정 디바이스 설정
    SET_DEVICE_STOP = [_STX, _HEADER_DEVICE_SETTING, _SET, 0x00, 0x00, _DEVICE_STOP, _ETX_HIGH, _ETX_LOW]
    SET_DEVICE_BLE = [_STX, _HEADER_DEVICE_SETTING, _SET, 0x00, 0x00, _DEVICE_BLE, _ETX_HIGH, _ETX_LOW]
    SET_DEVICE_USB = [_STX, _HEADER_DEVICE_SETTING, _SET, 0x00, 0x00, _DEVICE_USB, _ETX_HIGH, _ETX_LOW]

    # 연속 측정 제어
    START_CONTINUOUS_MEASUREMENT = [_STX, _HEADER_CONTINUOUS_MEASUREMENT, _SET, 0x00, 0x00, _MEASUREMENT_START, _ETX_HIGH, _ETX_LOW]
    STOP_CONTINUOUS_MEASUREMENT = [_STX, _HEADER_CONTINUOUS_MEASUREMENT, _SET, 0x00, 0x00, _MEASUREMENT_STOP, _ETX_HIGH, _ETX_LOW]

    # 자동 모드 제어
    SET_AUTO_MODE_ON = [_STX, _HEADER_AUTO_MODE, _SET, 0x00, 0x00, _AUTO_MODE_ON, _ETX_HIGH, _ETX_LOW]
    SET_AUTO_MODE_OFF = [_STX, _HEADER_AUTO_MODE, _SET, 0x00, 0x00, _AUTO_MODE_OFF, _ETX_HIGH, _ETX_LOW]

    # 조회 명령들
    GET_CURRENT_WEIGHT = [_STX, _HEADER_CURRENT_WEIGHT, _GET, 0x00, 0x00, 0x00, _ETX_HIGH, _ETX_LOW]
    GET_BATTERY_LEVEL = [_STX, _HEADER_BATTERY_LEVEL, _GET, 0x00, 0x00, 0x00, _ETX_HIGH, _ETX_LOW]
    GET_FIRMWARE_VERSION = [_STX, _HEADER_FIRMWARE_VERSION, _GET, 0x00, 0x00, 0x00, _ETX_HIGH, _ETX_LOW]
    GET_AUTO_MODE_STATUS = [_STX, _HEADER_AUTO_MODE, _GET, 0x00, 0x00, 0x00, _ETX_HIGH, _ETX_LOW]

    # 체중 영점 설정
    SET_WEIGHT_ZERO = [_STX, _HEADER_WEIGHT_ZERO, _SET, 0x00, 0x00, 0x00, _ETX_HIGH, _ETX_LOW]
    SET_WEIGHT_SCALE_TEST_ZERO = [_STX, _HEADER_WEIGHT_SCALE, _SET, 0x00, 0xCC, 0x00, _ETX_HIGH, _ETX_LOW]
    SET_WEIGHT_SCALE = [_STX, _HEADER_WEIGHT_SCALE, _SET, 0x00, 0x00, 0x00, _ETX_HIGH, _ETX_LOW]
    SET_WAKEUP = [_STX, _HEADER_WAKEUP_SENSITIVITY, _SET, 0x00, 0x00, 0x00, _ETX_HIGH, _ETX_LOW]


    # === 편의 메서드들 ===

    @classmethod
    def set_min_weight(cls, weight_kg):
        """최소 측정 무게 설정 (Kg 단위, 10배수로 전송)"""
        weight_value = int(weight_kg * 10)
        return cls.build_command(cls._HEADER_MIN_WEIGHT, cls._SET,
                                 (weight_value >> 8) & 0xFF, weight_value & 0xFF)

    @classmethod
    def set_measurement_count(cls, count):
        """족저압 측정 횟수 설정"""
        return cls.build_command(cls._HEADER_MEASUREMENT_COUNT, cls._SET,
                                 (count >> 8) & 0xFF, count & 0xFF)

    @classmethod
    def set_measurement_interval(cls, interval_ms):
        """족저압 측정 간격 설정 (100ms~2000ms)"""
        return cls.build_command(cls._HEADER_MEASUREMENT_INTERVAL, cls._SET,
                                 (interval_ms >> 8) & 0xFF, interval_ms & 0xFF)

    @classmethod
    def set_wifi_credentials(cls, ssid, password):
        """WiFi 설정 (SSID,PASSWORD 형태)"""
        wifi_data = ssid.encode('utf-8') + b',' + password.encode('utf-8')
        return cls.build_variable_command(cls._HEADER_WIFI_SETTING, cls._SET, wifi_data)

    @classmethod
    def start_single_measurement(cls, user_type=1, user_id=1):
        """단일 측정 시작"""
        data_bytes = [
            cls._MEASUREMENT_START,  # CTRL
            user_type,             # UTYPE
            user_id                # UID (1바이트 예시)
        ]
        return cls.build_variable_command(cls._HEADER_SINGLE_MEASUREMENT, cls._SET, data_bytes)

    @classmethod
    def stop_single_measurement(cls):
        """단일 측정 중지"""
        return cls.build_command(cls._HEADER_SINGLE_MEASUREMENT, cls._SET, 0x00, cls._MEASUREMENT_STOP)

    @classmethod
    def create_weight_scale(cls, weight_value):
        if not (-32768 <= weight_value <= 32767):
            raise ValueError(f"Weight value {weight_value} is out of range (-32768 ~ 32767)")

        # Int16을 2바이트로 변환 (Big Endian)
        if weight_value < 0:
            # 음수인 경우 2의 보수로 변환
            weight_value = weight_value + 65536

        high_byte = (weight_value >> 8) & 0xFF # 상위 바이트
        low_byte = weight_value & 0xFF         # 하위 바이트

        return [cls._STX, cls._HEADER_WEIGHT_SCALE, cls._SET, 0x00, high_byte, low_byte, cls._ETX_HIGH, cls._ETX_LOW]


    # === 레거시 호환성 (기존 코드와의 호환) ===

    # V1 명령어들 (문자열 기반)
    SET_MODE1_V1 = 'AT+MODE=1'
    START_MEASUREMENT_V1 = 'AT+AUTO'
    STOP_MEASUREMENT_V1 = 'AT+STOP'

    # V2 명령어들 (기존 코드 호환성)
    SET_BLEMODE_V2 = SET_DEVICE_BLE
    SET_USBMODE_V2 = SET_DEVICE_USB
    START_MEASUREMENT_AUTO_V2 = START_CONTINUOUS_MEASUREMENT
    STOP_MEASUREMENT_V2 = STOP_CONTINUOUS_MEASUREMENT