#!/usr/bin/env bash
set -euo pipefail
sudo apt-get update -y
sudo apt-get install -y python3.12-venv tesseract-ocr tesseract-ocr-nep awscli
python3.12 -m venv ~/ocr
~/ocr/bin/pip install --upgrade pip
~/ocr/bin/pip install "surya-ocr==0.22.1" "vllm>=0.6.4" "qwen-vl-utils" pillow
nvidia-smi
sudo apt-get install -y cmake build-essential git
git clone --depth 1 https://github.com/ggml-org/llama.cpp ~/llama.cpp
cmake -S ~/llama.cpp -B ~/llama.cpp/build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build ~/llama.cpp/build -j"$(nproc)" --target llama-server
echo "export LLAMA_CPP_BINARY=$HOME/llama.cpp/build/bin/llama-server" >> ~/.bashrc
git clone https://github.com/PiyusKhatri/arthasignal.git ~/arthasignal
