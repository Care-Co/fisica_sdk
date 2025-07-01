import fisica_sdk as fisica

# Initialize and connect
sdk = fisica.FisicaSDK()
devices = sdk.scan_devices()

if devices:
    sdk.connect(devices[0])

    # Step 1: Set zero point (empty platform)
    print("Please ensure platform is empty...")
    input("Press Enter when ready...")
    sdk.set_zero()
    print("Zero point calibrated.")

    # Step 2: Set scale calibration parameter
    # Use appropriate calibration value for your device
    # (consult device documentation for recommended values)
    calibration_value = 12600  # Example value within -32768 to 32767 range
    sdk.set_scale(calibration_value)
    print(f"Scale calibration set to: {calibration_value}")

    # Step 3: Verify calibration with known reference weight
    print("Place a known reference weight on platform...")
    reference_weight = float(input("Enter reference weight (kg): "))

    # Take a measurement to verify accuracy
    sdk.start_measurement(duration=10)
    sdk.wait()
    report = sdk.analyze()
    measured_weight = report.weight

    print(f"Reference: {reference_weight}kg, Measured: {measured_weight}kg")
    print(f"Accuracy: {abs(measured_weight - reference_weight):.3f}kg difference")

    # Step 4: Ready for accurate measurements
    print("Calibration complete. Ready for measurements.")
    sdk.disconnect()