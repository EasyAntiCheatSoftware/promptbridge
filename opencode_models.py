"""Public OpenCode text-model catalog and atomic, private model preferences."""
import json
from decimal import Decimal, InvalidOperation
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
    if value.upper().replace("-", "_") in {"MODEL_ID", "YOUR_MODEL_ID", "YOUR_MODEL"}:
        raise ValueError("MODEL_ID is an example placeholder, not a model. Run claude-openapi --opencode-select-model to choose a real model.")
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


def picker_settings(catalog, selected, free_only=False):
    """A native lineup: discovery itself drops IDs without claude/anthropic."""
    entries = {item["id"]: item for item in chat_models({"data": catalog})}
    entries.setdefault(selected, {"id": model_id(selected)})
    if free_only:
        entries = {key: item for key, item in entries.items() if is_free(item)}
    options = []
    for item in sorted(entries.values(), key=lambda item: (not is_free(item), model_name(item).casefold(), item["id"])):
        pricing = "FREE" if is_free(item) else "PAID/UNKNOWN"
        options.append({"model": item["id"], "label": model_name(item) + " [" + pricing + "]",
                        "description": item["id"] + " | Catalog pricing only; provider access required"})
    return {"replaceBuiltInOptions": True, "options": options}


def save_picker_settings(state, catalog, selected, free_only=False, *, default_model=None):
    """Update only the isolated user's lineup, preserving other native settings."""
    directory = state / "claude"
    if directory.is_symlink():
        raise ValueError("Private Claude settings directory is a symlink; settings preserved.")
    path = directory / "settings.json"
    settings = {}
    if path.exists():
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Private Claude settings exceed 1 MiB; settings preserved.")
        settings = json.loads(path.read_text())
        if not isinstance(settings, dict):
            raise ValueError("Private Claude settings must be a JSON object; settings preserved.")
    settings["modelPicker"] = picker_settings(catalog, selected, free_only)
    if default_model is not None:
        # The wrapper's saved preference is authoritative, not an older native
        # selection. A per-run --model override must never become this default.
        settings["model"] = model_id(default_model)
    atomic_json(directory, "settings.json", settings)


def native_saved_model(state):
    path = state / "claude/settings.json"
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        return None
    settings = json.loads(path.read_text())
    value = settings.get("model") if isinstance(settings, dict) else None
    try:
        return model_id(value) if value is not None else None
    except ValueError:
        return None  # Native aliases/old placeholders are not provider IDs.


def cached_catalog(directory: Path):
    try:
        path = directory / "models-cache.json"
        if path.stat().st_size > MAX_CATALOG_BYTES:
            return []
        return chat_models(json.loads(path.read_text(), parse_float=str))
    except (OSError, ValueError):
        return []


def fetch_catalog(directory: Path):
    # Public metadata only: no provider credential or conversation is sent.
    request = urllib.request.Request(CATALOG_URL, headers={"User-Agent": "promptbridge/1.1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(MAX_CATALOG_BYTES + 1)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError("OpenCode catalog exceeds the metadata size limit.")
    # Retain exact decimal text: a tiny nonzero JSON number must not round to 0.
    models = chat_models(json.loads(raw, parse_float=str))
    if not models:
        raise ValueError("OpenCode catalog contains no usable text/chat models.")
    atomic_json(directory, "models-cache.json", {"data": models})
    return models


def price(value):
    try:
        if isinstance(value, bool):
            return None
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
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
