from fisica_sdk import FisicaSDK, Visualizer  # Import SDK and visualization tool

# Define a function to visualize incoming data
def visualize(data):
    image = sdk.render(frame=data,scale=2.0)  # Render image from incoming data
    vis.update(image)         # Update visualizer with new image

# Initialize the SDK
sdk = FisicaSDK()

# Set session metadata
sdk.set_metadata(name="example")

# Find available devices.
device = sdk.scan_devices()

# Connect to the first device from the list.
sdk.connect(device[0])

# Register callback function to visualize data
sdk.on_data(visualize)

# Start measurement.
sdk.start_measurement()

# Sleep without blocking the main thread.
sdk.sleep(15)

# Stop measurement.
sdk.stop_measurement()

# Create visualizer instance
vis = Visualizer()

# Launch visualizer with scaling
vis.run(scale=2.0)
