from fisica_sdk import FisicaSDK, Visualizer, VisualOptions

def on_frame_received(frame):
    """Callback function for real-time data"""
    print(f"Received frame at {frame.timestamp}")

    # Render visualization
    image = sdk.render(frame, mode=VisualOptions.BBOX)

    # Update GUI (if running)
    vis.update(image)

# Setup SDK
sdk = FisicaSDK()
vis = Visualizer()
devices = sdk.scan_devices()

if devices:
    sdk.connect(devices[0])

    # Register callback
    sdk.on_data(on_frame_received)

    # Start measurement
    sdk.start_measurement(duration=30)

    # Start GUI viewer
    vis.run(scale=1.5)

    sdk.wait()
