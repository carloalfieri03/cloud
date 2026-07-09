/// env variables and paths and set-ups
const { S3Client, CopyObjectCommand } = require("@aws-sdk/client-s3");
const fs = require('fs');
const path = require('path');

const s3 = new S3Client({ region: process.env.AWS_REGION || "us-east-1" });
const SOURCE_BUCKET = "artillery-detect-521011614244-us-east-1-an";



const TARGET_BUCKETS = [
  "lambdamstipregofunz-ms-detect-in-521011614244",
  "lambdamstipregofunz-ms-resize-in-521011614244",
  "lambdamstipregofunz-ms-grayscale-in-521011614244"
];
/// 

/// reading the csv and mapping it in lines.foEach is reading lines and then creates traffic marix

const csvPath = path.join(__dirname, 'dati_resampled.csv');
const csvContent = fs.readFileSync(csvPath, 'utf-8');
const lines = csvContent.split(/\r?\n/).map(line => line.trim()).filter(line => line.length > 0);
lines.shift(); 

const groupedFiles = {
    'small': { 'easy': [], 'medium': [], 'hard': [] },
    'medium': { 'easy': [], 'medium': [], 'hard': [] },
    'big': { 'easy': [], 'medium': [], 'hard': [] }
};

lines.forEach(line => {
    const [size_category,image_key,difficulty] = line.split(',');
    if (groupedFiles[size_category] && groupedFiles[size_category][difficulty]) {
        groupedFiles[size_category][difficulty].push(image_key);
    }
});


const trafficMatrix = {
     [TARGET_BUCKETS[0]]: { 
        allocation: 0.4, 
        combinations: [
            { size: 'big', complexity: 'hard', weight: 0.5 },     
            { size: 'small', complexity: 'medium', weight: 0.4 }, 
            { size: 'medium', complexity: 'easy', weight: 0.1 }   
        ]
    },
    [TARGET_BUCKETS[1]]: { 
        allocation: 0.3, 
        combinations: [
            { size: 'big', complexity: 'hard', weight: 0.5 },     
            { size: 'small', complexity: 'medium', weight: 0.4 }, 
            { size: 'medium', complexity: 'easy', weight: 0.1 }  
        ]
    },
    [TARGET_BUCKETS[2]]: { 
        allocation: 0.3, 
        combinations: [
            { size: 'big', complexity: 'hard', weight: 0.5 },     
            { size: 'small', complexity: 'medium', weight: 0.4 }, 
            { size: 'medium', complexity: 'easy', weight: 0.1 }  
        ]
    }
}; /// bucket distribution and file types

function findWeightedItem(options, weightKey) {
    let rand = Math.random(); // random number between 0-1
    let cumulative = 0; 
    for (const option of options) {
        cumulative += option[weightKey];
        if (rand <= cumulative) return option; // se il numero random è più piccolo del treshold allora ritorna quella option
    }
    return options[0];
}

// NOTICE: This is now an async function
async function uploadImage(context, ee) {
    const bucketOptions = Object.keys(trafficMatrix).map(key => ({
        name: key,
        allocation: trafficMatrix[key].allocation
    }));
    const targetBucket = findWeightedItem(bucketOptions, 'allocation').name;
    const targetBucketConfig = trafficMatrix[targetBucket];
    const selectedCombination = findWeightedItem(targetBucketConfig.combinations, 'weight');
    
    const candidateKeys = groupedFiles[selectedCombination.size][selectedCombination.complexity];
    const targetKey = candidateKeys && candidateKeys.length > 0 
        ? candidateKeys[Math.floor(Math.random() * candidateKeys.length)] 
        : "fallback-default-file.txt";

    const sourcePath = `final images/${targetKey}`;

    // NATIVE AWS SDK EXECUTION
    const params = {
        Bucket: targetBucket,
        // AWS requires CopySource to be URL encoded just in case your filenames have spaces
        CopySource: encodeURI(`${SOURCE_BUCKET}/${sourcePath}`), 
        Key: targetKey
    };

    const startTime = Date.now();
    try {
        await s3.send(new CopyObjectCommand(params));
        const latency = Date.now() - startTime;
        
        // Push custom success and latency metrics to Artillery's report.json
        ee.emit('counter', 's3_copy.success', 1);
        ee.emit('histogram', 's3_copy.latency_ms', latency);
    } catch (error) {
        // Push custom error metrics
        ee.emit('counter', 's3_copy.error', 1);
        console.error(`S3 Copy Failed for ${targetKey}:`, error.message);
        const errorMessage = `Failed Key: ${targetKey} | Reason: ${error.message}\n`;
        fs.appendFileSync('failed_keys.log', errorMessage, 'utf8');
    }

    return ;
}

module.exports = {
    uploadImage
};