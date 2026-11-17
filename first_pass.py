"""Render the first submitted program for one frozen contract/task, fully offline.

python3 first_pass.py TASK.json method.py program.py REGISTRY
TASK is one row exported by quality_eval.py prompts. The trusted operator owns
REGISTRY and supplies only audited source files. program.build(prompt) emits a
JSON scene plan. In a fresh interpreter the frozen method.render(plan) returns
one RGB PIL.Image. This adapter accepts a single frozen Python module.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import tempfile
import time
import zlib


MAX_SIDE = 4096
BUILDER = """
import json, os, sys
from pathlib import Path
root = Path(os.environ['RSI_INPUT'])
sys.path.insert(0, str(root))
from program import build
with open(os.environ['RSI_SCENE'], 'x', encoding='utf-8') as out:
    json.dump(build((root / 'prompt.txt').read_text(encoding='utf-8')), out, allow_nan=False)
"""
DRIVER = """
import json, os, struct, sys
from pathlib import Path
root = Path(os.environ['RSI_INPUT'])
sys.path.insert(0, str(root))
import method
from PIL import Image
with open(os.environ['RSI_SCENE'], 'rb') as src:
    plan = src.read(1_048_577)
if len(plan) > 1_048_576:
    raise ValueError('scene exceeds this adapter limit of 1 MiB')
im = method.render(json.loads(plan))
if not isinstance(im, Image.Image) or im.mode != 'RGB' or not (1 <= min(im.size) <= max(im.size) <= 4096):
    raise ValueError('return a 1..4096 per side RGB image')
with open(os.environ['RSI_OUTPUT'], 'wb') as out:
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


def local_runtime():
    executable = runtime_executable()
    identity = "|".join((str(executable), sys.version, platform.platform()))
    return "local:" + digest(identity.encode())


def runtime_executable():
    configured = os.environ.get("RSI_PYTHON", "").strip()
    executable = Path(configured or sys.executable).expanduser()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("RSI_PYTHON must name an executable Python interpreter")
    return executable.resolve()


def run_local(scratch, inputs, timeout, details):
    scene = scratch / "scene.json"
    output = scratch / "pixels"
    executable = runtime_executable()
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONNOUSERSITE": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "RSI_INPUT": str(inputs),
        "RSI_SCENE": str(scene),
        "RSI_OUTPUT": str(output),
    }
    deadline = time.monotonic() + timeout
    errors = []
    for script in ("builder.py", "driver.py"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            details["timeout"] = True
            raise ValueError("render time budget exceeded")
        phase_start = time.monotonic()
        try:
            proc = subprocess.run([str(executable), "-I", str(inputs / script)], cwd=scratch,
                                  env=environment, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, timeout=remaining, check=False)
        except subprocess.TimeoutExpired as error:
            details["timeout"] = True
            details["phases"].append({"name": script[:-3], "wall_seconds": time.monotonic() - phase_start,
                                      "returncode": None, "stderr_tail": str(error.stderr or b"")[-4096:]})
            raise ValueError(f"{script[:-3]} timed out") from error
        except OSError as error:
            details["phases"].append({"name": script[:-3], "wall_seconds": time.monotonic() - phase_start,
                                      "returncode": None, "stderr_tail": str(error)[-4096:]})
            raise ValueError(f"could not start {script[:-3]}") from error
        stderr_tail = proc.stderr[-4096:].decode("utf-8", "replace") if proc.stderr else ""
        errors.append(proc.stderr[-4096:] if proc.stderr else b"")
        details["phases"].append({"name": script[:-3], "wall_seconds": time.monotonic() - phase_start,
                                  "returncode": proc.returncode, "stderr_tail": stderr_tail})
        if proc.returncode:
            (scratch / "stderr_tail.txt").write_bytes(b"".join(errors))
            raise ValueError(f"local renderer exited {proc.returncode}")
    if not output.is_file():
        raise ValueError("local renderer produced no pixels")
    return output.read_bytes()


def record(task, method, program, registry, timeout):
    expected = ("contract", "method_sha256", "prompt_sha256")
    if not isinstance(task, dict) or any(not isinstance(task.get(k), str) for k in ("id", "prompt", *expected)):
        raise ValueError("task requires string id, prompt, contract and digests")
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
    backend = task.get("backend", "local")
    if backend != "local":
        raise ValueError("this no-Docker runner supports backend=local")
    details = {"runtime_executable": str(runtime_executable()), "timeout": False, "phases": []}
    receipt = {"schema": "rsi.first_pass.v1", "status": "claimed",
               **{k: task[k] for k in (*expected, "id")}, "attempt": 1, "render_budget": 1,
               "reference_inputs": 0, "network": "unverified", "runtime_image": task.get("runtime_image", local_runtime()),
               "backend": backend, "sandbox": "host-provided-local-process",
               "method_entry": "method.render", "program_sha256": digest(program),
               "started_utc": datetime.now(timezone.utc).isoformat(), "runtime": details}
    save_json(case / "receipt.json", receipt)
    for filename, data in (("method.py", method), ("program.py", program), ("prompt.txt", prompt)):
        (case / filename).write_bytes(data)
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
            output = run_local(scratch, inputs, timeout, details)
            png, size = make_png(output)
            (case / "image.png").write_bytes(png)
            receipt.update(status="rendered", image_sha256=digest(png), size=list(size))
    except BaseException as error:
        receipt.update(status="failed", error=str(error), error_type=type(error).__name__)
        raise
    finally:
        receipt["wall_seconds"] = time.monotonic() - start
        receipt["ended_utc"] = datetime.now(timezone.utc).isoformat()
        receipt["runtime"] = details
        save_json(case / "receipt.json", receipt)
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
