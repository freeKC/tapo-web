#!/usr/bin/env bash
# ML environment for animal detection (DeepFaune 1.5.0). Separate from the app's .venv.
# torch 2.14 / cu126 = last prebuilt PyTorch line that drives a Pascal GPU (GTX 1050).
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH" UV_HTTP_TIMEOUT=900
[ -x .mlvenv/bin/python ] || uv venv --python 3.12 .mlvenv
uv pip install --python .mlvenv/bin/python --index-url https://download.pytorch.org/whl/cu126 'torch==2.14.0' 'torchvision==0.29.0'
uv pip install --python .mlvenv/bin/python ultralytics yolov5 timm opencv-python-headless pandas dill hachoir 'setuptools==81'
if [ ! -d ml/deepfaune ]; then
  git clone --branch v1.5.0 --depth 1 https://plmlab.math.cnrs.fr/deepfaune/software.git ml/deepfaune
fi
for f in deepfaune-yolov8s_960.pt md_v1000.0.0-sorrel.pt deepfaune-vit_large_patch16_dinov3.lvd1689m.pt deepfaune-vit_large_patch16_dinov3.lvd1689m-bird_head.pt; do
  [ -s ml/deepfaune/$f ] || curl -L -C - -o ml/deepfaune/$f https://pbil.univ-lyon1.fr/software/download/deepfaune/v1.5/$f
done
.mlvenv/bin/python -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())"
