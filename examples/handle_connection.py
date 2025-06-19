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

# Sleep main thread (measurement continues)
time.sleep(5)

# Stop measurement
sdk.stop_measurement()

# Disconnect from device.
sdk.disconnect()

# Wait for the completion of measurement, stop, disconnect, or sleep operations.
# Not recommended for use with measurement methods that do not have a specified duration.
sdk.wait()

# Connect to the *th device from the list (replaces previous device)
sdk.connect(device[0])

# Start measurement
sdk.start_measurement()

# Sleep without blocking the main thread
sdk.sleep(10)

# Stop measurement
sdk.stop_measurement()

# Disconnect from device
sdk.disconnect()

# Wait for disconnection (it blocks main thread)
sdk.wait()
