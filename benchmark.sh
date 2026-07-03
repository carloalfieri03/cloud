#!/bin/bash
# benchmark.sh - Test a Lambda function at different memory sizes

# --- CONFIGURATION ---
FUNCTION_NAME="lambdamstipregofunz-resize"
PAYLOAD_FILE="payload_large.json"
ITERATIONS=20
MEMORY_SIZES=(128 256 512 1024 1769)

if [ ! -f "$PAYLOAD_FILE" ]; then
    echo '{"test": "benchmark"}' > "$PAYLOAD_FILE"
    echo "⚠️ Payload file '$PAYLOAD_FILE' not found. Created a default file." >&2
fi

# Informational headers go to STDERR (>&2) so they don't break the CSV
echo "========================================================================" >&2
echo "🚀 Running Benchmark... (CSV data is being saved silently)" >&2
echo "========================================================================" >&2

# This is the ONLY text that goes to STDOUT (the CSV file)
echo "Memory(MB),ColdStart(ms),AvgDuration(ms),MaxMemUsed(MB),EstCostPerInvoke"

for MEM in "${MEMORY_SIZES[@]}"; do
    aws lambda update-function-configuration \
        --function-name "$FUNCTION_NAME" \
        --memory-size $MEM > /dev/null 2>&1

    aws lambda wait function-updated --function-name "$FUNCTION_NAME"

    COLD_RUN=$(aws lambda invoke --function-name "$FUNCTION_NAME" \
        --payload fileb://"$PAYLOAD_FILE" \
        --cli-binary-format raw-in-base64-out \
        --cli-read-timeout 300 \
        --log-type Tail \
        --query "LogResult" --output text /dev/null 2>/dev/null)

    COLD_REPORT=$(echo "$COLD_RUN" | base64 -d | grep "REPORT")
    
    INIT_DURATION=$(echo "$COLD_REPORT" | awk '{for (i=1; i<=NF; i++) if ($i=="Init" && $(i+1)=="Duration:") {print $(i+2); exit}}')
    if [ -z "$INIT_DURATION" ]; then INIT_DURATION="0.00"; fi

    TOTAL_DURATION=0
    MAX_MEM=0
    VALID_RUNS=0

    for i in $(seq 1 $ITERATIONS); do
        RESULT=$(aws lambda invoke --function-name "$FUNCTION_NAME" \
            --payload fileb://"$PAYLOAD_FILE" \
            --cli-binary-format raw-in-base64-out \
            --cli-read-timeout 300 \
            --log-type Tail \
            --query "LogResult" --output text /dev/null 2>/dev/null)

        REPORT=$(echo "$RESULT" | base64 -d | grep "REPORT")
        DURATION=$(echo "$REPORT" | awk '{for (i=1; i<=NF; i++) if ($i=="Duration:") {print $(i+1); exit}}')
        MEM_USED=$(echo "$REPORT" | awk '{for (i=1; i<=NF; i++) if ($i=="Max" && $(i+2)=="Used:") {print $(i+3); exit}}')

        # FIX 3: Safety net. Only do math if DURATION and MEM_USED actually exist
        if [ -n "$DURATION" ] && [ -n "$MEM_USED" ]; then
            TOTAL_DURATION=$(awk "BEGIN {print $TOTAL_DURATION + $DURATION}")
            VALID_RUNS=$((VALID_RUNS + 1))

            if [ "$MEM_USED" -gt "$MAX_MEM" ]; then
                MAX_MEM=$MEM_USED
            fi
        fi
    done

    # FIX 4: Prevent dividing by zero if all runs failed
    if [ "$VALID_RUNS" -gt 0 ]; then
        AVG_DURATION=$(awk "BEGIN {printf \"%.2f\", $TOTAL_DURATION / $VALID_RUNS}")
        COST=$(awk "BEGIN {printf \"%.10f\", ($MEM / 1024) * ($AVG_DURATION / 1000) * 0.0000166667}")
        echo "$MEM,$INIT_DURATION,$AVG_DURATION,$MAX_MEM,$COST"
    else
        echo "$MEM,FAILED,FAILED,FAILED,FAILED" >&2
    fi
done

# Trailing info and logs go to STDERR (>&2)
echo "========================================================================" >&2
echo "🔍 Fetching historical cold start log entries from CloudWatch..." >&2
echo "========================================================================" >&2

aws logs filter-log-events \
  --log-group-name "/aws/lambda/$FUNCTION_NAME" \
  --filter-pattern "Init Duration" \
  --limit 20 \
  --query "events[].message" \
  --output text >&2