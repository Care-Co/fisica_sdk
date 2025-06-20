import time

from fisica_sdk import FisicaSDK

# Initialize the SDK
sdk = FisicaSDK(debug=10)

# Set session metadata
sdk.set_metadata(name="example")

# Find available devices.
device = sdk.scan_devices()

# Connect to the first device from the list.
sdk.connect(device[0])

# Start measurement.
# sdk.start_measurement(5)
sdk.start_measurement()

# time.sleep(5)
# Sleep without blocking the main thread.
sdk.sleep(8)

# Stop measurement.
sdk.stop_measurement()
sdk.wait()

# Perform analysis and return the resulting report
report = sdk.analyze()
print(report)

# Most recent report
current_report = sdk.get_report()
print(current_report)

# Get all reports
all_report=sdk.get_all_reports()

# Save the report. If the filename is not specified, it will be generated based on the current time.
# 포맷은 json 타입으로 출력됩니다.
# If report is not specified, the most recent report will be saved.
# You can pass either a single report or a list of reports (List[SessionReport]) as the report argument.
sdk.export_report(report=all_report, output="2025_session")
