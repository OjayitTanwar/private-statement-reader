"""Resumable ranged HTTPS download of the pinned public model weight file."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import hashlib,json,os,time,urllib.request

ROOT=Path(__file__).resolve().parent
DEST=ROOT/'work/ai-model';PARTS=ROOT/'work/ai-download-parts';PARTS.mkdir(exist_ok=True)
SIZE=1583785269
EXPECTED='f616d5bc38d8221444e94f0c84b63dbca1d1d1ce10f61f6ce802cc2a816e30d0'
URL='https://huggingface.co/mlx-community/GLM-OCR-8bit/resolve/350a346bea43468bcd202abb833f7d32e9a17237/model.safetensors'
CHUNK=32*1024*1024
old=list((DEST/'.cache/huggingface/download').glob('*.incomplete'))
prefix=max(old,key=lambda p:p.stat().st_size,default=None)
if prefix:
    with prefix.open('rb') as source:
        for i in range(prefix.stat().st_size//CHUNK):
            path=PARTS/f'{i:04d}.part'
            if path.exists() and path.stat().st_size==CHUNK:source.seek(CHUNK,1);continue
            path.write_bytes(source.read(CHUNK))

def download(index):
    start=index*CHUNK;end=min(SIZE,start+CHUNK)-1;path=PARTS/f'{index:04d}.part';needed=end-start+1
    if path.exists() and path.stat().st_size==needed:return index
    for attempt in range(6):
        have=path.stat().st_size if path.exists() else 0
        if have>needed:path.unlink();have=0
        request=urllib.request.Request(URL,headers={'Range':f'bytes={start+have}-{end}','User-Agent':'PrivateStatementReader-model-setup/0.3'})
        try:
            with urllib.request.urlopen(request,timeout=45) as response:
                if response.status!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {start+have}-'):
                    raise ValueError('Server did not honor the requested byte range')
                with path.open('ab') as out:
                    while data:=response.read(256*1024):out.write(data)
            if path.stat().st_size==needed:return index
        except Exception as e:
            if attempt==5:raise
            time.sleep(min(5,attempt+1))
    raise RuntimeError('Incomplete model segment')

count=(SIZE+CHUNK-1)//CHUNK;finished=0
with ThreadPoolExecutor(max_workers=6) as pool:
    for future in as_completed([pool.submit(download,i) for i in range(count)]):
        future.result();finished+=1
        print(f'Model segments complete: {finished}/{count}',flush=True)

target=DEST/'model.safetensors';digest=hashlib.sha256()
with target.open('wb') as out:
    for i in range(count):
        with (PARTS/f'{i:04d}.part').open('rb') as source:
            while data:=source.read(1024*1024):out.write(data);digest.update(data)
if digest.hexdigest()!=EXPECTED:raise RuntimeError('Downloaded model SHA-256 mismatch')
(DEST/'source-manifest.json').write_text(json.dumps({'repository':'mlx-community/GLM-OCR-8bit','revision':'350a346bea43468bcd202abb833f7d32e9a17237','sha256':EXPECTED,'inference':'local only'},indent=2))
print('Verified local AI weights ready.',flush=True)
