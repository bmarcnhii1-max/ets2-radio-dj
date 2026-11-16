import zipfile, os

files = [
    'server.py', 'index.html', 'overlay.html',
    'requirements.txt', 'smtc_bridge.ps1', 'start_radio.bat',
    'README.md', 'LICENSE',
    'static/style.css', 'static/app.js',
]
widget_root = 'RadioWidget/AppPackages/RadioWidget_Registered'
wfiles = []
for dirpath, _, names in os.walk(widget_root):
    for n in names:
        full = os.path.join(dirpath, n)
        rel = os.path.relpath(full, '.').replace(os.sep, '/')
        wfiles.append(rel)

out = 'ETS2RadioDJ_bundle.zip'
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for f in files:
        if os.path.exists(f):
            z.write(f, f)
    for f in wfiles:
        z.write(f, f)
print('bundle:', out, f'{os.path.getsize(out)/1024/1024:.1f} MB, {len(files)+len(wfiles)} files')
