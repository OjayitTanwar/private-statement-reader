"""Refresh application source in an already-built, validated staging bundle."""
from pathlib import Path
import plistlib,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parent
APP=Path(sys.argv[1]).resolve()
if not APP.parent.name.startswith('private-statement-build-'):raise SystemExit('Not a build staging directory')
with (APP/'Contents/Info.plist').open('rb') as f:info=plistlib.load(f)
if info.get('CFBundleIdentifier')!='local.private.statementreader':raise SystemExit('Unexpected application bundle')
resources=APP/'Contents/Resources'
for name in ('app.py','bank_engine.py','people.py','table_grid.py','local_ai.py','ai_runner.py','ai_formats.py','automation.py'):
    shutil.copy2(ROOT/name,resources/'server'/name)
for item in (ROOT/'static').iterdir():
    if item.is_file():shutil.copy2(item,resources/'server/static'/item.name)
if '--native' in sys.argv:
    subprocess.run(['swiftc','-O','-target','arm64-apple-macosx14.0','-module-cache-path',str(ROOT/'work/module-cache'),str(ROOT/'native/StatementReader.swift'),'-o',str(APP/'Contents/MacOS/StatementReader')],check=True)
    info['CFBundleShortVersionString']='0.5.0';info['CFBundleVersion']='5'
    with (APP/'Contents/Info.plist').open('wb') as f:plistlib.dump(info,f)
subprocess.run(['xattr','-cr',str(APP)],capture_output=True,check=True)
subprocess.run(['codesign','--force','--deep','--sign','-',str(APP)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(APP)],check=True)
out=ROOT/'outputs'
subprocess.run(['ditto','--norsrc',str(APP),str(out/APP.name)],check=True)
shutil.copy2(ROOT/'README.md',out/'Read Me.md')
archive=out/'Private Statement Reader.zip'
if '--app-only' in sys.argv:
    print('Updated and verified app; archive unchanged.',flush=True)
    sys.exit(0)
elif '--incremental' in sys.argv and archive.is_file():
    subprocess.run(['zip','-q','-r','-u',str(archive),APP.name],cwd=APP.parent,check=True)
else:
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(APP),str(archive)],check=True)
print('Updated, signed, verified and packaged.',flush=True)
