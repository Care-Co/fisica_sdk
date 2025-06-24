from fisica_sdk import FisicaSDK

sdk = FisicaSDK(debug=10)
# print(FisicaSDK.__doc__)
# print(FisicaSDK.start_measurement.__doc__)

devices = sdk.scan_devices()
# serial_device = next((device for device in devices if device.get('type') == "bluetooth"), None)
# if serial_device:
#     sdk.connect(serial_device)
print(devices)
if devices:
    sdk.connect(devices[0])
# # Set metadata
# # Start measurement
sdk.start_measurement()

# # Measure for 8 seconds
sdk.sleep(5)
#
# # Stop measurement
sdk.stop_measurement()

sdk.wait()
#
# # # Wait for the completion of measurement, stop, disconnect, or sleep operations.
# # # Not recommended for use with measurement methods that do not have a specified duration.
# # sdk.wait()
# #
# # # Analyze measurement record
# # report1 = sdk.analyze()
# #
# # # Print analysis result
# # print(report1)
# #
# # # Repeat measurement with same metadata
# # sdk.start_measurement()
# #
# # # Measure for 8 seconds
# # sdk.sleep(8)
# #
# # # Stop measurement
# # sdk.stop_measurement()
#
# # Wait for disconnection (it blocks main thread)
# sdk.wait()
#
# # Analyze measurement record
# sdk.analyze()
#
# all_report=sdk.get_all_reports()
#
# sdk.export_report(all_report)
#
