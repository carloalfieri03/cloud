import os
import json
import boto3 
import cv2 
import numpy as np
import urllib.parse
import logging
import time
import signal

logger = logging.getLogger()
logger.setLevel(logging.INFO)
IS_COLD_START = True

LOCAL_TEST = os.environ.get("LOCAL_TEST", "false").lower() == "true"
LOCAL_OUTPUT_DIR = os.environ.get("LOCAL_OUTPUT_DIR", "/tmp/output")

# New for EC2: which bucket this worker polls, and how often
# must be created manually in the Linux terminal of the EC2 instance before running the script, e.g.:
# export INPUT_BUCKET=my-input-bucket
INPUT_BUCKET = os.environ.get("INPUT_BUCKET")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "5")) # how often to re-check S3 for new objects
MAX_KEYS_PER_POLL = int(os.environ.get("MAX_KEYS_PER_POLL", "50"))

SMALL_THRESHOLD_BYTES = 350 * 1024
MEDIUM_THRESHOLD_BYTES = 5000 * 1024
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET")
DETECTION_CONFIDENCE_THRESHOLD = 0.4 

CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse",
    "motorbike", "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor",
]

MODEL_DIR = os.path.dirname(os.path.abspath(__file__))
PROTOTXT_PATH = os.path.join(MODEL_DIR, os.environ.get("MODEL_PROTOTXT", "MobileNetSSD_deploy.prototxt"))
WEIGHTS_PATH = os.path.join(MODEL_DIR, os.environ.get("MODEL_WEIGHTS", "MobileNetSSD_deploy.caffemodel"))
NET = None
if not LOCAL_TEST:
    s3 = boto3.client("s3")
    cloudwatch = boto3.client("cloudwatch")
else:
    s3 = None
    cloudwatch = None

# open the S3 and CloudWatch clients once, to avoid re-opening them on every invocation

def get_net():
    global NET
    if NET is None:
        logger.info("Loading the MobileNet-SSD model...")
        NET = cv2.dnn.readNet(PROTOTXT_PATH, WEIGHTS_PATH,"Caffe")
    
    return NET

def classify_image_size(image_bytes):

    if image_bytes < SMALL_THRESHOLD_BYTES:
        return "small"
    elif image_bytes < MEDIUM_THRESHOLD_BYTES:
        return "medium"
    else:
        return "large"
    
def download_image_from_s3(bucket_name, object_key):
  
    response = s3.get_object(Bucket=bucket_name, Key=object_key)
    content = response['Body'].read()
    # ADD THIS: Prevent 0-byte Artillery artifacts from crashing OpenCV
    if not content or len(content) == 0:
        raise ValueError(f"S3 object {object_key} is completely empty (0 bytes).")
        
    
    image_array = np.frombuffer(content, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"OpenCV failed to decode image data for {object_key}.")
    
    return image, len(content)
    

def upload_image_to_s3(bucket_name, object_key, image, ext=".jpg"):
 
    success, encoded = cv2.imencode(ext, image)
    if not success:
        raise ValueError(f"Failed to encode image for {object_key}")
    s3.put_object(
        Bucket=bucket_name, 
        Key=object_key, 
        Body=encoded.tobytes(), 
        ContentType='image/jpeg'
    )


def detect_objects(image):
    """
    returns an annotated copy of the image plus a list of detections (label, confidence, box).
    """
    #logger.info(cv2.getBuildInformation())
    # image shape: (height × width × channels) 
    (h, w) = image.shape[:2]
    net = get_net()

    # MobileNet requires images to exactly 300x300, and scaled by 1/127.5 with mean subtraction of 127.5. The swapRB=True flag is needed because OpenCV loads images in BGR format by default, while the model expects RGB.
    blob = cv2.dnn.blobFromImage(cv2.resize(image, (300, 300)), scalefactor=1/127.5, size=(300, 300), mean = (127.5, 127.5, 127.5), swapRB=True)
    
    # blob object is passed as input to the object
    net.setInput(blob)
    # network description
    detections = net.forward()

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


def cloudwatch_put_metric(size_category, operation, duration_ms, detection_count,is_cold):
    if cloudwatch is None:
        return
    cold_start_str = "True" if is_cold else "False"
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
                        {"Name": "Operation", "Value": operation},
                        {"Name": "IsColdStart", "Value": cold_start_str}
                    ],
                    "Value": duration_ms,
                    "Unit": "Milliseconds"
                },
                {
                    "MetricName": "DetectionCount", # how many objects were detected in the image
                    "Dimensions": [
                        {"Name": "SizeCategory", "Value": size_category}
                    ],
                    "Value": detection_count,
                    "Unit": "Count"
                }
            ]
        )
    except Exception as e:
        logger.exception(f"Failed to emit CloudWatch metric: {e}")

def core_process(record):
    bucket = record['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(record['s3']['object']['key'])
    # ADD THIS: Instantly ignore anything that isn't a jpg or png
    valid_extensions = ('.jpg', '.jpeg', '.png')
    if not key.lower().endswith(valid_extensions):
        logger.warning(f"Ignoring non-image file from load test: {key}")
        return 0
    base_name = os.path.splitext(os.path.basename(key))[0]
        
    
    global IS_COLD_START
    
    # Capture the state for the current invocation
    current_invocation_is_cold = IS_COLD_START
    
    # Immediately flip the flag to False so subsequent warm hits skip this
    if IS_COLD_START:
        IS_COLD_START = False
        logger.info("This invocation is running inside a newly initialized container (Cold Start).")

    # ==========================================
    # FAILURE INJECTION TESTING SECTION
    # ==========================================
    
    # 1. Simulate an explicit code crash
    if "simulate-crash" in key:
        logger.warning(f"!!! Chaos Test Triggered: Simulating a code crash for file: {key} !!!")
        raise ValueError("Simulated processing crash for system testing")

    # 2. Simulate a function timeout
    if "simulate-timeout" in key:
        logger.warning(f"!!! Chaos Test Triggered: Simulating a hard timeout for file: {key} !!!")
        logger.warning("The execution will now freeze until AWS terminates the container.")
        time.sleep(70) # Exceeds maximum possible Lambda timeout (15 mins)

    # ==========================================

    start = time.perf_counter()
    image, original_bytes = download_image_from_s3(bucket, key)
    size_category = classify_image_size(original_bytes)
    detections_count = 0 ## credo si possa togliere

    processed_image, detections = detect_objects(image)
    
    # Upload annotated image
    upload_image_to_s3(OUTPUT_BUCKET, f"{base_name}_detections.jpg", processed_image)

    # Upload JSON metadata
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
    detections_count = len(detections)
    duration_ms = (time.perf_counter() - start) * 1000
    
    cloudwatch_put_metric(size_category, "detect", duration_ms, detections_count,current_invocation_is_cold)
    logger.info(f" Detection finished: {key} in {duration_ms:.2f} ms")
    return 0

def process_image_local(local_path):
    """
    Process an image from a local file path (no S3).
    Writes results to LOCAL_OUTPUT_DIR.
    """
    start = time.perf_counter()
    
    image = cv2.imread(local_path)
    if image is None:
        raise ValueError(f"Could not decode image: {local_path}")

    original_bytes = os.path.getsize(local_path)
    size_category = classify_image_size(original_bytes)
    base_name = os.path.splitext(os.path.basename(local_path))[0]
    
    # Run the model
    annotated_image, detections = detect_objects(image)

    os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
    # Save the annotated image locally
    image_save_path = os.path.join(LOCAL_OUTPUT_DIR, f"{base_name}_detected.jpg")
    cv2.imwrite(image_save_path, annotated_image)
    
    # Save the JSON locally so you can inspect the bounding box coordinates
    json_save_path = os.path.join(LOCAL_OUTPUT_DIR, f"{base_name}_detections.json")
    with open(json_save_path, "w") as f:
        json.dump({
            "source_key": local_path,
            "detections": detections
        }, f, indent=4)
    
    duration_ms = (time.perf_counter() - start) * 1000
    # In local test, you may optionally emit the metric or just log it
    logger.info(f"Processed local file {local_path} in {duration_ms:.2f} ms. Found {len(detections)} objects.")

    
    return {
        "key": local_path,
        "size_category": size_category,
        "detections_count": len(detections),
        "duration_ms": round(duration_ms, 1)
    }

def detection_handler(event, context):
    results = []

    # Intercept local testing payloads
    if LOCAL_TEST and "local_path" in event:
        results.append(process_image_local(event["local_path"]))
        return {"status": "success", "operation": "detect_local", "processed": results}
    
    for record in event.get('Records', []):
        try:
            results.append(core_process(record))
        except Exception as e:
            logger.exception(f"Error in detection_handler processing record: {record}")
            # CRITICAL: Re-raise the exact exception 'e' so the AWS Lambda service 
            # recognizes this invocation as a system failure.
            raise e

    return {"status": "success", "operation": "detect", "processed": results}
