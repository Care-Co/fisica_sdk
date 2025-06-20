from fisica_sdk import FisicaSDK

sdk = FisicaSDK(debug=10)

devices = sdk.scan_devices()

sdk.connect(devices[0])


# Set metadata
# Start measurement
sdk.start_measurement()

# Measure for 8 seconds
sdk.sleep(8)

# Stop measurement
sdk.stop_measurement()

# Wait for the completion of measurement, stop, disconnect, or sleep operations.
# Not recommended for use with measurement methods that do not have a specified duration.
sdk.wait()

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

# Wait for disconnection (it blocks main thread)
sdk.wait()

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

# Wait for disconnection (it blocks main thread)
sdk.wait()

# Analyze measurement record
sdk.analyze()

# Retrieve all metadata records
all_report=sdk.get_all_reports()

# Export analysis results to file
sdk.export_report(all_report)

# Reset all measurement records
sdk.reset_reports()