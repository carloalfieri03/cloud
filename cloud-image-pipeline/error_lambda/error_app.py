import os
import json
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET")

def error_feedback_handler(event, context):
    """
    Triggered by the SQS On-Failure Queue.
    Parses the failed event payload, extracts the original S3 key,
    and uploads an error notification JSON to the output bucket.
    """
    for record in event.get("Records", []):
        try:
            # 1. Parse the SQS body to get the Lambda destination failure payload
            sqs_body = json.loads(record["body"])
            
            # Lambda Destinations wraps the original invocation payload inside 'requestPayload'
            request_payload = sqs_body.get("requestPayload", {})
            s3_records = request_payload.get("Records", [])
            
            for s3_record in s3_records:
                bucket_name = s3_record["s3"]["bucket"]["name"]
                original_key = s3_record["s3"]["object"]["key"]
                
                # Get the base name without the original extension (e.g., "cat" from "cat.jpg")
                base_name = os.path.splitext(os.path.basename(original_key))[0]
                
                # Define the target feedback file key in the output bucket
                error_json_key = f"{base_name}_detections.json"
                
                error_message = {
                    "source_key": original_key,
                    "status": "failed",
                    "error_message": "There was an error processing your file. Please check file integrity or retry later."
                }
                
                # 2. Upload the error feedback JSON directly to the output S3 bucket
                logger.info(f"Uploading failure feedback JSON to S3 for key: {error_json_key}")
                s3.put_object(
                    Bucket=OUTPUT_BUCKET,
                    Key=error_json_key,
                    Body=json.dumps(error_message, indent=4),
                    ContentType="application/json"
                )
                
        except Exception as e:
            logger.exception(f"Failed to process failure feedback for record: {record}")
            # Raising the exception ensures the message stays in SQS if this function fails
            raise e

    return {"status": "feedback_processed"}