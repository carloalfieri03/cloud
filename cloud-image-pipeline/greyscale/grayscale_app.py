import os
import boto3 
import urllib.parse
import logging
import time
import io
from PIL import Image

logger = logging.getLogger()
logger.setLevel(logging.INFO)

LOCAL_TEST = os.environ.get("LOCAL_TEST", "false").lower() == "true"
LOCAL_OUTPUT_DIR = os.environ.get("LOCAL_OUTPUT_DIR", "/tmp/output")


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
        except Exception:
            logger.exception("Error in grayscale_handler")
            
    return {"status": "success", "operation": "grayscale", "processed": results}