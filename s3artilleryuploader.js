const { S3Client, CopyObjectCommand } = require("@aws-sdk/client-s3");
const s3 = new S3Client({ region: process.env.AWS_REGION || "us-east-1" });

const SOURCE_BUCKET = "artillery-detect-521011614244-us-east-1-an";
// Prefix where images actually reside in the source S3 bucket
const SOURCE_PREFIX = "Sample_images/";

// Array containing your microservice target buckets
const TARGET_BUCKETS = [
  "lambdamstipregofunz-ms-detect-in-521011614244"
  // Add your second and third bucket names here when ready
];

async function uploadImage(userContext, events) {
 // 1. Pull the relative path from the CSV row (e.g., "all_test_images/000000000139.jpg")
  const csvKey = userContext.vars.image_key;
  
  if (!csvKey) {
    console.error("CRITICAL: image_key is missing! Current variables:", JSON.stringify(userContext.vars));
    events.emit('counter', 'csv_data_missing_error', 1);
    return;
  }

  // 2. Combine the prefix and the CSV key to create the absolute S3 object path
  const sourceKey = `${SOURCE_PREFIX}${csvKey}`;

  // 3. Select a target bucket
  const randomBucketIndex = Math.floor(Math.random() * TARGET_BUCKETS.length);
  const targetBucket = TARGET_BUCKETS[randomBucketIndex];
  
  // 4. Generate unique target name properties
  const uniqueId = Math.random().toString(36).substring(7);
 
  // Keep the original extension but give it a unique name
  const extension = sourceKey.split('.').pop(); 
  const targetKey = `incoming/${uniqueId}.${extension}`;

  try {
    const command = new CopyObjectCommand({
    // CopySource resolves to: "artillery-detect-...-an/cloud-image-pipeline/DataV2/all_test_images/000000000139.jpg"
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