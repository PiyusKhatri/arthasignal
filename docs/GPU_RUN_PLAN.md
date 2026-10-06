# GPU run plan: Surya and Qwen2.5-VL on report images (AWS Mumbai)

**Status:** prepared, **not launched**. No instance, bucket, role or key has been created. The quota for 8 vCPUs of On-Demand G instances in ap-south-1 (Mumbai) is approved; one `g6.xlarge` uses 4.

The pilot comes first. The full run starts only if the pilot passes its stop rule (below).

## Why Surya produced no values on the laptop

- On this 8 GB Mac, Surya ran at about 200 s per image: 11 images took 2,180 s (`logs/ocr_surya_gold.log`).
- The runner (`scripts/ocr/ocr_run.py batch`) wrote output only after each block of 20 images. One block therefore took about 66 minutes.
- The label-set run started at 15:30 on 2026-10-05. The log ends with the Hugging Face warning and no error line, no Surya process is running, and `derived/ocr/surya/` is empty.
- So the process ended (killed, or stopped when the laptop slept) before the first 20 images were written. All 120 labelled reports lack Surya text, so Surya has 0 values in `docs/fundamentals_precision.json`.
- **Fixed:** every engine now writes each image as soon as it is read and prints seconds per image. A stopped run loses at most the image in progress, and a restart skips finished images.
- Surya is therefore not judged yet. It is included in the pilot, where an NVIDIA L4 should take seconds per image.

## What runs

| Job | Model | Licence | Output |
|---|---|---|---|
| Surya OCR | `surya-ocr==0.22.1` with llama.cpp built for CUDA | Code GPL-3.0; model weights under Datalab's model licence (free below a revenue threshold). Check it before any commercial use | One text file per image, read by the same v2 readers as Tesseract and PaddleOCR |
| Vision-language model | `Qwen/Qwen2.5-VL-7B-Instruct` through vLLM, temperature 0, images capped at 3.0 megapixels | Apache-2.0 | One JSON per image: unit, basis, fiscal year, quarter and the 8 fields as printed (`scripts/gpu/run_vlm.py`) |

Both outputs go through the same accounting checks and gold-set measurement as the local engines (`src/archive/fundamentals_quality.py`: methods `surya`, `surya_v2`, `qwen25vl7b`, `qwen25vl7b_checked`). Mismatches are classified by `src/archive/fundamentals_causes.py`.

## The pilot: 200 images

- `venv/bin/python -m src.archive.gpu_pilot` writes `derived/ocr/pilot_manifest.json` and `derived/ocr/pilot_images.tar` (288 MB). Built on 2026-10-06, it holds:
  - all **120 labelled reports**;
  - **80 unlabelled reports**, drawn by hash order across years (15 to 19 per year, 2014 to 2025) from the development-window manifest;
  - no report published on or after 2025-09-30.
- It answers four questions:
  1. seconds per image for each job;
  2. per-field precision on the 120 labels;
  3. whether the VLM's JSON parses;
  4. whether Surya reads Devanagari digits that PaddleOCR's English model cannot.

## Cost

Prices for ap-south-1 must be read before launch (step 2 prints them). The planning figures below use the us-east-1 list price of $0.805/h for `g6.xlarge` with a 40% margin for Mumbai, so **$1.13/h**.

| Item | Pilot (200 images) | Full run (2,101 images in the current manifest) |
|---|---|---|
| Setup: drivers check, packages, llama.cpp build, model download | 0.75 h | 0.75 h (on a new instance) |
| Surya, assumed 6 s per image on an L4 | 0.35 h | 3.5 h |
| Qwen2.5-VL-7B, assumed 4 s per image | 0.25 h | 2.3 h |
| Margin for checks and copying | 0.4 h | 1 h |
| Instance hours | **about 1.75 h, about $2.00** | **about 7.5 h, about $8.50** |
| 100 GB gp3 disk (about $0.0912 per GB-month in Mumbai), deleted with the instance | about $0.05 | about $0.15 |
| S3 storage and requests (0.3 GB pilot, about 3 GB full) | under $0.10 | under $0.50 |
| **Total** | **about $2-3** | **about $9-10** |

- The second column covers the 2,101 development-window report images in `derived/ocr/manifest.json`. All 7,198 files in `raw/archive/sharesansar_reports` (10 GB) would be about 3.4 times that.
- The speeds are assumptions. **Stop rule:** if the pilot measures more than 12 s per image for either job, do not start the full run; re-plan instead.

## Steps

All commands run on the Mac unless marked. Replace `<you>` with a short suffix and `<MYIP>` with your public IP (`curl -s https://checkip.amazonaws.com`).

**1. Tools (once).**
```bash
brew install awscli
aws configure            # access key of an IAM user allowed to use EC2, S3, IAM and SSM; region ap-south-1; output json
export AWS_REGION=ap-south-1 BUCKET=s3://arthasignal-ocr-<you>/arthasignal-ocr
```

**2. Check availability and price.**
```bash
aws ec2 describe-instance-type-offerings --location-type availability-zone \
  --filters Name=instance-type,Values=g6.xlarge,g5.xlarge --query 'InstanceTypeOfferings[].[InstanceType,Location]' --output text
aws pricing get-products --region us-east-1 --service-code AmazonEC2 --filters \
  Type=TERM_MATCH,Field=instanceType,Value=g6.xlarge Type=TERM_MATCH,Field=regionCode,Value=ap-south-1 \
  Type=TERM_MATCH,Field=operatingSystem,Value=Linux Type=TERM_MATCH,Field=tenancy,Value=Shared \
  Type=TERM_MATCH,Field=preInstalledSw,Value=NA Type=TERM_MATCH,Field=capacitystatus,Value=Used \
  --query 'PriceList[0]' --output text | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print([p['pricePerUnit'] for t in d['terms']['OnDemand'].values() for p in t['priceDimensions'].values()])"
aws service-quotas get-service-quota --service-code ec2 --quota-code L-DB2E81BA --query 'Quota.Value'   # On-Demand G and VT vCPUs; must be at least 4
```
If `g6.xlarge` is not offered in a zone, use `g5.xlarge` (1 x A10G, 24 GB) in the steps below. Check its price the same way.

**3. Bucket and pilot upload.**
```bash
aws s3 mb s3://arthasignal-ocr-<you> --region ap-south-1
venv/bin/python -m src.archive.gpu_pilot
scripts/gpu/sync.sh pilot $BUCKET
```

**4. Instance role (once), limited to this bucket.**
```bash
aws iam create-role --role-name arthasignal-gpu --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam put-role-policy --role-name arthasignal-gpu --policy-name bucket --policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::arthasignal-ocr-<you>"},
    {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":"arn:aws:s3:::arthasignal-ocr-<you>/*"}]}'
aws iam create-instance-profile --instance-profile-name arthasignal-gpu
aws iam add-role-to-instance-profile --instance-profile-name arthasignal-gpu --role-name arthasignal-gpu
```

**5. Key and firewall (once).**
```bash
aws ec2 create-key-pair --key-name arthasignal-gpu --query KeyMaterial --output text > ~/.ssh/arthasignal-gpu.pem && chmod 400 ~/.ssh/arthasignal-gpu.pem
SG=$(aws ec2 create-security-group --group-name arthasignal-gpu --description "SSH from one IP" --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $SG --protocol tcp --port 22 --cidr <MYIP>/32
```

**6. Launch one instance.** The AMI is "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)", which ships the drivers and CUDA. The disk is deleted with the instance, and an operating-system shutdown terminates it.
```bash
AMI=$(aws ssm get-parameter --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id --query Parameter.Value --output text)
ID=$(aws ec2 run-instances --image-id $AMI --instance-type g6.xlarge --key-name arthasignal-gpu --security-group-ids $SG \
  --iam-instance-profile Name=arthasignal-gpu --instance-initiated-shutdown-behavior terminate \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=arthasignal-gpu}]' 'ResourceType=volume,Tags=[{Key=Name,Value=arthasignal-gpu}]' \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids $ID
IP=$(aws ec2 describe-instances --instance-ids $ID --query 'Reservations[0].Instances[0].PublicIpAddress' --output text); echo $ID $IP
```

**7. Copy the scripts and set up (on the instance).** The first command on the instance arms a dead-man switch: the machine terminates itself after 4 hours even if you forget step 10.
```bash
ssh -i ~/.ssh/arthasignal-gpu.pem ubuntu@$IP 'mkdir -p ~/arthasignal/scripts'
scp -i ~/.ssh/arthasignal-gpu.pem -r scripts/gpu scripts/ocr ubuntu@$IP:~/arthasignal/scripts/
ssh -i ~/.ssh/arthasignal-gpu.pem ubuntu@$IP
sudo shutdown -h +240
nvidia-smi                                         # must show the L4 (or A10G)
bash ~/arthasignal/scripts/gpu/setup_instance.sh && source ~/.bashrc
```

**8. Pilot (on the instance, about 40 minutes of jobs).**
```bash
export BUCKET=s3://arthasignal-ocr-<you>/arthasignal-ocr
bash ~/arthasignal/scripts/gpu/run_surya.sh $BUCKET pilot
bash ~/arthasignal/scripts/gpu/run_vlm.sh $BUCKET pilot
cat ~/work/out/timing/*.json                       # seconds and images per job
```

**9. Measure (on the Mac).**
```bash
scripts/gpu/sync.sh down $BUCKET
venv/bin/python -m src.archive.fundamentals_quality
venv/bin/python -m src.archive.fundamentals_causes
```
Apply the stop rule. The full run needs a new launch (steps 6-7), then on the instance:
```bash
rm -f ~/work/manifest_remote.json
bash ~/arthasignal/scripts/gpu/run_surya.sh $BUCKET full
bash ~/arthasignal/scripts/gpu/run_vlm.sh $BUCKET full
```
Run `scripts/gpu/sync.sh up $BUCKET` on the Mac first; it uploads only the 2,101 manifest images. Raise the dead-man switch to `sudo shutdown -h +600` for the full run.

**10. Stop and terminate (always, as soon as the outputs are in S3).**
```bash
aws ec2 terminate-instances --instance-ids $ID
aws ec2 wait instance-terminated --instance-ids $ID
aws ec2 describe-volumes --filters Name=tag:Name,Values=arthasignal-gpu --query 'Volumes[].[VolumeId,State]' --output text   # must print nothing
aws ec2 describe-instances --filters Name=tag:Name,Values=arthasignal-gpu Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text   # must print nothing
```
**Stopping is not enough:** a stopped instance keeps billing its disk. If `describe-volumes` lists a volume, delete it with `aws ec2 delete-volume --volume-id <id>`.

Optional, after the results are copied home:
- `aws s3 rm --recursive s3://arthasignal-ocr-<you>` and `aws s3 rb s3://arthasignal-ocr-<you>`;
- `aws ec2 delete-security-group --group-id $SG`.
