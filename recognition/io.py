"""Separate JSON artifacts for Phase 2."""
import json
from pathlib import Path


def save_results(results, evaluation, metrics, output_dir):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if results is not None:
        (output / "recognition_results.json").write_text(
            json.dumps(results, separators=(",", ":")) + "\n", encoding="utf-8")
    (output / "recognition_evaluation.json").write_text(
        json.dumps({"events": evaluation, "summary": metrics}, indent=2) + "\n", encoding="utf-8")


def stream_recognition_results(events, recognizer, output_dir):
    """Write detailed evidence incrementally, retaining only lightweight rows.

    Full candidate-by-reference evidence is intentionally large for a 10,000
    event experiment. Streaming prevents that evaluation artifact from becoming
    the process's working set while preserving every value in the JSON output.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    compact = []
    with (output / "recognition_results.json").open("w", encoding="utf-8") as handle:
        handle.write("[")
        for index, event in enumerate(events):
            result = recognizer.recognize(event).to_dict()
            if index:
                handle.write(",")
            json.dump(result, handle, separators=(",", ":"))
            compact.append({key: value for key, value in result.items()
                            if key != "candidate_evidence"})
        handle.write("]\n")
    return compact
