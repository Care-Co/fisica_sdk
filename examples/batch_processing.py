import fisica_sdk as fisica

sdk = fisica.FisicaSDK()
devices = sdk.scan_devices()

if devices:
    sdk.connect(devices[0])

    # Multiple measurements
    for i in range(5):
        sdk.set_metadata(id=i, name=f"Test_{i}")
        sdk.start_measurement(duration=5)
        sdk.wait()
        sdk.analyze()
        # Get session data
        frames = sdk.get_session_frames()
        print(f"Session {i}: {len(frames)} frames captured")

        # Reset for next measurement
        sdk.reset_session()

    # Export all reports
    reports = sdk.get_all_reports()
    sdk.export_report(reports, output="measurement_results.json")
    sdk.disconnect()