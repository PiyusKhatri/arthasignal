# GPU run plan: Surya and a vision-language model on all report images

**Status:** prepared, **not launched**. No instance has been created. Start it only when the GPU quota is approved.

## What runs

| Job | Model | Licence | Output |
|---|---|---|---|
| Surya OCR | `datalab-to/surya-ocr-2` (surya-ocr 0.22.1, through llama.cpp built with CUDA) | Surya code GPL-3.0; model weights under Datalab's model licence (free below a revenue threshold), which must be checked before any commercial use | One text file per image, the same format as the laptop runs |
| Vision-language model | `Qwen/Qwen2.5-VL-7B-Instruct` through vLLM, temperature 0 | Apache-2.0 | JSON per image: fields copied as printed, unit, basis, fiscal year and quarter |

**Input:** the 9,883 development-window report images (about 3-5 GB), plus any collected later. Each output then goes through the same consensus, accounting checks and gold-set precision measurement (`src/archive/fundamentals_quality.py`) as the local engines.

## Instance choice

| Cloud | Instance | GPU | On-demand price (check before launch) | Notes |
|---|---|---|---|---|
| **AWS (recommended)** | `g6.xlarge` | 1 × NVIDIA L4, 24 GB | about $0.80/h in us-east-1; Mumbai (ap-south-1) is higher where offered | 4 vCPU, 16 GB RAM, enough for a 7B VLM at 16k context |
| AWS (alternative) | `g5.xlarge` | 1 × A10G, 24 GB | about $1.01/h in us-east-1 | Widely available, including ap-south-1 |
| Azure | `Standard_NC4as_T4_v3` | 1 × T4, 16 GB | about $0.53/h | Slower; the 7B VLM needs a reduced context or 4-bit weights |

**Which AMI or image:** on AWS use "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)", which ships the drivers and CUDA. On Azure use the "NVIDIA GPU-Optimized VMI".

## Quota request (needed once; new accounts start at 0 GPU vCPUs)

**AWS:**
1. Console → Service Quotas → AWS services → Amazon EC2.
2. Search "Running On-Demand G and VT instances" (for spot instances: "All G and VT Spot Instance Requests").
3. Request quota increase → new value **8** (vCPUs; g6.xlarge uses 4) → choose the region (us-east-1 or ap-south-1) → submit.
4. The use case text can say: "OCR of about 10,000 public financial-report images for personal research, one g6.xlarge for about 30 hours."
5. Approval usually takes from a few hours to 2 days. Check the request status on the same page.

**Azure:**
1. Portal → Subscriptions → your subscription → Usage + quotas.
2. Filter "Standard NCASv3_T4 Family vCPUs" for the region → New quota request → **4** → submit.

## Cost estimate (to be confirmed by the pilot)

| Step | Assumed speed | Time for 9,883 images | Cost at $0.80/h |
|---|---|---|---|
| Setup and drivers check | — | 0.5 h | $0.40 |
| Pilot (200 images, both jobs) | measured | about 1 h | $0.80 |
| Surya | about 6 s per tall image on an L4 | about 16.5 h | $13 |
| Qwen2.5-VL-7B (vLLM, batched) | about 4 s per image | about 11 h | $9 |
| EBS 100 GB gp3 for 2 days, S3 storage and transfer of about 5 GB | — | — | about $3 |
| **Total** | | **about 29 h** | **about $26 on demand**, about $10-12 on spot |

The speeds are planning assumptions, not measurements. **The pilot decides.** If the measured time per image is more than twice the assumption, stop and re-plan rather than running the full set.

## Steps (when the quota is approved)

On the Mac:
```bash
aws s3 mb s3://<your-bucket>                                           # once
scripts/gpu/sync.sh up s3://<your-bucket>/arthasignal-ocr               # images and manifest
```
Launch one `g6.xlarge` with the Deep Learning Base AMI and 100 GB gp3, an instance role that can read and write the bucket, and SSH from your IP only. Then on the instance:
```bash
bash ~/arthasignal/scripts/gpu/setup_instance.sh    # or copy the scripts first: scp -r scripts/gpu ubuntu@<ip>:~
source ~/.bashrc
bash ~/arthasignal/scripts/gpu/run_surya.sh s3://<your-bucket>/arthasignal-ocr
bash ~/arthasignal/scripts/gpu/run_vlm.sh s3://<your-bucket>/arthasignal-ocr
```
For the pilot, before the full runs, edit `~/work/manifest_remote.json` down to 200 entries (for example `python3 -c "import json;p='/home/ubuntu/work/manifest_remote.json';m=json.load(open(p));json.dump(m[:200],open(p,'w'))"`), run both jobs, time them and look at a few outputs. Then restore the full manifest by rerunning `run_surya.sh`. Both runners skip images that already have output, so a stopped or spot-interrupted run resumes where it left off.

Back on the Mac:
```bash
scripts/gpu/sync.sh down s3://<your-bucket>/arthasignal-ocr
venv/bin/python -m src.archive.fundamentals_quality         # precision with the new engines included
```
**Then terminate the instance and delete the EBS volume.** Stopping alone keeps billing the disk.
