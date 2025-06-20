from fisica_sdk import FisicaSDK

sdk = FisicaSDK()

devices = sdk.scan_devices()

sdk.connect(devices[0])


# Set metadata
# Start measurement
sdk.start_measurement()
# Measure for 8 seconds
sdk.sleep(8)
# Stop measurement
sdk.stop_measurement()
# Analyze measurement record
report1 = sdk.analyze()
# Print analysis result
print(report1)

# Repeat measurement with same metadata
sdk.start_measurement()
# Measure for 8 seconds
sdk.sleep(8)
# Stop measurement
sdk.stop_measurement()
# Analyze measurement record
sdk.analyze()

# Reset session
sdk.reset_session()
# Set metadata
sdk.set_metadata(name="donggil")
# Start measurement
sdk.start_measurement()
# Measure for 5 seconds
sdk.sleep(5)
# Stop measurement
sdk.stop_measurement()
# Analyze measurement record
sdk.analyze()

# Retrieve all metadata records
all_report=sdk.get_all_reports()
# See get_all_reports for example output

# Export analysis results to file
sdk.export_report(all_report)

# Reset all measurement records
sdk.reset_reports()