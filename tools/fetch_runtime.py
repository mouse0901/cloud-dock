from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import Request, urlopen
import hashlib
import shutil
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/flet-dev/flet/releases/download/v1.0.4/flet-windows.zip"
SIZE = 40249537
SHA256 = "daea81356d6996e4e660a61b9edb2c82eb1c45371a3f8ada217b22fcb690d83f"
PARTS = ROOT / "work" / "runtime-parts"
PARTS.mkdir(parents=True, exist_ok=True)

def fetch(index):
    start = index * SIZE // 8
    end = (index + 1) * SIZE // 8 - 1
    output = PARTS / f"{index}.part"
    for attempt in range(3):
        try:
            request = Request(URL, headers={"Range": f"bytes={start}-{end}", "User-Agent": "CloudDock-LocalSetup/0.1", "Accept-Encoding": "identity"})
            with urlopen(request, timeout=45) as response:
                if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{SIZE}":
                    raise RuntimeError(f"Unexpected Range response: {response.status}, {response.headers.get('Content-Range')}")
                with output.open("wb") as target:
                    shutil.copyfileobj(response, target, 65536)
            if output.stat().st_size != end-start+1:
                raise RuntimeError("Incomplete range")
            print(f"Runtime segment {index+1}/8 verified", flush=True)
            return output
        except Exception as exc:
            if attempt == 2:
                raise
            print(f"Retry segment {index+1}: {type(exc).__name__}", flush=True)
            time.sleep(2 ** attempt)

with ThreadPoolExecutor(max_workers=8) as pool:
    for result in as_completed([pool.submit(fetch, i) for i in range(8)]):
        result.result()
archive = ROOT / "work" / "flet-windows.verified.zip"
with archive.open("wb") as target:
    for i in range(8):
        with (PARTS / f"{i}.part").open("rb") as source:
            shutil.copyfileobj(source, target)
if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
    raise RuntimeError("Runtime checksum mismatch")
destination = (ROOT / "runtime").resolve()
with zipfile.ZipFile(archive) as z:
    for item in z.infolist():
        if not (destination / item.filename).resolve().is_relative_to(destination):
            raise RuntimeError("Archive escapes runtime directory")
    z.extractall(destination)
print("OFFICIAL_RUNTIME_SHA256_VERIFIED", flush=True)
