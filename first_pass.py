"""Render the first submitted program for one frozen contract/task, fully offline.

python3 first_pass.py TASK.json method.py program.py REGISTRY
TASK is one row exported by quality_eval.py prompts. The trusted operator owns
REGISTRY and supplies only audited source files. program.build(prompt) emits a
JSON scene plan. In a fresh interpreter the frozen method.render(plan) returns
one RGB PIL.Image. This adapter accepts a single frozen Python module.
"""
import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib


RUNTIME = "sha256:f701435f0f973bbf410d7f9d1614f5d4ae31ff9895a7aeb25eb5ca8455e7250e"
MAX_SIDE = 4096
BUILDER = """
import json, sys
from pathlib import Path
sys.path.insert(0, '/input')
from program import build
with open('/tmp/scene.json', 'x', encoding='utf-8') as out:
    json.dump(build(Path('/input/prompt.txt').read_text(encoding='utf-8')), out, allow_nan=False)
"""
DRIVER = """
import json, struct, sys
from pathlib import Path
sys.path.insert(0, '/input')
import method
from PIL import Image
with open('/tmp/scene.json', 'rb') as src:
    plan = src.read(1_048_577)
if len(plan) > 1_048_576:
    raise ValueError('scene exceeds this adapter limit of 1 MiB')
im = method.render(json.loads(plan))
if not isinstance(im, Image.Image) or im.mode != 'RGB' or not (1 <= min(im.size) <= max(im.size) <= 4096):
    raise ValueError('return a 1..4096 per side RGB image')
with open('/out/pixels', 'wb') as out:
    out.write(struct.pack('>II', *im.size))
    out.write(im.tobytes())
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    os.replace(tmp, path)


def make_png(raw):
    if len(raw) < 8:
        raise ValueError("missing RGB header")
    w, h = struct.unpack(">II", raw[:8])
    if not (1 <= min(w, h) <= max(w, h) <= MAX_SIDE and len(raw) == 8 + 3*w*h):
        raise ValueError("invalid RGB raster dimensions or length")
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    stride = 3*w
    scanlines = b"".join(b"\0" + raw[offset:offset+stride] for offset in range(8, len(raw), stride))
    header = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines))
            + chunk(b"IEND", b"")), (w, h)


def record(task, method, program, registry, timeout):
    expected = ("contract", "method_sha256", "prompt_sha256")
    if any(not re.fullmatch("[0-9a-f]{64}", task[k]) for k in expected):
        raise ValueError("task requires frozen contract, method and prompt digests")
    if not isinstance(task["id"], str) or not task["id"].strip() or not task["prompt"].strip():
        raise ValueError("missing task id or prompt")
    prompt = task["prompt"].encode("utf-8")
    if digest(method) != task["method_sha256"] or digest(prompt) != task["prompt_sha256"]:
        raise ValueError("method or prompt changed after freeze")
    if timeout <= 0 or timeout > 86400:
        raise ValueError("invalid timeout in seconds")
    # One operator-owned directory per contract. Duplicate prompt IDs and exact
    # duplicate prompts are rejected by freeze. The atomic claim survives failure.
    case = registry / task["contract"] / task["prompt_sha256"]
    case.parent.mkdir(parents=True, exist_ok=True)
    try:
        case.mkdir()
    except FileExistsError:
        raise ValueError(f"first submission already claimed: {case}") from None
    receipt = {"schema": "rsi.first_pass.v1", "status": "claimed",
               **{k: task[k] for k in (*expected, "id")}, "attempt": 1, "render_budget": 1,
               "reference_inputs": 0, "network": "none", "runtime_image": RUNTIME,
               "method_entry": "method.render", "program_sha256": digest(program)}
    save_json(case / "receipt.json", receipt)
    name = "rsi-first-" + uuid.uuid4().hex
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as temp:
            scratch = Path(temp)
            inputs = scratch / "input"
            inputs.mkdir(mode=0o755)
            for filename, data in (("prompt.txt", prompt), ("method.py", method),
                                   ("program.py", program), ("builder.py", BUILDER.encode()),
                                   ("driver.py", DRIVER.encode())):
                path = inputs / filename
                path.write_bytes(data)
                path.chmod(0o444)
            options = ["docker", "run", "--name", name, "--pull=never", "--network=none",
                       "--log-driver=none", "--read-only", "--cap-drop=ALL",
                       "--security-opt=no-new-privileges", "--user=65534:65534",
                       "--pids-limit=64", "--cpus=2", "--memory=1g", "--memory-swap=1g",
                       "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
                       "--tmpfs=/out:rw,noexec,nosuid,nodev,size=56m,mode=1777",
                       "--mount", f"type=bind,src={inputs.resolve()},dst=/input,readonly",
                       "--env=OPENBLAS_NUM_THREADS=1", "--env=OMP_NUM_THREADS=1", RUNTIME,
                       "sh", "-c", 'python -IB /input/builder.py 1>&2 && python -IB /input/driver.py 1>&2 && exec cat /out/pixels']
            # Read stdout with a hard byte ceiling. Never import candidate code
            # or decode an arbitrary container-supplied image on the host.
            with subprocess.Popen(options, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                errors = deque(maxlen=4)
                output = bytearray()
                overflow = threading.Event()
                def drain_stdout():
                    for buf in iter(lambda: proc.stdout.read(65536), b""):
                        if len(output) + len(buf) > 8 + 3*MAX_SIDE**2:
                            overflow.set()
                            proc.kill()
                            return
                        output.extend(buf)
                def drain_stderr():
                    for buf in iter(lambda: proc.stderr.read(1024), b""):
                        errors.append(buf)
                readers = [threading.Thread(target=f, daemon=True) for f in (drain_stdout, drain_stderr)]
                for thread in readers:
                    thread.start()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise ValueError("render time budget exceeded") from None
                finally:
                    for thread in readers:
                        thread.join()
                if overflow.is_set():
                    raise ValueError("output exceeds raster budget")
                if proc.returncode:
                    (case / "stderr_tail.txt").write_bytes(b"".join(errors))
                    raise ValueError(f"offline renderer exited {proc.returncode}")
            png, size = make_png(output)
            (case / "image.png").write_bytes(png)
            # Retain the exact submission for cold replay; omit evaluator files.
            for filename in ("method.py", "program.py", "prompt.txt"):
                shutil.copyfile(inputs / filename, case / filename)
            receipt.update(status="rendered", image_sha256=digest(png), size=list(size))
    except BaseException as error:
        receipt.update(status="failed", error=str(error))
        raise
    finally:
        receipt["wall_seconds"] = time.monotonic() - start
        save_json(case / "receipt.json", receipt)
        if shutil.which("docker"):
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20)
    return case


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for arg in ("task", "method", "program", "registry"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    try:
        path = record(json.loads(args.task.read_text()), args.method.read_bytes(), args.program.read_bytes(),
                      args.registry, args.timeout)
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as err:
        parser.exit(2, f"first submission: {err}\n")
    print(path / "receipt.json")


if __name__ == "__main__":
    main()
