"""Private local MLX allocator telemetry; no images, names or document content."""
import json
import os
from pathlib import Path
import threading
import time


def start_memory_reporter(mx):
    location=os.environ.get('STATEMENT_RESOURCE_METRICS')
    if not location:return
    folder=Path(location);folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=folder/f'ai-memory-{os.getpid()}.json'
    def report():
        while True:
            try:
                active=int(mx.get_active_memory());cache=int(mx.get_cache_memory())
                temporary=target.with_suffix('.tmp')
                descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(descriptor,'w') as stream:json.dump({'pid':os.getpid(),'time':time.time(),'metal_bytes':active+cache},stream)
                os.replace(temporary,target)
            except (OSError,ValueError,AttributeError,RuntimeError):pass
            time.sleep(1)
    threading.Thread(target=report,daemon=True,name='mlx-memory-telemetry').start()
