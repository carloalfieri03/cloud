import os
import boto3 
import urllib.parse
import logging
import time
import io
from PIL import Image
import signal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

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

if not LOCAL_TEST:
    s3 = boto3.client("s3")
    cloudwatch = boto3.client("cloudwatch")
else:
    s3 = None
    cloudwatch = None

# open the S3 and CloudWatch clients once, to avoid re-opening them on every invocation


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

    # PIL uses io.BytesIO to read byte streams directly
    image = Image.open(io.BytesIO(content))
    
    # Best practice: convert to RGB to strip alpha channels (PNGs) before saving as JPEG
    if image.mode != "RGB" and image.mode != "RGBA":
        image = image.convert("RGB")
        
    return image, len(content)

def upload_image_to_s3(bucket_name, object_key, image, ext="JPEG"):
    # Create an empty byte buffer in memory
    buffer = io.BytesIO()
    
    # Save the PIL image into that buffer as a JPEG
    image.save(buffer, format=ext)
    
    s3.put_object(
        Bucket=bucket_name, 
        Key=object_key, 
        Body=buffer.getvalue(), 
        ContentType='image/jpeg'
    )


def convert_to_grayscale(image):
    return image.convert("L")

def cloudwatch_put_metric(size_category, operation, duration_ms):
  
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
                }
            ]
        )
    except Exception as e:
        logger.exception(f"Failed to emit CloudWatch metric: {e}")

def core_process(record):
    bucket = record['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(record['s3']['object']['key'])
    base_name = os.path.splitext(os.path.basename(key))[0]
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
    
       
    processed_image = convert_to_grayscale(image)   
    upload_image_to_s3(OUTPUT_BUCKET, f"{base_name}_grayscale.jpg", processed_image)
    

    duration_ms = (time.perf_counter() - start) * 1000
    cloudwatch_put_metric(size_category, "grayscale", duration_ms)
    logger.info(f"Grayscale finished: {key} in {duration_ms:.2f} ms")
  
    return 0

def process_image_local(local_path):
    """
    Process an image from a local file path (no S3).
    Writes results to LOCAL_OUTPUT_DIR.
    """
    start = time.perf_counter()
    
    # Pillow can open files directly from the local path
    image = Image.open(local_path)
    if image.mode != "RGB":
        image = image.convert("RGB")

    original_bytes = os.path.getsize(local_path)
    size_category = classify_image_size(original_bytes)
    base_name = os.path.splitext(os.path.basename(local_path))[0]
    
    # Processing steps  must be identical to normal pipeline
    grayscale_image = convert_to_grayscale(image)

    # Write outputs locally instead of S3
    os.makedirs(LOCAL_OUTPUT_DIR, exist_ok=True)
    save_path = os.path.join(LOCAL_OUTPUT_DIR, f"{base_name}_greyscale.jpg")

    # Pillow saves directly to the file system
    grayscale_image.save(save_path, format="JPEG")
    
    duration_ms = (time.perf_counter() - start) * 1000
    # In local test, you may optionally emit the metric or just log it
    logger.info(f"Processed local file {local_path} in {duration_ms:.2f} ms, "
                f"size category: {size_category}")
    
    
    return {
        "key": local_path,
        "size_category": size_category,
        "duration_ms": round(duration_ms, 1)
    }

def grayscale_handler(event, context):

    results = []
   # Local test mode
    if LOCAL_TEST and "local_path" in event:
        results.append(process_image_local(event["local_path"]))
        return {"status": "success", "operation": "grayscale_local", "processed": results}
    
    for record in event.get('Records', []):
        try:
            results.append(core_process(record))
        except Exception as e:
            logger.exception(f"Error in grayscale_handler processing record: {record}")
            # CRITICAL: Re-raise the exact exception 'e' so the AWS Lambda service 
            # recognizes this invocation as a system failure.
            raise e
    return {"status": "success", "operation": "grayscale", "processed": results}

### --- NEW CODE FOR THE EC2 WORKER (PROPOSAL 4) BELOW --- ###

_shutdown_requested = False # Ctrl+C

def handle_shutdown_signal(sign, frame):
    global _shutdown_requested
    logger.info(f"Shutdown signal received: {sign}. Will exit after current poll cycle.")
    _shutdown_requested = True

signal.signal(signal.SIGTERM, handle_shutdown_signal) # ask the process to terminate gracefully
signal.signal(signal.SIGINT, handle_shutdown_signal)

# to reuse the same core_process() function, we need to build a fake S3 event record for each object found in the input bucket
def build_fake_s3_record(bucket, key):
    return {"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}

def run_worker():
    if not INPUT_BUCKET:
        raise SystemExit("INPUT_BUCKET env var is required for the EC2 worker mode.")
    
    logger.info(
        f"Starting EC2 resize worker. INPUT_BUCKET={INPUT_BUCKET} "
        f"OUTPUT_BUCKET={OUTPUT_BUCKET} POLL_INTERVAL={POLL_INTERVAL_SECONDS}s"
    )

    while not _shutdown_requested: # infinite loop, until Ctrl+C or SIGTERM is received
        try:
            # every 5s (or whatever POLL_INTERVAL_SECONDS is set to), list objects in the input bucket
            response = s3.list_objects_v2(Bucket=INPUT_BUCKET, MaxKeys=MAX_KEYS_PER_POLL)
            for obj in response.get("Contents", []):
                key = obj["Key"]
                record = build_fake_s3_record(INPUT_BUCKET, key)
                try:
                    core_process(record)
                    s3.delete_object(Bucket=INPUT_BUCKET, Key=key)
                except Exception:
                    logger.exception(f"Failed to process {key} - leaving it in the bucket to retry next poll")
        except Exception:
            logger.exception("Error while polling S3 - will retry next cycle")

        time.sleep(POLL_INTERVAL_SECONDS)

    logger.info("Worker stopped cleanly.")

if __name__ == "__main__":
    run_worker()