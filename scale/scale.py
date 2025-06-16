# from fisica_sdk.serial_uart import SerialUART
# from fisica_sdk.bluetooth_uart import BluetoothUART
# from fisica_sdk.port_scanner import find_serial_com_ports, find_bluetooth_com_ports, print_all_com_port_details
# from fisica_sdk.serial_handler import SerialHandler
# from queue import Empty
#
# class Scale:
#     def __init__(self, baudrate=115200):
#         self.baudrate = baudrate
#         self.uart = None
#         self.connected=False
#         self.handler = None  # SerialHandler 인스턴스 저장용
#
#     def connect(self):
#         ports = find_serial_com_ports()
#         if ports:
#             print(f"[Scale] 유선 포트 감지: {ports[0]}", flush=True)
#             print_all_com_port_details()
#             self.uart = SerialUART(ports[0], self.baudrate)
#             self.handler = SerialHandler(self.uart.ser)
#         else:
#             bt_ports = find_bluetooth_com_ports()
#             if bt_ports:
#                 print(f"[Scale] 블루투스 포트 감지: {bt_ports[0]}", flush=True)
#                 self.uart = BluetoothUART(self.baudrate)
#                 # 블루투스용 SerialHandler를 별도로 만들어야 함 (현재는 유선만 지원)
#                 self.handler = None
#             else:
#                 raise RuntimeError("유선/블루투스 포트를 찾을 수 없습니다.")
#         self.uart.connect()
#         self.connected=True
#         return self.connected
#
#     def send(self, data, newLine):
#         self.uart.send(data, newLine)
#
#
#     def receive(self):
#         data = self.uart.receive()
#         print(f'[receive] : {data}')
#         # if data:
#         if data is not None and 'entry' in data:
#             self.send("AT+AUTO", newLine=True)
#         if data:
#             return data
#         return None
#
#
#     def stream(self):
#         """자동 수신 처리 시작 (쓰레드)"""
#         if self.handler:
#             self.handler.start_listening()
#             self.send("AT+AUTO",True)
#         else:
#             print("[경고] handler 없음 → stream 불가")
#
#     def stop_stream(self):
#         print("stop stream")
#         if self.handler:
#             self.handler.stop_listening()
#             self.send("AT+STOP", True)
#     def get(self, block=True, timeout=10):
#         if self.handler:
#             try:
#                 return self.handler.data_queue.get(block=block, timeout=timeout)
#             except Empty:
#                 return None
#         return None
#
#
#     def close(self):
#         self.uart.close()
#
#
#
# #    def receive(self):
# #        data = self.uart.receive()
# #        if data:
# #            return self.handler.receive_data_handler(data)
# #        return None
#
# #    def receive(self):
# #        """단일 응답만 처리하고 바로 반환 (연결 과정 등에서 사용)"""
# #        data = self.uart.receive()
# #        if data and self.handler:
# #            return self.handler.receive_data_handler(data)
# #        return None
