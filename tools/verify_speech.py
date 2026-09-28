# 复用已锁定的本地 Paraformer，验证实际音频内容；不加入生产朗读链路。
import argparse
import json
from pathlib import Path
from funasr import AutoModel

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    lock = json.loads(
        (ROOT / "config/asr-validation.lock.json").read_text(encoding="utf-8")
    )
    model = AutoModel(
        model=lock["model_path"], device="cpu", disable_update=True, ncpu=4
    )
    results = []
    for path in args.audio:
        result = model.generate(input=path)
        results.append({"audio": path, "recognition": result})
    Path(args.output).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
