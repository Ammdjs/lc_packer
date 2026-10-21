"""Fetch publisher-verified packages and reproduce the offline release inputs."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'artifacts/v2-downloads'
PACKAGES = ROOT / 'tool-packages'
CACHE.mkdir(parents=True, exist_ok=True)
PACKAGES.mkdir(exist_ok=True)
JDK_BASE = 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/'
FILES = [
    ('OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip', JDK_BASE, 190817615, 'sha256', 'e53a79c3c3d86865bd7e787903884331068e71321714ffd44f145785affc7cb0'),
    ('OpenJDK17U-jdk-sources_17.0.20.1_1.tar.gz', JDK_BASE, 109272338, 'sha256', '21e2a065d244ab048e737f21af5d1fc74daaeb6707de36477ead8db1dca71214'),
    ('build-tools_r36.1_windows.zip', 'https://dl.google.com/android/repository/', 59366211, 'sha1', '95f048ae758dad90617975fe3f787f038b03338c'),
    ('platform-tools_r37.0.1-win.zip', 'https://dl.google.com/android/repository/', 8044989, 'sha1', 'e03e78b1d80b396f1c3358e31251cb31740e1110'),
]


def fetch(item):
    name, base, size, algorithm, expected = item
    path = CACHE / name
    if not path.exists():
        temporary = path.with_name(path.name + '.partial')
        try:
            req = urllib.request.Request(base + name, headers={'User-Agent': 'LC-Packer-2.0.0'})
            with urllib.request.urlopen(req, timeout=90) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
            assert temporary.stat().st_size == size, f'Unexpected size: {name}'
            assert hashlib.new(algorithm, temporary.read_bytes()).hexdigest() == expected, f'Publisher digest mismatch: {name}'
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    assert path.stat().st_size == size
    assert hashlib.new(algorithm, path.read_bytes()).hexdigest() == expected
    print(json.dumps({'verified': name, 'bytes': size}, ensure_ascii=True), flush=True)
    return path


with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    list(pool.map(fetch, FILES))
for name in (FILES[0][0], FILES[1][0]):
    shutil.copy2(CACHE / name, PACKAGES / name)
jar = fetch(('apktool_3.0.3.jar', 'https://github.com/iBotPeaches/Apktool/releases/download/v3.0.3/', 15478013, 'sha256', 'dbf930b076c6b9be08d57c449cacefc3bdd6b71ebd59b3066fc0e1f5b14f9423'))
shutil.copy2(jar, PACKAGES / jar.name)


def minimal(source, prefix, output, members):
    # Stored entries and fixed metadata make the exact package reproducible
    # across zlib versions and operating systems. Binary contents are unchanged.
    temporary = PACKAGES / (output + '.partial')
    try:
        with zipfile.ZipFile(CACHE / source) as upstream, zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name in members:
                info = zipfile.ZipInfo(name, (2026, 10, 22, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 0
                info.external_attr = 0x20
                archive.writestr(info, upstream.read(prefix + name))
        temporary.replace(PACKAGES / output)
    finally:
        temporary.unlink(missing_ok=True)


minimal('build-tools_r36.1_windows.zip', 'android-16/', 'android-build-tools-36.1.0-lc.zip',
        ['aapt.exe', 'zipalign.exe', 'lib/apksigner.jar', 'NOTICE.txt', 'source.properties'])
minimal('platform-tools_r37.0.1-win.zip', 'platform-tools/', 'android-platform-tools-37.0.1-lc.zip',
        ['adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll', 'NOTICE.txt', 'source.properties'])
print(json.dumps([{'name':p.name,'size':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(PACKAGES.iterdir()) if p.is_file()],indent=2),flush=True)

# The committed catalog is the authority for release and runtime verification.
catalog = json.loads((ROOT / 'lc_packer/data/tools_catalog.json').read_text(encoding='utf-8'))
for record in [*catalog['packages'], *catalog['source_archives']]:
    path = PACKAGES / record['filename']
    assert path.stat().st_size == record['size'], f"Catalog size mismatch: {path.name}"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'], f"Catalog SHA256 mismatch: {path.name}"
print('All offline release inputs match the committed catalog.', flush=True)
