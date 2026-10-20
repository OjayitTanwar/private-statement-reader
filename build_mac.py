"""Build an Apple Silicon .app using the installed Xcode command line tools."""
from pathlib import Path
import importlib.util
import os
import plistlib
import shutil
import subprocess
import tempfile
import hashlib
import json
import importlib.metadata
import stat
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'outputs'
STAGING=Path(tempfile.mkdtemp(prefix='private-statement-build-'))
APP=STAGING/'Private Statement Reader.app'
CONTENTS=APP/'Contents'; RES=CONTENTS/'Resources'; BIN=CONTENTS/'MacOS'
for p in (OUT,RES,BIN,RES/'server/static',RES/'python-packages',ROOT/'work/module-cache'):
    p.mkdir(parents=True,exist_ok=True)
icons=STAGING/'StatementReader.iconset';icons.mkdir()
icon=Image.new('RGBA',(1024,1024),(0,0,0,0));draw=ImageDraw.Draw(icon)
draw.rounded_rectangle((50,50,974,974),radius=208,fill='#174d3b')
draw.rounded_rectangle((266,190,760,822),radius=35,fill='#f3f7ed')
for y,width in ((315,290),(405,290),(495,170)):
    draw.rounded_rectangle((350,y,350+width,y+34),radius=12,fill='#9daf9e')
draw.ellipse((538,560,840,862),fill='#a9d5ad')
draw.line([(613,710),(665,758),(770,649)],fill='#174d3b',width=32,joint='curve')
for size in (16,32,128,256,512):
    for scale in (1,2):icon.resize((size*scale,size*scale),Image.Resampling.LANCZOS).save(icons/f'icon_{size}x{size}{"@2x" if scale==2 else ""}.png')
subprocess.run(['iconutil','--convert','icns',str(icons),'-o',str(RES/'StatementReader.icns')],check=True)
for name in ('app.py','bank_engine.py','people.py','table_grid.py','local_ai.py','ai_runner.py','ai_formats.py','automation.py','review_checks.py','source_repair.py','manual_overrides.py','resource_control.py','ai_memory.py'):
    shutil.copy2(ROOT/name,RES/'server'/name)
for p in (ROOT/'static').iterdir():
    if p.is_file(): shutil.copy2(p,RES/'server/static'/p.name)
for package in ('PIL','openpyxl','et_xmlfile','numpy'):
    spec=importlib.util.find_spec(package)
    if not spec: raise SystemExit(f'Missing build dependency: {package}. Install requirements.txt first.')
    shutil.copytree(Path(spec.origin).parent,RES/'python-packages'/package,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
cv=ROOT/'work/ai-env/lib/python3.13/site-packages/cv2'
if cv.is_dir():shutil.copytree(cv,RES/'python-packages/cv2',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
ai_packages=ROOT/'work/ai-env/lib/python3.13/site-packages'
ai_model=ROOT/'work/ai-model'
if ai_packages.is_dir() and (ai_model/'model.safetensors').is_file():
    shutil.copytree(ai_packages,RES/'ai-packages',dirs_exist_ok=True,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','pip','pip-*'))
    shutil.copytree(ai_model,RES/'ai-model',dirs_exist_ok=True,ignore=shutil.ignore_patterns('.cache'))
notices=RES/'Third Party Notices';notices.mkdir(exist_ok=True)
for name in ('Pillow','openpyxl','et-xmlfile','numpy'):
    dist=importlib.metadata.distribution(name)
    for entry in dist.files or []:
        if 'license' in str(entry).lower() and Path(dist.locate_file(entry)).is_file():
            shutil.copy2(dist.locate_file(entry),notices/(name+'-'+Path(entry).name))
model=ROOT/'work/models/eng.traineddata'
if model.is_file():
    models=RES/'models';models.mkdir(exist_ok=True)
    shutil.copy2(model,models/'eng.traineddata')
    osd=Path('/opt/homebrew/share/tessdata/osd.traineddata')
    if osd.is_file():shutil.copy2(osd,models/osd.name)
    for name in ('configs','tessconfigs'):
        directory=Path('/opt/homebrew/share/tessdata')/name
        if directory.is_dir():shutil.copytree(directory,models/name,dirs_exist_ok=True)
    font=Path('/opt/homebrew/share/tessdata/pdf.ttf')
    if font.is_file():shutil.copy2(font,models/font.name)
    (models/'model-source.json').write_text(json.dumps({'source':'https://github.com/tesseract-ocr/tessdata_best',
        'file':'eng.traineddata','sha256':hashlib.sha256(model.read_bytes()).hexdigest(),'license':'Apache-2.0'},indent=2))
shutil.copy2(ROOT/'LICENSE',RES/'LICENSE')
env={**os.environ,'CLANG_MODULE_CACHE_PATH':str(ROOT/'work/module-cache')}
for source,dest,frameworks in [('VisionOCR.swift',RES/'VisionOCR',['Vision','ImageIO']),('StatementReader.swift',BIN/'StatementReader',['AppKit','WebKit'])]:
    subprocess.run(['swiftc','-O','-target','arm64-apple-macosx14.0','-module-cache-path',str(ROOT/'work/module-cache'),
                    str(ROOT/'native'/source),'-o',str(dest),*[arg for f in frameworks for arg in ('-framework',f)]],check=True,env=env)
plist={'CFBundleName':'Private Statement Reader','CFBundleDisplayName':'Private Statement Reader',
       'CFBundleIdentifier':'local.private.statementreader','CFBundleExecutable':'StatementReader',
       'CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.7.0','CFBundleVersion':'8',
       'LSMinimumSystemVersion':'14.0','NSHighResolutionCapable':True,'CFBundleIconFile':'StatementReader',
       'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True},
       'NSHumanReadableCopyright':'Local bank statement OCR. Open-source processing; Apple Vision on macOS.'}
with (CONTENTS/'Info.plist').open('wb') as f: plistlib.dump(plist,f)
for base,dirs,files in os.walk(APP):
    for name in files:
        path=Path(base)/name
        if not path.is_symlink():path.chmod(path.stat().st_mode|stat.S_IWUSR)
subprocess.run(['xattr','-cr',str(APP)],check=True,capture_output=True,text=True)
subprocess.run(['codesign','--force','--deep','--sign','-',str(APP)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(APP)],check=True)
DELIVERED=OUT/APP.name
subprocess.run(['ditto','--norsrc',str(APP),str(DELIVERED)],check=True)
shutil.copy2(ROOT/'native/Setup.command',OUT/'Setup.command'); (OUT/'Setup.command').chmod(0o755)
shutil.copy2(ROOT/'docs/USER_GUIDE.md',OUT/'Read Me.md')
subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(APP),str(OUT/'Private Statement Reader.zip')],check=True)
print(DELIVERED)
