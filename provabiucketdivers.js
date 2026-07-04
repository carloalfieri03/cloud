const { S3Client, CopyObjectCommand } = require("@aws-sdk/client-s3");
const s3 = new S3Client({ region: process.env.AWS_REGION || "eu-east-1" });

const SOURCE_BUCKET = "artillery-detect-521011614244-us-east-1-an";

// Array containing your three microservice target buckets
const TARGET_BUCKETS = [
  "lambdamstipregofunz-ms-detect-in-521011614244",
  "lambdamstipregofunz-ms-resize-in-521011614244",
  "lambdamstipregofunz-ms-grayscale-in-521011614244"
];

async function uploadImage(userContext, events) {
  // Aligned perfectly with your CSV mapping fields configuration
  const sourceKey = userContext.vars.image_key;
  
  // Safety check to handle missing data row inputs gracefully
  if (!sourceKey) {
    console.error("CRITICAL: image_key is missing! Current variables available:", JSON.stringify(userContext.vars));
    events.emit('counter', 'csv_data_missing_error', 1);
    return ;
  }

  // Randomly distribute across the target bucket array infrastructure
  const randomBucketIndex = Math.floor(Math.random() * TARGET_BUCKETS.length);
  const targetBucket = TARGET_BUCKETS[randomBucketIndex];
  
  const uniqueId = Math.random().toString(36).substring(7);
  const extension = sourceKey.split('.').pop(); 
  const targetKey = `incoming/${uniqueId}.${extension}`;

  try {
    const command = new CopyObjectCommand({
      CopySource: `${SOURCE_BUCKET}/${sourceKey}`,
      Bucket: targetBucket,
      Key: targetKey,
    });
    
    await s3.send(command);
    events.emit('counter', 's3_uploads_success', 1);
  } catch (error) {
    console.error(`AWS S3 Copy Error for key ${sourceKey} to ${targetBucket}:`, error);
    events.emit('counter', 's3_uploads_error', 1);
  }
  
  return ;
}

module.exports = { uploadImage };