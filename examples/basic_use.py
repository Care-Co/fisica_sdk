import fisica_sdk as fisica

# Initialize SDK
sdk = fisica.FisicaSDK()

# Scan for devices
devices = sdk.scan_devices()
print("Available devices:", devices)

# Connect to the first device
if devices:
    sdk.connect(devices[0])

    # Set metadata
    sdk.set_metadata(name="John Doe")

    # Start measurement for 10 seconds
    sdk.start_measurement(duration=10)

    # Wait for completion
    sdk.wait()

    # Analyze results
    report = sdk.analyze()
    print("Analysis complete:", report)

    # Disconnect
    sdk.disconnect()

    # Wait for completion
    sdk.wait()