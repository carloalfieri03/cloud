import os
import json
import boto3
import cv2 
import numpy as np
import urllib.parse
import logging
import time

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LOCAL_TEST = os.environ.get("LOCAL_TEST", "false").lower() == "true"
LOCAL_OUTPUT_DIR = os.environ.get("LOCAL_OUTPUT_DIR", "/tmp/output")

MODEL_DIR = os.path.dirname(os.path.abspath(__file__))
PROTOTXT_PATH = os.path.join(MODEL_DIR, os.environ.get("MODEL_PROTOTXT", "MobileNetSSD_deploy.prototxt"))
WEIGHTS_PATH = os.path.join(MODEL_DIR, os.environ.get("MODEL_WEIGHTS", "MobileNetSSD_deploy.caffemodel"))

# read the network model from the Caffe prototxt and weights files
NET = cv2.dnn.readNetFromCaffe(PROTOTXT_PATH, WEIGHTS_PATH)

CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse",
    "motorbike", "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor",
]

DETECTION_CONFIDENCE_THRESHOLD = 0.4

SMALL_THRESHOLD_BYTES = 350 * 1024
MEDIUM_THRESHOLD_BYTES = 5000 * 1024
# BIG_THRESHOLD_BYTES= 5000 * 1024 ## just for info 

OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET")

# open the S3 and CloudWatch clients once, to avoid re-opening them on every invocation
if not LOCAL_TEST:
    s3 = boto3.client("s3")
    cloudwatch = boto3.client("cloudwatch")
else:
    s3 = None
    cloudwatch = None

def classify_image_size(image_bytes):
    if image_bytes < SMALL_THRESHOLD_BYTES:
        return "small"
    elif image_bytes < MEDIUM_THRESHOLD_BYTES:
        return "medium"
    else:
        return "large"

def process_image_local(local_path,operation="all"):
    """
    Process an image from a local file path (no S3).
    Writes results to LOCAL_OUTPUT_DIR.
    """
    start = time.perf_counter()
    
    # Read the image from disk
    with open(local_path, "rb") as f:
        content = f.read()
    
    image_array = np.frombuffer(content, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not decode image: {local_path}")
    
    original_bytes = os.path.getsize(local_path)
    size_category = classify_image_size(original_bytes)
    base_name = os.path.splitext(os.path.basename(local_path))[0]

    os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
    detections = []

    # Step 2 & 3: Run only the requested operation (Mirrors S3 logic)
    if operation == "resize" or operation == "all":
        resized_image = resize_image(image)
        cv2.imwrite(os.path.join(LOCAL_OUTPUT_DIR, f"{base_name}_resized.jpg"), resized_image)

    # Added support for both spellings just to be safe
    if operation in ["greyscale", "grayscale", "all"]:
        grayscale_image = convert_to_grayscale(image)
        cv2.imwrite(os.path.join(LOCAL_OUTPUT_DIR, f"{base_name}_grayscale.jpg"), grayscale_image)

    if operation == "detect" or operation == "all":
        annotated_image, detections = detect_objects(image)
        cv2.imwrite(os.path.join(LOCAL_OUTPUT_DIR, f"{base_name}_detected.jpg"), annotated_image)
        
        with open(os.path.join(LOCAL_OUTPUT_DIR, f"{base_name}_detections.json"), "w") as f:
            json.dump({
                "source_path": local_path,
                "size_category": size_category,
                "original_bytes": original_bytes,
                "detections": detections
            }, f, indent=4)
    
    duration_ms = (time.perf_counter() - start) * 1000
    # In local test, you may optionally emit the metric or just log it
    logger.info(f"Processed local file {local_path} with operation '{operation}' in {duration_ms:.2f} ms")
    
    return {
        "key": local_path,
        "operation": operation,
        "size_category": size_category,
        "duration_ms": round(duration_ms, 1),
        "detections_count": len(detections)
    }
    
def download_image_from_s3(bucket_name, object_key):
    response = s3.get_object(Bucket=bucket_name, Key=object_key)
    content =  response['Body'].read()
    image_array = np.frombuffer(content, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    return image, len(content)

def upload_image_to_s3(bucket_name, object_key, image, ext=".jpg"):
    success, encoded = cv2.imencode(ext, image)
    if not success:
        raise ValueError(f"Failed to encode image for {object_key}")
    s3.put_object(Bucket=bucket_name, Key=object_key, Body=encoded.tobytes(), ContentType='image/jpeg')

def resize_image(image):
    (h, w) = image.shape[:2]
    
    # compute the new dimensions (50% smaller)
    new_dim = (int(w * 0.5), int(h * 0.5))

    return cv2.resize(image, new_dim, interpolation=cv2.INTER_AREA) # INTER_AREA minimizes the distortion while downscaling

def convert_to_grayscale(image):
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

def detect_objects(image):
    """
    Runs MobileNet-SSD object detection and returns an annotated copy of the image plus a list of detections (label, confidence, box).
    """
    # image shape: (height × width × channels) 
    (h, w) = image.shape[:2]

    # MobileNet requires images to exactly 300x300, and scaled by 1/127.5 with mean subtraction of 127.5. The swapRB=True flag is needed because OpenCV loads images in BGR format by default, while the model expects RGB.
    blob = cv2.dnn.blobFromImage(cv2.resize(image, (300, 300)), scalefactor=1/127.5, size=(300, 300), mean = (127.5, 127.5, 127.5), swapRB=True)
    
    # blob object is passed as input to the object
    NET.setInput(blob)
    # network description
    detections = NET.forward()

    # create a copy of the image to draw the rectangles and labels on it so we do not ruin the original image
    annotated_image = image.copy()
    results = []

    for i in range(detections.shape[2]):
        confidence = float(detections[0, 0, i, 2])

        if confidence < DETECTION_CONFIDENCE_THRESHOLD:
            continue

        # get the class label
        idx = int(detections[0, 0, i, 1])
        label = CLASSES[idx]

        # compute the (x, y)-coordinates of the bounding box for the object
        # The NN outputs bouding box coordinates in normalized format (values between 0 and 1 relative to the image dimensions), so we need to multiply by the original width and height to get pixel coordinates.
        box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
        (startX, startY, endX, endY) = box.astype("int")

        # Draw the bounding box and label on the annotated image
        cv2.rectangle(annotated_image, (startX, startY), (endX, endY), (0, 255, 0), 2) # (0, 255, 0) specifies the color of the rectangle in BGR format (green), and 2 is the thickness of the rectangle
        text = f"{label}: {confidence:.2f}"
        y = startY - 15 if startY - 15 > 15 else startY + 15
        cv2.putText(annotated_image, text, (startX, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # save the raw data to be sent to S3 as a json file
        results.append({
            "label": label,
            "confidence": round(confidence, 4),
            "box": [int(startX), int(startY), int(endX), int(endY)]
        })

    return annotated_image, results

def cloudwatch_put_metric(size_category, operation, duration_ms, detection_count):
        
    if cloudwatch is None:
        return
    """
    Emits a custom CloudWatch metric with the image size category as a dimension.
    """
    try: 
        cloudwatch.put_metric_data(
            Namespace="ImageProcessingPipeline",
            MetricData=[
                {
                    "MetricName": "ProcessingDurationMs",
                    "Dimensions": [
                        {"Name": "SizeCategory", "Value": size_category},
                        {"Name": "Operation", "Value": operation}
                    ],
                    "Value": duration_ms,
                    "Unit": "Milliseconds"
                },
                {
                    "MetricName": "DetectionCount", # how many objects were detected in the image
                    "Dimensions": [
                        {"Name": "SizeCategory", "Value": size_category},
                        {"Name": "Operation", "Value": operation}
                    ],
                    "Value": detection_count,
                    "Unit": "Count"
                }
            ]
        )
    except Exception as e:
        logger.exception(f"Failed to emit CloudWatch metric: {e}")

def process_image(bucket, key):
    """
    Runs the three processing steps (resize, grayscale, object detection) and returns the final annotated image and the list of detections.
    """

    start = time.perf_counter() # start the timer to measure processing duration

    # Step 1: Download the image and classify its size
    image, original_bytes = download_image_from_s3(bucket, key)
    if image is None:
        raise ValueError(f"Could not decode image: s3://{bucket}/{key}")
    
    size_category = classify_image_size(original_bytes)
    base_name = os.path.splitext(os.path.basename(key))[0]

    # Example of path = run-2026/resize/small/00001_dog.jpg
    path_components = key.split('/')
    operation = path_components[1] if len(path_components) > 3 else "all"

    detections = []

    # Step 2 & 3: run only the requested operation
    if operation == "resize" or operation == "all":
        resized_image = resize_image(image)
        upload_image_to_s3(OUTPUT_BUCKET, f"{base_name}_resized.jpg", resized_image)

    if operation == "greyscale" or operation == "all":
        greyscale_image = convert_to_grayscale(image)
        upload_image_to_s3(OUTPUT_BUCKET, f"{base_name}_greyscale.jpg", greyscale_image)

    if operation == "detect" or operation == "all":
        annotated_image, detections = detect_objects(image)
        upload_image_to_s3(OUTPUT_BUCKET, f"{base_name}_detected.jpg", annotated_image)
        s3.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=f"{base_name}_detections.json",
            Body=json.dumps({
                "source_key": key,
                "size_category": size_category,
                "original_bytes": original_bytes,
                "detections": detections
            }),
            ContentType='application/json'
        )

    # Step 4: Emit the custom CloudWatch metric
    duration_ms = (time.perf_counter() - start) * 1000
    cloudwatch_put_metric(size_category, operation, duration_ms, len(detections))

    logger.info(f"Processed s3://{bucket}/{key} with operation '{operation}' in {duration_ms:.2f} ms, size category: {size_category}, detections: {len(detections)}")

    return {
        "key": key,
        "operation": operation,
        "size_category": size_category,
        "duration_ms": round(duration_ms, 1),
        "detections": len(detections)
    }

def handler(event, context):
    results = []

    # 1. Local test
    if LOCAL_TEST and "local_path" in event:
        # Extract the specific operation from the JSON, default to "all" if missing
        requested_operation = event.get("operation", "all")
        results.append(process_image_local(event["local_path"], requested_operation))
        return {"status": "success", "processed": results}
        
    # 2. Standard AWS S3 execution
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        try:
            results.append(process_image(bucket, key))
        except Exception as e:
            logger.exception(f"Error processing s3://{bucket}/{key}: {e}")
    
    return {"status": "success", "processed": results}