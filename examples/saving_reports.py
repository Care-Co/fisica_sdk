from fisica_sdk import FisicaSDK

# Initialize the SDK
sdk = FisicaSDK()

# Set session metadata
sdk.set_metadata(name="example")

# Find available devices.
device = sdk.scan_devices()

# Connect to the first device from the list.
sdk.connect(device[0])

# Start measurement.
sdk.start_measurement()

# Sleep without blocking the main thread.
sdk.sleep(15)

# Stop measurement.
sdk.stop_measurement()

# Wait for the completion of measurement, stop, disconnect, or sleep operations.
# It blocks main thread.
sdk.wait()

# Perform analysis and return the resulting report
report = sdk.analyze()

# Most recent report
second_report = sdk.get_report()

# Get all reports
all_report=sdk.get_all_reports()

# Save the report. If the filename is not specified, it will be generated based on the current time.
# If format is not specified, JSON is used by default.
# If report is not specified, the most recent report will be saved.
# You can pass either a single report or a list of reports (List[SessionReport]) as the report argument.
sdk.export_report(report=all_report, format="json", filename="2025_session")
