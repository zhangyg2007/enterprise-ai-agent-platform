"""Persist every selected case, including request failures, without reruns or overwrites."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import time

from .baseline import FIELDS, SCHEMA, SYSTEM_PROMPT, empty_score, load_cases, read_case_bytes, score_response
from .ollama import ModelError

DEFAULT_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 256}


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def revision() -> dict:
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=5, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                                   text=True, timeout=5, check=True).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


def checked_options(options: dict | None) -> dict:
    result = dict(DEFAULT_OPTIONS)
    if options:
        if set(options) - set(result):
            raise ValueError("未知生成参数")
        result.update(options)
    for key, maximum in (("num_ctx", 32768), ("num_predict", 8192)):
        if type(result[key]) is not int or not 1 <= result[key] <= maximum:
            raise ValueError(f"{key} 必须为 1..{maximum} 的整数")
    if type(result["seed"]) is not int or result["seed"] != 42:
        raise ValueError("本课固定 seed=42")
    if type(result["temperature"]) not in (int, float) or result["temperature"] != 0:
        raise ValueError("本课固定 temperature=0")
    return result


def summarize(records: list[dict], *, identity_stable: bool, is_subset: bool) -> dict:
    n = len(records)
    latencies = sorted(record["wall_seconds"] for record in records)
    scores = [record["score"] for record in records]
    request_errors = sum(record["error"] is not None for record in records)
    return {
        "total": n, "request_errors": request_errors,
        "json_valid_rate": sum(s["json_valid"] for s in scores) / n,
        "schema_valid_rate": sum(s["schema_valid"] for s in scores) / n,
        "exact_match_rate": sum(s["exact_match"] for s in scores) / n,
        "field_accuracy": {key: sum(s["field_correct"][key] for s in scores) / n for key in FIELDS},
        "failures": dict(Counter(s["failure"] for s in scores if s["failure"])),
        "latency_seconds": {"mean": statistics.mean(latencies),
                            "p50": latencies[math.ceil(n * 0.50) - 1],
                            "p95": latencies[math.ceil(n * 0.95) - 1]},
        "identity_stable": identity_stable, "is_subset": is_subset,
        "comparison_eligible": identity_stable and not is_subset and request_errors == 0,
    }


def run_evaluation(client, *, model: str, cases_path: Path, output_dir: Path,
                   limit: int | None = None, think: bool | None = None,
                   options: dict | None = None) -> dict:
    options = checked_options(options)
    if think is not None and type(think) is not bool:
        raise ValueError("think 必须为 None/True/False")
    source_bytes = read_case_bytes(cases_path)
    cases = load_cases(cases_path, raw=source_bytes)
    if limit is not None and (type(limit) is not int or not 1 <= limit <= len(cases)):
        raise ValueError("limit 必须在 1 与题集题数之间")
    selected = cases if limit is None else cases[:limit]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "status": "running", "started_at": utc_now(), "finished_at": None,
        "model": model, "source_cases_sha256": digest(source_bytes),
        "cases_sha256": digest(encoded(selected)), "selected_count": len(selected), "source_count": len(cases),
        "prompt_sha256": digest(SYSTEM_PROMPT.encode("utf-8")), "schema_sha256": digest(encoded(SCHEMA)),
        "options": options, "think": think, "keep_alive": "5m", "stream": False,
        "base_url": client.base_url, "timeout_seconds": client.timeout_seconds,
        "python": platform.python_version(), "platform": platform.platform(), "code": revision(),
    }
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "cases.json", selected)
    write_json(output_dir / "request-contract.json", {"system_prompt": SYSTEM_PROMPT, "schema": SCHEMA})
    before = client.inventory(model)
    write_json(output_dir / "inventory-before.json", before)
    records = []
    with (output_dir / "records.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for case in selected:
            started = time.perf_counter()
            raw, error = None, None
            try:
                raw = client.chat(model, [{"role": "system", "content": SYSTEM_PROMPT},
                                          {"role": "user", "content": case["input"]}],
                                  format_schema=SCHEMA, options=options, think=think)
            except ModelError as exc:
                error = {"code": exc.code, "message": str(exc), "status": exc.status}
                raw = exc.raw
            elapsed = time.perf_counter() - started
            score = empty_score(error["code"]) if error else score_response(raw, case["expected"])
            speed = None
            if not error:
                count, duration = raw.get("eval_count"), raw.get("eval_duration")
                if type(count) is int and count > 0 and type(duration) is int and duration > 0:
                    try:
                        speed = count * 1_000_000_000 / duration
                        if not math.isfinite(speed):
                            speed = None
                    except OverflowError:
                        speed = None
            record = {"id": case["id"], "category": case["category"], "input": case["input"],
                      "expected": case["expected"], "raw": raw, "error": error, "score": score,
                      "wall_seconds": elapsed, "eval_tokens_per_second": speed}
            stream.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            records.append(record)
    try:
        after = client.inventory(model)
        write_json(output_dir / "inventory-after.json", after)
        identity_stable = all(before.get(key) == after.get(key)
                              for key in ("model", "digest", "server_version", "template_sha256"))
    except ModelError as exc:
        identity_stable = False
        write_json(output_dir / "inventory-after.json", {"error": exc.code, "message": str(exc)})
    summary = summarize(records, identity_stable=identity_stable, is_subset=len(selected) < len(cases))
    write_json(output_dir / "summary.json", summary)
    manifest.update(status="completed", finished_at=utc_now(), artifacts={
        path.name: digest(path.read_bytes()) for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "manifest.json"
    })
    write_json(output_dir / "manifest.json", manifest)
    return summary
