# Proposal 4 — EC2 Deployment Instructions 

Purpose of this document: get ONE worker (start with resize) running on ONE EC2 instance in the sandbox. Once this works, repeat the exact same steps for greyscale, detect, and the monolith.

only ONE EC2 instance since the script is stopped before starting the next one, thus you can reuse the 't3.small' instance type for all the tests.

---

## Step 0 — Start your Lab session

Click "Start Lab" as usual, wait for "Lab status: ready", open the AWS
Management Console tab. Keep the instructions tab open too — the terminal
at the top of it already has AWS CLI configured with your session's
credentials, and you'll use it for Step 1.

---

## Step 1 — Create the S3 buckets (run in the terminal at the top of the lab page)

```bash
aws s3 mb s3://YOURNAME-resize-input --region us-east-1
aws s3 mb s3://YOURNAME-greyscale-input --region us-east-1
aws s3 mb s3://YOURNAME-detect-input --region us-east-1
aws s3 mb s3://YOURNAME-output --region us-east-1

aws s3 mb s3://YOURNAME-monolith-input --region us-east-1
```

Replace YOURNAME with something unique to you (bucket names are global
across all of AWS, not just your account).

---

## Step 2 — Launch the EC2 instance (in the AWS Console tab)

1. Search "EC2" in the top search bar → **Launch instance**
2. Name: `resize-worker`
3. AMI: leave the default **Amazon Linux 2023**
4. Instance type: **t3.small** (allowed in your sandbox, has enough RAM)
5. Key pair: select **vockey** (already exists, don't create a new one)
6. Scroll to **Advanced details** → find **IAM instance profile** → select
   **LabInstanceProfile**.
   **This step is not optional.** Without it, the script will run but every single S3/CloudWatch call will fail with an AccessDenied error, because this is what grants the instance permission to talk to AWS services — the EC2 equivalent of what `Role: LabRole` did for your Lambda functions.
7. Click **Launch instance**. Wait ~30 seconds for it to show "Running".

---

## Step 3 — Connect to the instance (browser terminal, no SSH key needed)

1. EC2 Console → **Instances** → check the box next to `resize-worker`
2. Click **Connect** (top of the page)
3. Click the **Session Manager** tab → **Connect**

A black terminal window opens directly in your browser. This only works
because of the IAM instance profile you attached in Step 2 — this is
exactly the mechanism your sandbox instructions describe: *"you can attach
the role (via the instance profile) to an EC2 instance when you want to
access an EC2 instance (terminal in the browser) using AWS Systems Manager
Session Manager."*

You are now inside the EC2 instance. Every command from here on runs here,
not on your laptop.

---

## Step 4 — Install Python (inside the Session Manager terminal)

# 1. Update the server and install git and python tools
sudo yum update -y
sudo yum install git python3 python3-pip -y

# 2. Download code from GitHub
git clone https://github.com/carloalfieri03/cloud.git

# 3. Enter the project folder
cd cloud

# 4. Install 'uv' on the EC2 Amazon Linux server
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env

# 5. Create the virtual environment using uv
uv venv

# 6. Activate it on macOS/Linux
source .venv/bin/activate

# 7. Install requirements 
### check if works on linux
uv pip install -r cloud-image-pipeline/resize/requirements.txt      
uv pip install -r cloud-image-pipeline/greyscale/requirements.txt 
uv pip install -r cloud-image-pipeline/detect/requirements.txt 
uv pip install -r cloud-image-pipeline/monolitic/requirements.txt 

---

## Step 5 - Set Variables and Run

```bash
# set the static vars
export OUTPUT_BUCKET="YOURNAME-output"
export POLL_INTERVAL_SECONDS=5
export AWS_DEFAULT_REGION="us-east-1"

# set the input bucket 
export INPUT_BUCKET="YOURNAME-resize-input" 
```

# Run the polling script!
```bash
uv run cloud-image-pipeline/resize/resize_app.py
```

---

## Step 6 — Test it (from your laptop, in a different terminal)

```bash
aws s3 cp test_image.jpg s3://YOURNAME-resize-input/test_image.jpg
```

Within 5 seconds, watch the Session Manager terminal — it should print
`"Resize finished: test_image.jpg in ... ms"`. Then check the output:

```bash
aws s3 ls s3://YOURNAME-output/
```

You should see `test_image_resized.jpg`. The original object will be gone
from `resize-input` — that's the delete-after-processing behaviour, working
as intended (see the comment block in resize_app.py for why).

Press `Ctrl+C` in the Session Manager terminal to stop the worker cleanly.

---

## Step 8 — Repeat for the other three

Exactly the same 8 steps, changing only:
- the folder/file name (`greyscale` + `grayscale_app.py`, `detect` +
  `detect_app.py` — this one also needs the two model files copied up via
  S3 in Step 5, same way as the script — `monolitic` + `monolitic_app.py`)

You must overwrite the env var and start the script:

```bash
export INPUT_BUCKET="YOURNAME-greyscale-input"
uv run cloud-image-pipeline/greyscale/grayscale_app.py
```

Press `Ctrl+C`

```bash
export INPUT_BUCKET="YOURNAME-detect-input"
uv run cloud-image-pipeline/detect/detect_app.py
```

Press `Ctrl+C`

```bash
export INPUT_BUCKET="YOURNAME-monolith-input"
uv run cloud-image-pipeline/monolitic/monolitic_app.py
```

---
