import time
from fisica_sdk import FisicaSDK

# Initialize the SDK
sdk = FisicaSDK()

# Find available devices.
device = sdk.scan_devices()

# Connect to the first device from the list.
sdk.connect(device[0])

# Start measurement.
sdk.start_measurement()

# Sleep main thread (measurement continues).
time.sleep(5)

# Stop measurement.
sdk.stop_measurement()

for i in range(3):
    print("The above operations affect the main thread.")
    time.sleep(1)

# Start measurement again.
sdk.start_measurement()

# Sleep without blocking the main thread.
sdk.sleep(5)

# Stop measurement.
sdk.stop_measurement()

for i in range(8):
    print("The above operations do not affect the main thread.")
    time.sleep(1)
