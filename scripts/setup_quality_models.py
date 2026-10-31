"""Download verified, public inference weights; no API keys or paid services."""
import hashlib
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    "data/models/face/yunet.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "data/models/asd/lr_asd.model": (
        "https://raw.githubusercontent.com/Junhua-Liao/LR-ASD/1b6dcd2d8fc2895683de6508ec6294ec47d388ca/weight/finetuning_TalkSet.model",
        "6b4ef53694e874e96cf630198dc479c78aebb3993bbf166aee3d926dfe7d9342"),
}


def main():
    for relative, (url, expected) in ASSETS.items():
        target = ROOT / relative
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            print(f"OK: {relative}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".download")
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                partial.write_bytes(response.read())
            if hashlib.sha256(partial.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"Checksum mismatch: {relative}")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
        print(f"Installed: {relative}")


if __name__ == "__main__":
    main()
