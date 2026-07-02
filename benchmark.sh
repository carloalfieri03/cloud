#!/bin/bash
# benchmark.sh - Test a Lambda function at different memory sizes

# --- CONFIGURATION ---
FUNCTION_NAME="lambdamstipregofunz-resize"
PAYLOAD_FILE="payload_small.json"
ITERATIONS=20
MEMORY_SIZES=(128 256 512 1024 1769)

# Check if the payload file exists; if not, create a default dummy file
if [ ! -f "$PAYLOAD_FILE" ]; then
    echo '{"test": "benchmark"}' > "$PAYLOAD_FILE"
    echo "⚠️ Payload file '$PAYLOAD_FILE' not found. Created a default file."
fi

echo "========================================================================"
echo "Memory(MB),ColdStart(ms),AvgDuration(ms),MaxMemUsed(MB),EstCostPerInvoke"
echo "========================================================================"

for MEM in "${MEMORY_SIZES[@]}"; do
    # 1. Update function memory size
    aws lambda update-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --memory-size $MEM > /dev/null 2>&1

    # 2. Wait for configuration change to propagate completely
    aws lambda wait function-updated --function-name "$FUNCTION_NAME"

    # 3. First invocation triggers a Cold Start (parse Init Duration from this run)
    COLD_RUN=$(aws lambda invoke --function-name "$FUNCTION_NAME" \
        --payload fileb://"$PAYLOAD_FILE" \
        --cli-binary-format raw-in-base64-out \
        --log-type Tail \
        --query "LogResult" --output text /dev/null)

    COLD_REPORT=$(echo "$COLD_RUN" | base64 -d | grep "REPORT")
    
    # Extract Init Duration (the cold start duration) if present
    INIT_DURATION=$(echo "$COLD_REPORT" | awk '{for (i=1; i<=NF; i++) if ($i=="Init" && $(i+1)=="Duration:") {print $(i+2); exit}}')
    if [ -z "$INIT_DURATION" ]; then
        INIT_DURATION="0.00" 
    fi

    TOTAL_DURATION=0
    MAX_MEM=0

    # 4. Loop for subsequent warm invocations
    for i in $(seq 1 $ITERATIONS); do
        RESULT=$(aws lambda invoke --function-name "$FUNCTION_NAME" \
            --payload fileb://"$PAYLOAD_FILE" \
            --cli-binary-format raw-in-base64-out \
            --log-type Tail \
            --query "LogResult" --output text /dev/null)

        # Decode and parse execution report lines
        REPORT=$(echo "$RESULT" | base64 -d | grep "REPORT")
        DURATION=$(echo "$REPORT" | awk '{for (i=1; i<=NF; i++) if ($i=="Duration:") {print $(i+1); exit}}')
        MEM_USED=$(echo "$REPORT" | awk '{for (i=1; i<=NF; i++) if ($i=="Max" && $(i+2)=="Used:") {print $(i+3); exit}}')

        # Fixed: Replaced bc with awk for decimal summation
        TOTAL_DURATION=$(awk "BEGIN {print $TOTAL_DURATION + $DURATION}")

        if [ "$MEM_USED" -gt "$MAX_MEM" ]; then
            MAX_MEM=$MEM_USED
        fi
    done

    # 5. Calculations (Fixed: Replaced bc with awk formatting strings)
    AVG_DURATION=$(awk "BEGIN {printf \"%.2f\", $TOTAL_DURATION / $ITERATIONS}")
    COST=$(awk "BEGIN {printf \"%.10f\", ($MEM / 1024) * ($AVG_DURATION / 1000) * 0.0000166667}")

    echo "$MEM,$INIT_DURATION,$AVG_DURATION,$MAX_MEM,$COST"
done

echo "========================================================================"
echo "🔍 Fetching historical cold start log entries from CloudWatch..."
echo "========================================================================"

# Filter CloudWatch logs for specific initialization events across your function
aws logs filter-log-events \
  --log-group-name "/aws/lambda/$FUNCTION_NAME" \
  --filter-pattern "Init Duration" \
  --limit 20 \
  --query "events[].message" \
  --output text