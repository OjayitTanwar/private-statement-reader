"""Download a public, local-only specialist OCR model; never sends document data."""
import json,os
from pathlib import Path
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['HF_HUB_DISABLE_XET']='1'
os.environ['HF_HUB_DOWNLOAD_TIMEOUT']='180'
from huggingface_hub import snapshot_download
ROOT=Path(__file__).resolve().parent
repo='mlx-community/GLM-OCR-8bit'
revision='350a346bea43468bcd202abb833f7d32e9a17237'
dest=ROOT/'work/ai-model'
print(f'Downloading {repo} at {revision} to the local project',flush=True)
snapshot_download(repo,revision=revision,local_dir=str(dest),max_workers=4,
                  allow_patterns=['*.json','*.safetensors','*.jinja','*.txt','*.model','README.md','LICENSE*'])
(dest/'source-manifest.json').write_text(json.dumps({'repository':repo,'revision':revision,'inference':'local only'},indent=2))
print('Local specialist AI model ready.',flush=True)
