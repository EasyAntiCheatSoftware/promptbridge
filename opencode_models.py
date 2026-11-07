"""Public OpenCode text-model catalog and atomic, private model preferences."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
import urllib.request

CATALOG_URL = "https://opencode.ai/inference/v1/models"
MAX_CATALOG_BYTES = 8 * 1024 * 1024
ALIASES = {"default", "inherit", "best", "sonnet", "opus", "haiku", "fable", "opusplan"}


def model_id(value):
    if not isinstance(value, str):
        raise ValueError("Model ID must be text.")
    value = value.strip()
    if value.startswith("opencode/"):
        value = value[len("opencode/"):]
    if value.endswith("[1m]"):
        value = value[:-4]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}", value) or value in ALIASES:
        raise ValueError("Use an explicit OpenCode model ID, such as big-pickle.")
    return value


def atomic_json(directory: Path, name: str, data):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    target = directory / name
    if target.is_symlink():
        raise ValueError("Model settings/cache path is a symlink; existing files preserved.")
    fd, path = tempfile.mkstemp(prefix=".models-", dir=directory)
    staged = Path(path)
    try:
        with os.fdopen(fd, "w") as file:
            os.fchmod(file.fileno(), 0o600)
            json.dump(data, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


def saved_model(directory: Path):
    path = directory / "model.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("Invalid saved model settings; use --opencode-set-model to replace them.") from exc
    if not isinstance(data, dict):
        raise ValueError("Saved model settings must be a JSON object.")
    return model_id(data.get("model"))


def chat_models(data):
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("OpenCode catalog must contain a data array.")
    models = {}
    for item in data["data"]:
        if not isinstance(item, dict) or item.get("type", "chat") != "chat":
            continue
        modalities = item.get("modalities", {})
        if isinstance(modalities, dict) and modalities.get("output"):
            outputs = modalities["output"]
            if not isinstance(outputs, list) or "text" not in outputs:
                continue
        try:
            identifier = model_id(item.get("id"))
        except ValueError:
            continue
        models[identifier] = dict(item, id=identifier)
    return list(models.values())


def model_name(item):
    value = item.get("name")
    if not isinstance(value, str):
        return item["id"]
    return "".join(character for character in value if character.isprintable()) or item["id"]


def cached_catalog(directory: Path):
    try:
        path = directory / "models-cache.json"
        if path.stat().st_size > MAX_CATALOG_BYTES:
            return []
        return chat_models(json.loads(path.read_text()))
    except (OSError, ValueError):
        return []


def fetch_catalog(directory: Path):
    # Public metadata only: no provider credential or conversation is sent.
    request = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "promptbridge/1.1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(MAX_CATALOG_BYTES + 1)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("OpenCode catalog exceeds the metadata size limit.")
    models = chat_models(json.loads(raw))
    if not models:
        raise ValueError("OpenCode catalog contains no usable text/chat models.")
    atomic_json(directory, "models-cache.json", {"data": models})
    return models


def price(value):
    try:
        if isinstance(value, bool):
            return None
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def is_free(item):
    try:
        text = item["cost"]["text"]
        if any(price(text[kind]["price"]) != 0 for kind in ("input", "output")):
            return False
        for kind in ("input", "output"):
            for value in text[kind].get("cache", {}).values():
                if price(value) != 0:
                    return False
        return True
    except (KeyError, TypeError, AttributeError):
        return False


def context_tokens(item):
    try:
        limit = item.get("limit", {})
        value = limit.get("input") or limit.get("context")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None
        return value
    except AttributeError:
        return None
