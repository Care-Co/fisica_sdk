from fisica_sdk import FisicaSDK

# Define a list to collect incoming data
datas = []

# Define a callback function to handle incoming data
def test(data):
    datas.append(data)  # Append received data to the datas list

# Initialize the SDK
sdk = FisicaSDK()

# Set session metadata
sdk.set_metadata(name="example")

# Find available devices.
device = sdk.scan_devices()

# Connect to the first device from the list.
sdk.connect(device[0])

# Register callback function
sdk.on_data(test)

# Start measurement again.
sdk.start_measurement()

# Sleep without blocking the main thread.
sdk.sleep(15)

# Stop measurement.
sdk.stop_measurement()

# Wait for any asynchronous cleanup to complete
sdk.wait()

# Print all collected data
print(datas)