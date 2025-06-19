import numpy as np
import cv2

from fisica_sdk import FisicaSDK, VisualOptions

sample_grid_data = np.array([  # Define a sample grid data array for rendering
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 254, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 67, 147, 147, 254, 13, 0, 0, 0, 0, 0, 13, 147, 254, 0, 13, 13, 0, 0, 0],
     [0, 0, 13, 147, 0, 0, 120, 201, 0, 0, 0, 0, 0, 67, 147, 0, 0, 0, 93, 0, 13, 0],
     [0, 67, 147, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 201, 0],
     [0, 0, 0, 120, 147, 254, 67, 13, 0, 0, 0, 0, 0, 0, 147, 67, 147, 67, 120, 0, 0, 13],
     [0, 93, 93, 228, 254, 147, 201, 13, 0, 0, 0, 0, 0, 0, 147, 93, 120, 120, 228, 13, 0, 0],
     [0, 93, 13, 147, 254, 228, 120, 40, 0, 0, 0, 0, 0, 0, 93, 228, 40, 201, 201, 228, 174, 0],
     [0, 40, 228, 174, 174, 147, 254, 254, 0, 0, 0, 0, 0, 0, 201, 67, 120, 254, 120, 201, 13, 0],
     [13, 174, 174, 67, 67, 67, 13, 0, 0, 0, 0, 0, 0, 0, 0, 93, 120, 93, 254, 201, 147, 0],
     [0, 93, 254, 67, 13, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 13, 40, 93, 40, 0],
     [0, 13, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 40, 120, 174, 0],
     [0, 0, 13, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 40, 228, 174, 0],
     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 13, 0, 0],
     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 120, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 40, 13, 13, 0, 0, 0, 0, 0, 0, 0, 0, 174, 40, 67, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 40, 228, 254, 0, 0, 0, 0, 0, 0, 93, 254, 174, 120, 174, 0, 0, 0],
     [0, 0, 0, 0, 67, 147, 228, 174, 40, 0, 0, 0, 0, 0, 120, 254, 0, 201, 228, 0, 0, 0],
     [0, 0, 0, 0, 147, 120, 254, 147, 93, 0, 0, 0, 0, 0, 254, 254, 254, 254, 0, 0, 0, 0],
     [0, 0, 0, 0, 13, 147, 201, 147, 67, 0, 0, 0, 0, 0, 201, 254, 13, 201, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 13, 67, 40, 0, 0, 0, 0, 0, 0, 0, 93, 40, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
])

sdk = FisicaSDK()

# Render and display each visualization mode one by one
cv2.imshow("example", sdk.render(grid_data=sample_grid_data, mode=VisualOptions.BINARY))  # Show binary mode
cv2.waitKey()
cv2.imshow("example", sdk.render(grid_data=sample_grid_data, mode=VisualOptions.BINARY_NONZERO))  # Show binary non-zero mode
cv2.waitKey()
cv2.imshow("example", sdk.render(grid_data=sample_grid_data, mode=VisualOptions.BLUR, scale=2))  # Show blurred image with scaling
cv2.waitKey()
cv2.imshow("example", sdk.render(grid_data=sample_grid_data, mode=VisualOptions.PIXEL, scale=2))  # Show pixel visualization
cv2.waitKey()
cv2.imshow("example", sdk.render(grid_data=sample_grid_data, mode=VisualOptions.BBOX, scale=2))  # Show bounding boxes
cv2.waitKey()
cv2.imshow("example", sdk.render(grid_data=sample_grid_data, mode=VisualOptions.CONTOUR, scale=2))  # Show contour visualization
cv2.waitKey()

# Render all visualization modes at once and display them in sequence
images = sdk.render(grid_data=sample_grid_data, mode=VisualOptions.ALL, scale=2)  # Render all visualizations
for image in images:
    cv2.imshow("example", image)  # Display each rendered image
    cv2.waitKey()
cv2.destroyAllWindows()  # Close all OpenCV windows