#!/usr/bin/env python3
"""Provider-aware image generation CLI.

The implementation keeps the historical ``gpt_image_api`` module name out of
the public entrypoint while accepting the same environment variables for
backward compatibility.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from io import BytesIO
import json
from math import gcd
import os
import re
import sys
import time
import mimetypes
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import quote


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_RESPONSES_MODEL = "gpt-5.4"
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "low"
DEFAULT_FORMAT = "png"
DEFAULT_TIMEOUT = 180
DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_SIZE_POLICY = "normalize"
DEFAULT_USER_AGENT = "gpt-image-client/1.0"
DEFAULT_PROVIDER_PROFILE = "auto"
DEFAULT_ROUTING_MODE = "auto"
DEFAULT_TOOL_MODEL_POLICY = "auto"
MODEL_CATALOG_SCHEMA_VERSION = 1
SCRIPT_VERSION = "1.0.0"
MODEL_CATALOG_FILENAME = "image-generation-api-model-catalog.json"
# Short, human-friendly references are accepted at the command boundary and
# always resolved to an auditable provider model id before a request is built.
# Keep this map deliberately small: it is a convenience layer, not a second
# model registry.
MODEL_ALIASES = {
    "gpt2": "gpt-image-2",
    "gpt-2": "gpt-image-2",
    "gpt25": "gpt-image-2.5",
    "gpt-2.5": "gpt-image-2.5",
    "gpt2.5": "gpt-image-2.5",
    "gpt4k": "gpt-image-2-4k",
    "gpt-4k": "gpt-image-2-4k",
    "grok": "grok-imagine-image",
    "grok1": "grok-imagine-image",
    "grok-1": "grok-imagine-image",
    "grok2": "grok-imagine-image-2.0",
    "grok-2": "grok-imagine-image-2.0",
    "grok-quality": "grok-imagine-image-quality",
    "grokquality": "grok-imagine-image-quality",
}


def default_model_catalog_path() -> Path:
    """Return the per-user catalog path even in stripped test environments."""
    try:
        home = Path.home()
    except RuntimeError:
        # Some callers deliberately clear HOME/USERPROFILE while testing
        # environment precedence. Keep resolution deterministic and usable.
        home_text = os.environ.get("USERPROFILE") or os.environ.get("HOME")
        if not home_text:
            home_text = os.environ.get("HOMEDRIVE", "") + os.environ.get("HOMEPATH", "")
        home = Path(home_text) if home_text else Path.cwd()
    return home / ".codex" / MODEL_CATALOG_FILENAME
STABLE_MODEL_STATUSES = frozenset({"primary", "official_model"})
UNVERIFIED_MODEL_STATUSES = frozenset(
    {"retired_alias", "gateway_alias_unverified", "unverified_alias", "listed_unknown"}
)
ENABLED_IMAGE_VENDORS = ("openai", "xai")
ENABLED_IMAGE_FAMILIES = ("gpt-image-*", "grok-imagine-image*")
SIZE_PATTERN = re.compile(r"^\d+x\d+$")
USER_AGENT_PATTERN = re.compile(r"^[\x20-\x7E]{1,256}$")
IMAGE_ID_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:^|[-_])image(?:[-_]|$)",
        r"(?:^|[-_])img(?:[-_]|$)",
        r"imagen",
        r"dall[-_]?e",
        r"nano[-_]?banana",
        r"flux",
        r"auraflow|hidream|playground[-_]?v|realvis|dreamshaper|proteus",
        r"pixverse|idefics[-_]?image|mochi[-_]?image|cosmos[-_]?image",
        r"stable[-_]?diffusion",
        r"sdxl|sd3|stable[-_]?cascade|stable[-_]?image",
        r"grok[-_]?imagine",
        r"seedream",
        r"seed[-_]?edit|seed[-_]?image",
        r"hunyuan[-_]?image",
        r"firefly",
        r"ideogram",
        r"midjourney|\bmj[-_]",
        r"recraft",
        r"nova[-_]?canvas",
        r"qwen[-_]?image",
        r"wan[-_]?image",
        r"wan\d",
        r"janus|emu[-_]?3|mousi",
        r"sana|lumina[-_]?image|omnigen|bagel",
        r"kolors",
        r"pixart",
        r"playground",
        r"cogview",
        r"cogvideox",
        r"doubao",
        r"spark[-_]?image",
        r"ernie[-_]?image",
        r"hunyuan[-_]?image",
        r"imagen[-_]?",
        r"ernie[-_]?image",
        r"kling[-_]?image",
        r"leonardo[-_]?image",
        r"pixverse[-_]?image|pixverse[-_]",
        r"luma[-_]?image|dream[-_]?machine",
        r"minimax[-_]?image",
        r"z[-_]?image|zhipu[-_]?image",
    )
]
VIDEO_ID_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:^|[-_])video(?:[-_]|$)",
        r"(?:^|[-_])vid(?:[-_]|$)",
        r"(?:^|[-_])(?:t2v|i2v|v2v)(?:[-_]|$)",
        r"(?:text|image|img)[-_]?to[-_]?video",
        r"(?:^|[-_])(?:sora|veo)(?:[-_]|$)",
        r"(?:^|[-_])cogvideo(?:x)?(?:[-_]|$)",
        r"(?:^|[-_])kling[-_]?video(?:[-_]|$)",
        r"(?:^|[-_])wan[-_]?video(?:[-_]|$)",
        r"(?:^|[-_])runway[-_]?gen(?:[-_]|$)",
        r"grok[-_]?imagine[-_]?video",
    )
]
VENDOR_LABELS = {
    "openai": "OpenAI",
    "xai": "xAI / Grok",
    "google": "Google / Gemini",
    "stability": "Stability AI",
    "black-forest-labs": "Black Forest Labs",
    "midjourney": "Midjourney",
    "ideogram": "Ideogram",
    "alibaba": "Alibaba / Qwen",
    "bytedance": "ByteDance / Seed",
    "tencent": "Tencent",
    "baidu": "Baidu",
    "amazon": "Amazon",
    "adobe": "Adobe",
    "leonardo": "Leonardo AI",
    "recraft": "Recraft",
    "runway": "Runway",
    "minimax": "MiniMax",
    "kuaishou": "Kuaishou / Kling",
    "luma": "Luma AI",
    "pika": "Pika",
    "kolors": "Kuaishou / Kolors",
    "pixart": "PixArt",
    "playground": "Playground AI",
    "doubao": "ByteDance / Doubao",
    "cogview": "Zhipu / CogView",
    "spark": "iFlytek / Spark",
    "deepseek": "DeepSeek",
    "zhipu": "Zhipu AI",
    "microsoft": "Microsoft",
    "nvidia": "NVIDIA",
    "huggingface": "Hugging Face",
    "generic": "Unknown / gateway models",
}
VENDOR_PRIORITY = [
    "openai",
    "xai",
    "google",
    "black-forest-labs",
    "stability",
    "alibaba",
    "bytedance",
    "midjourney",
    "ideogram",
    "tencent",
    "baidu",
    "amazon",
    "adobe",
    "leonardo",
    "recraft",
    "runway",
    "minimax",
    "kuaishou",
    "luma",
    "pika",
    "deepseek",
    "zhipu",
    "microsoft",
    "nvidia",
    "huggingface",
    "generic",
]
VENDOR_RULES = [
    ("openai", ("openai", "gpt-image", "dall-e", "dalle", "chatgpt-image")),
    ("xai", ("xai", "grok")),
    ("google", ("google", "imagen", "gemini-image")),
    ("stability", ("stability", "stable-diffusion", "sdxl", "sd3")),
    ("black-forest-labs", ("black-forest", "black forest", "blackforest", "bfl", "flux")),
    ("midjourney", ("midjourney", "mid-journey")),
    ("ideogram", ("ideogram",)),
    ("alibaba", ("alibaba", "qwen-image", "qwen_image", "wan-image", "wan_image")),
    ("bytedance", ("bytedance", "seedream", "seed-dream", "seedream")),
    ("tencent", ("tencent", "hunyuan-image", "hunyuan_image")),
    ("baidu", ("baidu", "ernie-image", "wenxin")),
    ("amazon", ("amazon", "nova-canvas", "nova_canvas")),
    ("adobe", ("adobe", "firefly")),
    ("leonardo", ("leonardo",)),
    ("recraft", ("recraft",)),
    ("runway", ("runway",)),
    ("minimax", ("minimax", "mini-max")),
    ("kuaishou", ("kuaishou", "kling-image", "kling_image")),
    ("luma", ("luma", "dream-machine")),
    ("pika", ("pika",)),
    ("kolors", ("kolors",)),
    ("pixart", ("pixart",)),
    ("playground", ("playground",)),
    ("doubao", ("doubao",)),
    ("cogview", ("cogview", "zhipu", "chatglm")),
    ("spark", ("spark-image", "spark_image", "iflytek", "科大讯飞")),
    ("deepseek", ("deepseek",)),
    ("zhipu", ("zhipu", "智谱")),
    ("microsoft", ("microsoft", "azure")),
    ("nvidia", ("nvidia",)),
    ("huggingface", ("huggingface", "hugging-face")),
]
MODEL_METADATA_VENDOR_KEYS = (
    "vendor",
    "provider",
    "owned_by",
    "ownedBy",
    "organization",
    "publisher",
    "creator",
    "company",
    "vendor_id",
    "vendorId",
    "vendor_name",
    "vendorName",
    "provider_id",
    "providerId",
    "provider_name",
    "providerName",
    "provider_profile",
    "providerProfile",
)
MODEL_METADATA_IMAGE_KEYS = (
    "image_generation",
    "imageGeneration",
    "image_generation_capability",
    "imageGenerationCapability",
    "output_modalities",
    "outputModalities",
    "modalities",
    "capabilities",
    "tasks",
    "type",
    "modality",
    "supported_endpoints",
    "supportedEndpoints",
    "endpoints",
    "endpoint",
    "features",
    "supported_tasks",
    "supportedTasks",
    "capability",
    "capability_map",
    "capabilityMap",
)
QUALITY_VALUES = ["auto", "low", "medium", "high"]
FORMAT_VALUES = ["png", "jpeg", "webp"]
SIZE_VALUES = [
    "auto",
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "2048x2048",
    "2048x1152",
    "1152x2048",
    "3840x2160",
    "2160x3840",
]
SIZE_POLICY_VALUES = ["normalize", "strict", "provider"]
# ``generic`` remains a parser/configuration compatibility value so older
# callers can receive a deterministic "not enabled" error. It is not an
# executable provider profile in the current two-provider scope.
PROVIDER_PROFILE_VALUES = ["auto", "openai", "grok", "generic"]
EXECUTABLE_PROVIDER_PROFILES = ("openai", "grok")
ROUTING_MODE_VALUES = ["auto", "draft", "final", "4k", "transparent", "grok-wide", "grok-quality"]
TOOL_MODEL_POLICY_VALUES = ["auto", "include", "omit"]
BACKGROUND_VALUES = ["auto", "opaque", "transparent"]
MODERATION_VALUES = ["auto", "low"]
RESPONSE_FORMAT_VALUES = ["url", "b64_json"]
GROK_RESOLUTION_VALUES = ["1k", "2k"]
GROK_ASPECT_RATIO_VALUES = [
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "19.5:9",
    "9:19.5",
    "20:9",
    "9:20",
    "21:9",
    "5:2",
]
GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_MAX_EDGE = 3840
SENSITIVE_KEY_PATTERN = re.compile(r"(api[_-]?key|authorization|bearer|token|secret|password)", re.IGNORECASE)
WINDOWS_USER_ENV_NAMES = (
    "IMAGE_GENERATION_API_KEY",
    "IMAGE_GENERATION_BASE_URL",
    "IMAGE_GENERATION_MODEL",
    "IMAGE_GENERATION_VENDOR",
    "IMAGE_GENERATION_RESPONSES_MODEL",
    "IMAGE_GENERATION_TOOL_MODEL",
    "IMAGE_GENERATION_TIMEOUT",
    "IMAGE_GENERATION_RETRIES",
    "IMAGE_GENERATION_RETRY_DELAY",
    "IMAGE_GENERATION_USER_AGENT",
    "IMAGE_GENERATION_PROVIDER_PROFILE",
    "IMAGE_GENERATION_ROUTING_MODE",
    "IMAGE_GENERATION_TOOL_MODEL_POLICY",
    "IMAGE_GENERATION_MODEL_CATALOG",
    "GPT_IMAGE_API_KEY",
    "GPT_IMAGE_BASE_URL",
    "GPT_IMAGE_MODEL",
    "GPT_IMAGE_RESPONSES_MODEL",
    "GPT_IMAGE_TOOL_MODEL",
    "GPT_IMAGE_TIMEOUT",
    "GPT_IMAGE_RETRIES",
    "GPT_IMAGE_RETRY_DELAY",
    "GPT_IMAGE_USER_AGENT",
    "GPT_IMAGE_PROVIDER_PROFILE",
    "GPT_IMAGE_ROUTING_MODE",
    "GPT_IMAGE_TOOL_MODEL_POLICY",
    "GPT_IMAGE_VENDOR",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_IMAGE_MODEL",
    "OPENAI_RESPONSES_MODEL",
    "OPENAI_IMAGE_TOOL_MODEL",
    "OPENAI_IMAGE_TIMEOUT",
    "OPENAI_IMAGE_VENDOR",
)
PRESETS: dict[str, dict[str, Any]] = {
    "fast": {"size": "1024x1024", "quality": "low", "output_format": "png"},
    "standard": {"size": "1024x1024", "quality": "medium", "output_format": "png"},
    "quality": {"size": "1024x1024", "quality": "high", "output_format": "png"},
    "final": {"size": "2048x2048", "quality": "high", "output_format": "png"},
    "square-2k": {"size": "2048x2048", "quality": "high", "output_format": "png"},
    "landscape-2k": {"size": "2048x1152", "quality": "high", "output_format": "webp"},
    "portrait-2k": {"size": "1152x2048", "quality": "high", "output_format": "webp"},
    "landscape-4k": {"size": "3840x2160", "quality": "high", "output_format": "webp"},
    "portrait-4k": {"size": "2160x3840", "quality": "high", "output_format": "webp"},
    "transparent": {
        "size": "1024x1024",
        "quality": "high",
        "output_format": "png",
        "background": "transparent",
    },
    "wide": {"size": "1536x1024", "quality": "low", "output_format": "webp"},
    "tall": {"size": "1024x1536", "quality": "low", "output_format": "webp"},
    "social": {"size": "1024x1024", "quality": "medium", "output_format": "webp"},
}

ROUTING_MODES: dict[str, dict[str, Any]] = {
    "auto": {},
    "draft": {
        "model": "grok-imagine-image",
        "size": "1024x1024",
        "quality": "low",
        "output_format": "webp",
        "aspect_ratio": "1:1",
        "resolution": "1k",
    },
    "final": {"model": "gpt-image-2", "size": "2048x2048", "quality": "high", "output_format": "png"},
    "4k": {"model": "gpt-image-2", "size": "3840x2160", "quality": "high", "output_format": "webp"},
    "transparent": {
        "model": "gpt-image-2",
        "size": "1024x1024",
        "quality": "high",
        "output_format": "png",
        "background": "transparent",
    },
    "grok-wide": {
        "model": "grok-imagine-image-2.0",
        "size": "2048x1152",
        "quality": "medium",
        "output_format": "webp",
        "aspect_ratio": "16:9",
        "resolution": "2k",
    },
    "grok-quality": {
        "model": "grok-imagine-image-quality",
        "size": "2048x1152",
        "quality": "high",
        "output_format": "webp",
        "aspect_ratio": "16:9",
        "resolution": "2k",
    },
}

MODEL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "gpt-image-2": {
        "provider_profile": "openai",
        "status": "primary",
        "generation": True,
        "editing": True,
        "transparent_background": True,
        "streaming": True,
        "max_resolution": "3840px edge",
        "recommended_for": "final images, text, masks, references, transparent output",
    },
    "gpt-image-2-4k": {
        "provider_profile": "openai",
        "status": "gateway_alias_unverified",
        "generation": True,
        "editing": None,
        "transparent_background": None,
        "streaming": None,
        "max_resolution": "gateway-defined",
        "recommended_for": "explicit gateway 4K tests only",
    },
    "grok-imagine-image": {
        "provider_profile": "grok",
        "status": "official_model",
        "generation": True,
        "editing": True,
        "transparent_background": False,
        "streaming": False,
        "max_resolution": "2k",
        "recommended_for": "drafts and lower-cost exploration",
    },
    "grok-imagine-image-2.0": {
        "provider_profile": "grok",
        "status": "official_model",
        "generation": True,
        "editing": True,
        "transparent_background": False,
        "streaming": False,
        "max_resolution": "2k",
        "recommended_for": "wide compositions and alternate visual style",
    },
    "grok-imagine-image-quality": {
        "provider_profile": "grok",
        "status": "official_model",
        "generation": True,
        "editing": True,
        "transparent_background": False,
        "streaming": False,
        "max_resolution": "2k",
        "recommended_for": "explicit Grok quality route",
    },
    "grok-imagine-image-pro": {
        "provider_profile": "grok",
        "status": "retired_alias",
        "generation": True,
        "editing": True,
        "transparent_background": False,
        "streaming": False,
        "max_resolution": "gateway-defined",
        "recommended_for": "do not auto-route; use grok-imagine-image-quality",
    },
    "grok-imagine-image-edit": {
        "provider_profile": "grok",
        "status": "gateway_alias_unverified",
        "generation": None,
        "editing": True,
        "transparent_background": None,
        "streaming": None,
        "max_resolution": "gateway-defined",
        "recommended_for": "explicit edit tests only",
    },
    "grok-imagine-image-lite": {
        "provider_profile": "grok",
        "status": "gateway_alias_unverified",
        "generation": True,
        "editing": None,
        "transparent_background": None,
        "streaming": None,
        "max_resolution": "gateway-defined",
        "recommended_for": "explicit low-cost tests only",
    },
    "grok-imagine-image-quality-lite": {
        "provider_profile": "grok",
        "status": "gateway_alias_unverified",
        "generation": True,
        "editing": None,
        "transparent_background": None,
        "streaming": None,
        "max_resolution": "gateway-defined",
        "recommended_for": "explicit gateway tests only",
    },
}


class OutputProcessingError(Exception):
    def __init__(self, category: str, summary: str, details: dict[str, Any] | None = None):
        super().__init__(summary)
        self.category = category
        self.summary = summary
        self.details = details or {}


class ModelSelectionRequired(Exception):
    """Raised before a paid request when live discovery found multiple choices."""

    def __init__(self, details: dict[str, Any]):
        super().__init__(details.get("summary", "An image model selection is required."))
        self.details = details


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    if path is None:
        current = Path.cwd().resolve()
        candidates = [current / ".env", *(parent / ".env" for parent in current.parents)]
        path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None or not path.exists():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _model_alias_key(value: Any) -> str:
    """Normalize only the friendly alias spelling, not the provider id."""
    return re.sub(r"[\s_.-]+", "", str(value).strip().lower())


def canonical_model_reference(value: Any) -> tuple[str, str | None]:
    """Return ``(provider_model_id, alias_used)`` for a user model reference."""
    raw = str(value or "").strip()
    if not raw:
        return raw, None
    if "=" in raw:
        prefix, suffix = raw.split("=", 1)
        if prefix.strip().lower() in {"model", "choice", "select", "模型", "选择"}:
            raw = suffix.strip()
    if not raw:
        return raw, None
    target = MODEL_ALIASES.get(raw.lower())
    if target is None:
        compact = _model_alias_key(raw)
        target = next(
            (candidate for alias, candidate in MODEL_ALIASES.items() if _model_alias_key(alias) == compact),
            None,
        )
    if target is None:
        return raw, None
    return target, raw if raw.lower() != target.lower() else None


def model_aliases_for_model(model: Any) -> list[str]:
    """List stable shorthand aliases for a canonical model id."""
    model_text = str(model).strip().lower()
    aliases: list[str] = []
    for alias, target in MODEL_ALIASES.items():
        if target.lower() == model_text and alias.lower() != model_text and alias not in aliases:
            aliases.append(alias)
    return aliases


def load_windows_user_environment() -> dict[str, str]:
    disable_user_env = first_value(
        os.environ.get("IMAGE_GENERATION_DISABLE_USER_ENV"),
        os.environ.get("GPT_IMAGE_DISABLE_USER_ENV"),
        "",
    )
    if os.name != "nt" or str(disable_user_env).lower() in {"1", "true", "yes"}:
        return {}
    try:
        import winreg
    except ImportError:
        return {}

    values: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            for name in WINDOWS_USER_ENV_NAMES:
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if value not in (None, ""):
                    values[name] = str(value)
    except OSError:
        return {}
    return values


def validate_user_agent(user_agent: str) -> None:
    if not USER_AGENT_PATTERN.fullmatch(user_agent):
        raise SystemExit("user_agent must contain 1-256 printable ASCII characters without newlines.")


def validate_choice(value: str, allowed: list[str], label: str) -> None:
    if value not in allowed:
        raise SystemExit(f"{label} must be one of: {', '.join(allowed)}.")


def resolve_config(args: argparse.Namespace) -> dict[str, Any]:
    dotenv = load_dotenv(Path(args.env_file).expanduser().resolve() if args.env_file else None)
    user_env = load_windows_user_environment()

    api_key = first_value(
        getattr(args, "api_key", None),
        os.environ.get("IMAGE_GENERATION_API_KEY"),
        user_env.get("IMAGE_GENERATION_API_KEY"),
        os.environ.get("GPT_IMAGE_API_KEY"),
        user_env.get("GPT_IMAGE_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
        user_env.get("OPENAI_API_KEY"),
        dotenv.get("IMAGE_GENERATION_API_KEY"),
        dotenv.get("GPT_IMAGE_API_KEY"),
        dotenv.get("OPENAI_API_KEY"),
    )
    base_url = str(
        first_value(
            getattr(args, "base_url", None),
            os.environ.get("IMAGE_GENERATION_BASE_URL"),
            user_env.get("IMAGE_GENERATION_BASE_URL"),
            os.environ.get("GPT_IMAGE_BASE_URL"),
            user_env.get("GPT_IMAGE_BASE_URL"),
            os.environ.get("OPENAI_BASE_URL"),
            user_env.get("OPENAI_BASE_URL"),
            dotenv.get("IMAGE_GENERATION_BASE_URL"),
            dotenv.get("GPT_IMAGE_BASE_URL"),
            dotenv.get("OPENAI_BASE_URL"),
            DEFAULT_BASE_URL,
        )
    ).rstrip("/")
    configured_model = first_value(
        os.environ.get("IMAGE_GENERATION_MODEL"),
        user_env.get("IMAGE_GENERATION_MODEL"),
        os.environ.get("GPT_IMAGE_MODEL"),
        user_env.get("GPT_IMAGE_MODEL"),
        os.environ.get("OPENAI_IMAGE_MODEL"),
        user_env.get("OPENAI_IMAGE_MODEL"),
        dotenv.get("IMAGE_GENERATION_MODEL"),
        dotenv.get("GPT_IMAGE_MODEL"),
        dotenv.get("OPENAI_IMAGE_MODEL"),
    )
    # A model stored in an old environment variable is retained for diagnostics,
    # but it must not change the no-choice behavior. The global default is an
    # invariant: only a command-scoped model, catalog choice, or routing mode can
    # select something other than gpt-image-2.
    explicit_model = getattr(args, "model", None)
    raw_model = first_value(explicit_model, DEFAULT_MODEL)
    model, model_alias = canonical_model_reference(raw_model)
    model_source = "cli" if explicit_model else "default"
    responses_model = first_value(
        getattr(args, "responses_model", None),
        getattr(args, "model", None) if getattr(args, "command", None) == "responses" else None,
        os.environ.get("IMAGE_GENERATION_RESPONSES_MODEL"),
        user_env.get("IMAGE_GENERATION_RESPONSES_MODEL"),
        os.environ.get("GPT_IMAGE_RESPONSES_MODEL"),
        user_env.get("GPT_IMAGE_RESPONSES_MODEL"),
        os.environ.get("OPENAI_RESPONSES_MODEL"),
        user_env.get("OPENAI_RESPONSES_MODEL"),
        dotenv.get("IMAGE_GENERATION_RESPONSES_MODEL"),
        dotenv.get("GPT_IMAGE_RESPONSES_MODEL"),
        dotenv.get("OPENAI_RESPONSES_MODEL"),
        DEFAULT_RESPONSES_MODEL,
    )
    configured_tool_model = first_value(
        os.environ.get("IMAGE_GENERATION_TOOL_MODEL"),
        user_env.get("IMAGE_GENERATION_TOOL_MODEL"),
        os.environ.get("GPT_IMAGE_TOOL_MODEL"),
        user_env.get("GPT_IMAGE_TOOL_MODEL"),
        os.environ.get("OPENAI_IMAGE_TOOL_MODEL"),
        user_env.get("OPENAI_IMAGE_TOOL_MODEL"),
        dotenv.get("IMAGE_GENERATION_TOOL_MODEL"),
        dotenv.get("GPT_IMAGE_TOOL_MODEL"),
        dotenv.get("OPENAI_IMAGE_TOOL_MODEL"),
    )
    explicit_tool_model = getattr(args, "tool_model", None)
    raw_tool_model = first_value(explicit_tool_model, DEFAULT_MODEL)
    tool_model, tool_model_alias = canonical_model_reference(raw_tool_model)
    tool_model_source = "cli" if explicit_tool_model else "default"
    timeout = int(
        first_value(
            getattr(args, "timeout", None),
            os.environ.get("IMAGE_GENERATION_TIMEOUT"),
            user_env.get("IMAGE_GENERATION_TIMEOUT"),
            os.environ.get("GPT_IMAGE_TIMEOUT"),
            user_env.get("GPT_IMAGE_TIMEOUT"),
            os.environ.get("OPENAI_IMAGE_TIMEOUT"),
            user_env.get("OPENAI_IMAGE_TIMEOUT"),
            dotenv.get("IMAGE_GENERATION_TIMEOUT"),
            dotenv.get("GPT_IMAGE_TIMEOUT"),
            dotenv.get("OPENAI_IMAGE_TIMEOUT"),
            DEFAULT_TIMEOUT,
        )
    )
    retries = int(
        first_value(
            getattr(args, "retries", None),
            os.environ.get("IMAGE_GENERATION_RETRIES"),
            user_env.get("IMAGE_GENERATION_RETRIES"),
            os.environ.get("GPT_IMAGE_RETRIES"),
            user_env.get("GPT_IMAGE_RETRIES"),
            dotenv.get("IMAGE_GENERATION_RETRIES"),
            dotenv.get("GPT_IMAGE_RETRIES"),
            DEFAULT_RETRIES,
        )
    )
    retry_delay = float(
        first_value(
            getattr(args, "retry_delay", None),
            os.environ.get("IMAGE_GENERATION_RETRY_DELAY"),
            user_env.get("IMAGE_GENERATION_RETRY_DELAY"),
            os.environ.get("GPT_IMAGE_RETRY_DELAY"),
            user_env.get("GPT_IMAGE_RETRY_DELAY"),
            dotenv.get("IMAGE_GENERATION_RETRY_DELAY"),
            dotenv.get("GPT_IMAGE_RETRY_DELAY"),
            DEFAULT_RETRY_DELAY,
        )
    )
    user_agent = str(
        first_value(
            getattr(args, "user_agent", None),
            os.environ.get("IMAGE_GENERATION_USER_AGENT"),
            user_env.get("IMAGE_GENERATION_USER_AGENT"),
            os.environ.get("GPT_IMAGE_USER_AGENT"),
            user_env.get("GPT_IMAGE_USER_AGENT"),
            dotenv.get("IMAGE_GENERATION_USER_AGENT"),
            dotenv.get("GPT_IMAGE_USER_AGENT"),
            DEFAULT_USER_AGENT,
        )
    )
    explicit_provider_profile = getattr(args, "provider_profile", None)
    configured_provider_profile = first_value(
        os.environ.get("IMAGE_GENERATION_PROVIDER_PROFILE"),
        user_env.get("IMAGE_GENERATION_PROVIDER_PROFILE"),
        os.environ.get("GPT_IMAGE_PROVIDER_PROFILE"),
        user_env.get("GPT_IMAGE_PROVIDER_PROFILE"),
        dotenv.get("IMAGE_GENERATION_PROVIDER_PROFILE"),
        dotenv.get("GPT_IMAGE_PROVIDER_PROFILE"),
    )
    provider_profile = str(first_value(explicit_provider_profile, DEFAULT_PROVIDER_PROFILE)).lower()
    explicit_routing_mode = getattr(args, "mode", None)
    configured_routing_mode = first_value(
        os.environ.get("IMAGE_GENERATION_ROUTING_MODE"),
        user_env.get("IMAGE_GENERATION_ROUTING_MODE"),
        os.environ.get("GPT_IMAGE_ROUTING_MODE"),
        user_env.get("GPT_IMAGE_ROUTING_MODE"),
        dotenv.get("IMAGE_GENERATION_ROUTING_MODE"),
        dotenv.get("GPT_IMAGE_ROUTING_MODE"),
    )
    # Legacy global routing values cannot silently defeat the numbered/default
    # model contract. They remain visible for diagnostics, but only a command
    # scoped --mode is an active routing request.
    routing_mode = str(first_value(explicit_routing_mode, DEFAULT_ROUTING_MODE)).lower()
    tool_model_policy = str(
        first_value(
            getattr(args, "tool_model_policy", None),
            os.environ.get("IMAGE_GENERATION_TOOL_MODEL_POLICY"),
            user_env.get("IMAGE_GENERATION_TOOL_MODEL_POLICY"),
            os.environ.get("GPT_IMAGE_TOOL_MODEL_POLICY"),
            user_env.get("GPT_IMAGE_TOOL_MODEL_POLICY"),
            dotenv.get("IMAGE_GENERATION_TOOL_MODEL_POLICY"),
            dotenv.get("GPT_IMAGE_TOOL_MODEL_POLICY"),
            DEFAULT_TOOL_MODEL_POLICY,
        )
    ).lower()
    validate_user_agent(user_agent)
    validate_choice(provider_profile, PROVIDER_PROFILE_VALUES, "provider_profile")
    validate_choice(routing_mode, ROUTING_MODE_VALUES, "routing_mode")
    validate_choice(tool_model_policy, TOOL_MODEL_POLICY_VALUES, "tool_model_policy")

    cli_vendor = getattr(args, "vendor", None)
    configured_vendor = first_value(
        os.environ.get("IMAGE_GENERATION_VENDOR"),
        user_env.get("IMAGE_GENERATION_VENDOR"),
        os.environ.get("GPT_IMAGE_VENDOR"),
        user_env.get("GPT_IMAGE_VENDOR"),
        os.environ.get("OPENAI_IMAGE_VENDOR"),
        user_env.get("OPENAI_IMAGE_VENDOR"),
        dotenv.get("IMAGE_GENERATION_VENDOR"),
        dotenv.get("GPT_IMAGE_VENDOR"),
        dotenv.get("OPENAI_IMAGE_VENDOR"),
    )
    preferred_vendor = first_value(cli_vendor, configured_vendor)
    catalog_path = Path(
        str(
            first_value(
                getattr(args, "catalog", None),
                os.environ.get("IMAGE_GENERATION_MODEL_CATALOG"),
                user_env.get("IMAGE_GENERATION_MODEL_CATALOG"),
                dotenv.get("IMAGE_GENERATION_MODEL_CATALOG"),
                default_model_catalog_path(),
            )
        )
    ).expanduser().resolve()

    return {
        "api_key": str(api_key or ""),
        "base_url": base_url,
        "model": str(model),
        "model_requested": str(raw_model),
        "model_alias": model_alias,
        "model_source": model_source,
        "configured_model_preference": str(configured_model) if configured_model else None,
        "tool_model_requested": str(raw_tool_model),
        "tool_model_alias": tool_model_alias,
        "tool_model_source": tool_model_source,
        "configured_tool_model_preference": str(configured_tool_model) if configured_tool_model else None,
        # Only the current command's --vendor narrows selection. A global
        # vendor setting merely orders the choices so it cannot silently lock
        # a future request to one provider.
        "vendor_requested": str(cli_vendor).lower() if cli_vendor else None,
        "vendor_preference": str(preferred_vendor).lower() if preferred_vendor else None,
        "vendor_source": "cli" if cli_vendor else "configured" if configured_vendor else None,
        "responses_model": str(responses_model),
        "tool_model": str(tool_model),
        "timeout": timeout,
        "retries": retries,
        "retry_delay": retry_delay,
        "user_agent": user_agent,
        "provider_profile_requested": provider_profile,
        "configured_provider_profile": str(configured_provider_profile).lower() if configured_provider_profile else None,
        "provider_profile": provider_profile,
        "routing_mode": routing_mode,
        "configured_routing_mode": str(configured_routing_mode).lower() if configured_routing_mode else None,
        "routing_mode_source": "cli" if explicit_routing_mode else "default",
        "tool_model_policy": tool_model_policy,
        "catalog_path": str(catalog_path),
        "selection_mode": "catalog",
        "windows_user_environment_loaded": bool(user_env),
    }


def mask_key(api_key: str) -> str:
    if not api_key:
        return "missing"
    tail = api_key[-4:] if len(api_key) >= 4 else api_key
    return f"set tail={tail}"


def validate_base_url(base_url: str) -> None:
    if not base_url.startswith(("https://", "http://")):
        raise SystemExit("base_url must start with https:// or http://.")
    if not base_url.endswith("/v1"):
        raise SystemExit("base_url must include /v1, for example https://api.openai.com/v1.")


def is_gpt_image_2_model(model: str) -> bool:
    normalized = str(model).strip().lower()
    return normalized == "gpt-image-2" or normalized.startswith("gpt-image-2-")


def is_gpt_image_model(model: str) -> bool:
    return str(model).strip().lower().startswith("gpt-image-")


def is_grok_image_model(model: str) -> bool:
    return str(model).strip().lower().startswith("grok-imagine-image")


def is_enabled_image_model(model: str) -> bool:
    """Return whether this model belongs to the currently enabled adapters."""
    return is_gpt_image_model(model) or is_grok_image_model(model)


def enabled_vendor_for_model(model: str) -> str | None:
    if is_gpt_image_model(model):
        return "openai"
    if is_grok_image_model(model):
        return "xai"
    return None


def validate_enabled_image_model(model: str, command: str) -> None:
    if is_enabled_image_model(model) and is_image_model_id(model, {}):
        return
    raise SystemExit(
        f"Model '{model}' is discovery-only and is not enabled for {command}. "
        "The current skill executes only GPT Image (gpt-image-*) and "
        "Grok Image (grok-imagine-image*) models."
    )


def execution_inventory(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split discovered image candidates into executable and discovery-only records."""
    executable = [record for record in records if record.get("enabled_for_execution")]
    discovery_only = [record for record in records if not record.get("enabled_for_execution")]
    return executable, discovery_only


def no_enabled_models_message(records: list[dict[str, Any]]) -> str:
    discovered = ", ".join(str(record["id"]) for record in records[:8])
    suffix = f" Discovered: {discovered}." if discovered else ""
    return (
        "Image models were discovered, but none are enabled for execution. "
        "The current skill executes only GPT Image (gpt-image-*) and Grok Image "
        "(grok-imagine-image*) models; no image POST was sent."
        f"{suffix}"
    )


def is_official_openai_base_url(base_url: str) -> bool:
    return base_url.rstrip("/").lower() == "https://api.openai.com/v1"


def _searchable_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
        except (TypeError, ValueError):
            return str(value).lower()
    return str(value).lower()


def _normalized_metadata_key(value: Any) -> str:
    # Normalize both snake_case and gateway-style camelCase keys to the same
    # spelling (for example ``imageGeneration`` -> ``image_generation``).
    text = str(value).strip()
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _metadata_key_values(
    metadata: dict[str, Any] | None, keys: tuple[str, ...] | set[str]
) -> list[tuple[str, Any]]:
    """Collect normalized key/value pairs for ordered metadata decisions."""
    if not metadata:
        return []
    target_keys = {_normalized_metadata_key(key) for key in keys}
    pairs: list[tuple[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized_key = _normalized_metadata_key(key)
                if normalized_key in target_keys:
                    pairs.append((normalized_key, child))
                visit(child)
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                visit(child)

    visit(metadata)
    return pairs


def _metadata_scalar_values(value: Any) -> list[str]:
    """Flatten metadata values into strings for conservative vendor matching."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(_metadata_scalar_values(child))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for child in value:
            result.extend(_metadata_scalar_values(child))
        return result
    return [str(value)]


def _metadata_truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "supported",
            "enabled",
            "available",
        }
    return False


def _metadata_falsey(value: Any) -> bool:
    """Return whether metadata explicitly disables a capability."""
    if value is False:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in {
            "0",
            "false",
            "no",
            "off",
            "disabled",
            "unsupported",
            "unavailable",
            "none",
        }
    return False


def _metadata_nested_negative(value: Any) -> bool:
    """Recognize capability objects such as ``{"supported": false}``."""
    if _metadata_falsey(value):
        return True
    if isinstance(value, dict):
        indicator_keys = {
            "supported",
            "enabled",
            "available",
            "value",
            "generation",
            "generate",
            "editing",
            "edit",
        }
        for key, child in value.items():
            normalized_key = _normalized_metadata_key(key)
            if normalized_key in indicator_keys and _metadata_falsey(child):
                return True
        return False
    return False


def _metadata_nested_positive(value: Any) -> bool:
    """Recognize capability objects such as ``{"supported": true}``."""
    if _metadata_truthy(value):
        return True
    if isinstance(value, dict):
        positive_keys = {
            "supported",
            "enabled",
            "available",
            "value",
            "generation",
            "generate",
            "editing",
            "edit",
        }
        for key, child in value.items():
            normalized_key = _normalized_metadata_key(key)
            if normalized_key in positive_keys and _metadata_truthy(child):
                return True
        return any(_metadata_nested_positive(child) for child in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_metadata_nested_positive(child) for child in value)
    return False


def _metadata_image_tokens(value: Any) -> bool:
    """Return whether a metadata value names an image-oriented capability."""
    text = _searchable_text(value)
    if not text:
        return False
    static_image = re.search(
        r"(?:image[_-]?generation|text[_-]?to[_-]?image|image[_-]?to[_-]?image|"
        r"img2img|txt2img|t2i|inpaint|outpaint|image[_-]?(?:edit|editing|upscal)|"
        r"图片|图像|生图|文生图)",
        text,
        re.IGNORECASE,
    )
    # A bare modality such as `image` is meaningful in a list/dict, while the
    # `image` part of `image-to-video` is only an input to a video operation.
    standalone_image = re.search(
        r"(?:^|[\s\[\]\{\},:;\"'])images?(?:$|[\s\[\]\{\},:;\"'])",
        text,
        re.IGNORECASE,
    )
    video_only = re.search(
        r"(?:text|image|img)[_-]?to[-_]?video|"
        r"(?:^|[\s\[\]\{\},:;\"'])video(?:[_-]?(?:generation|editing|synthesis))?(?:$|[\s\[\]\{\},:;\"'])|"
        r"(?:^|[-_])(?:t2v|i2v|v2v)(?:[-_]|$)",
        text,
        re.IGNORECASE,
    )
    if video_only and not static_image and not standalone_image:
        return False
    return bool(static_image or standalone_image)


def _metadata_capability_flags(metadata: dict[str, Any] | None) -> dict[str, bool]:
    """Extract conservative operation flags from nested gateway metadata."""
    flags = {
        "generation": False,
        "editing": False,
        "streaming": False,
        "transparent_background": False,
    }
    generation_keys = {
        "image",
        "images",
        "image_generation",
        "image_generation_capability",
        "text_to_image",
        "text2image",
        "txt2img",
        "t2i",
        "generation",
        "generate",
        "generations",
        "supported_tasks",
        "tasks",
        "output_modalities",
        "modalities",
        "type",
        "modality",
        "supported_endpoints",
        "endpoints",
        "endpoint",
    }
    editing_keys = {
        "edit",
        "editing",
        "image_editing",
        "image_edit",
        "img2img",
        "inpaint",
        "outpaint",
        "mask",
        "masks",
    }
    streaming_keys = {"stream", "streaming", "sse", "partial_images", "partial_image"}
    transparent_keys = {
        "transparent",
        "transparency",
        "transparent_background",
        "alpha",
        "alpha_channel",
    }

    def visit(value: Any, key: str = "") -> None:
        normalized_key = _normalized_metadata_key(key)
        if isinstance(value, dict):
            if normalized_key in editing_keys and _metadata_nested_positive(value):
                flags["editing"] = True
            if normalized_key in generation_keys and _metadata_nested_positive(value):
                flags["generation"] = True
            for child_key, child_value in value.items():
                visit(child_value, str(child_key))
            return
        if isinstance(value, (list, tuple, set)):
            for child in value:
                visit(child, normalized_key)
            return

        if normalized_key in editing_keys:
            if _metadata_truthy(value) or _metadata_image_tokens(value):
                flags["editing"] = True
        if normalized_key in generation_keys:
            if _metadata_truthy(value) or _metadata_image_tokens(value):
                flags["generation"] = True
        if normalized_key in streaming_keys and _metadata_truthy(value):
            flags["streaming"] = True
        if normalized_key in transparent_keys and _metadata_truthy(value):
            flags["transparent_background"] = True

        # A task/modality/endpoint string is meaningful even when its key is
        # not one of the known spellings above.
        if normalized_key in {"task", "tasks", "supported_tasks", "modality", "modalities", "output_modalities", "endpoint", "endpoints", "supported_endpoints"}:
            text = _searchable_text(value)
            if _metadata_image_tokens(text):
                flags["generation"] = True
            if re.search(r"edit|inpaint|outpaint|img2img|mask", text, re.IGNORECASE):
                flags["editing"] = True

    if metadata:
        visit(metadata)
    return flags


def _metadata_explicitly_disables_image(metadata: dict[str, Any] | None) -> bool:
    """Detect an explicit negative image capability in nested metadata."""
    if not metadata:
        return False
    image_keys = {
        "image",
        "images",
        "image_generation",
        "image_generation_capability",
        "text_to_image",
        "text2image",
        "txt2img",
        "t2i",
        "image_editing",
        "image_edit",
        "img2img",
        "inpaint",
        "outpaint",
        "output_modalities",
        "modalities",
    }
    disabled = False

    def visit(value: Any, key: str = "") -> None:
        nonlocal disabled
        if disabled:
            return
        normalized_key = _normalized_metadata_key(key)
        if normalized_key in image_keys and (
            _metadata_falsey(value) or _metadata_nested_negative(value)
        ):
            disabled = True
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key))
        elif isinstance(value, (list, tuple, set)):
            for child in value:
                visit(child, normalized_key)

    visit(metadata)
    return disabled


def normalize_vendor_id(value: str) -> str:
    raw = str(value or "").strip().lower()
    # Keep Unicode letters so gateways can expose vendor names in Chinese or
    # another local language without collapsing every unknown vendor to one
    # generic bucket.
    normalized = "".join(char if (char.isalnum() or char in {"-", "_"}) else "-" for char in raw)
    normalized = re.sub(r"[-_]+", "-", normalized).strip("-")
    aliases = {
        "x-ai": "xai",
        "xai": "xai",
        "open-ai": "openai",
        "stability-ai": "stability",
        "black-forest-labs": "black-forest-labs",
        "amazon-web-services": "amazon",
        "aws": "amazon",
        "google-deepmind": "google",
        "byte-dance": "bytedance",
        "ali-baba": "alibaba",
    }
    return aliases.get(normalized, normalized or "generic")


def infer_model_vendor(model: str, metadata: dict[str, Any] | None = None) -> str:
    model_text = model.lower()
    # A gateway's explicit owner/provider metadata describes the actual route
    # more reliably than a model-id substring (for example a proxy may expose
    # ``gpt-image-2`` under its own vendor). Check it before inferring from the
    # model name, and support nested/camelCase metadata.
    metadata_pairs = _metadata_key_values(metadata, MODEL_METADATA_VENDOR_KEYS)
    key_priority = {
        "vendor": 0,
        "vendor_id": 0,
        "vendor_name": 0,
        "owned_by": 1,
        "organization": 2,
        "publisher": 2,
        "creator": 2,
        "company": 2,
        "provider": 3,
        "provider_id": 3,
        "provider_name": 3,
        "provider_profile": 4,
    }
    ordered_pairs = sorted(
        enumerate(metadata_pairs),
        key=lambda item: (key_priority.get(item[1][0], 5), item[0]),
    )

    def marker_matches(vendor: str, marker: str, text: str) -> bool:
        # `openai-compatible` describes a protocol, not necessarily the
        # vendor. Avoid collapsing a gateway-owned model into OpenAI.
        if vendor == "openai" and "compatible" in text and marker == "openai":
            return False
        return marker in text

    invalid_vendor_ids = {
        "unknown",
        "none",
        "null",
        "model",
        "models",
        "system",
        "generic",
        "provider",
        "gateway",
        "compatible",
        "openai-compatible",
    }
    # Resolve each metadata priority tier completely before considering a
    # lower tier, so an explicit `vendor=Acme` cannot be overridden by a
    # protocol hint such as `provider=OpenAI-compatible`.
    for priority in sorted({key_priority.get(key, 5) for _, (key, _) in ordered_pairs}):
        tier = [(key, value) for _, (key, value) in ordered_pairs if key_priority.get(key, 5) == priority]
        for _, value in tier:
            metadata_text = " ".join(
                _searchable_text(candidate) for candidate in _metadata_scalar_values(value)
            )
            for vendor, markers in VENDOR_RULES:
                if any(marker_matches(vendor, marker, metadata_text) for marker in markers):
                    return vendor
        for _, value in tier:
            for candidate in _metadata_scalar_values(value):
                normalized = normalize_vendor_id(candidate)
                if normalized not in invalid_vendor_ids and "compatible" not in normalized:
                    return normalized
    for vendor, markers in VENDOR_RULES:
        if any(marker in model_text for marker in markers):
            return vendor
    return "generic"


def vendor_label(vendor: str) -> str:
    return VENDOR_LABELS.get(vendor, vendor.replace("-", " ").title())


def metadata_indicates_image_model(metadata: dict[str, Any] | None) -> bool:
    if not metadata:
        return False
    flags = _metadata_capability_flags(metadata)
    return flags["generation"] or flags["editing"]


def infer_provider_profile(model: str) -> str:
    if is_gpt_image_model(model):
        return "openai"
    if is_grok_image_model(model):
        return "grok"
    capability = MODEL_CAPABILITIES.get(model)
    if capability:
        return str(capability["provider_profile"])
    return "generic"


def resolve_provider_profile(model: str, requested: str) -> str:
    inferred = infer_provider_profile(model)
    if requested == "auto":
        return inferred
    if is_gpt_image_model(model) and requested != "openai":
        raise SystemExit("GPT Image models must use --provider-profile openai or auto.")
    if is_grok_image_model(model) and requested != "grok":
        raise SystemExit("Grok image models must use --provider-profile grok or auto.")
    return requested


def capability_for_model(model: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    capability = MODEL_CAPABILITIES.get(model)
    if capability:
        record = {"id": model, **capability}
    else:
        profile = infer_provider_profile(model)
        record = {
            "id": model,
            "provider_profile": profile,
            "status": "listed_unknown" if profile == "generic" else "unverified_alias",
            "generation": None,
            "editing": None,
            "transparent_background": None,
            "streaming": None,
            "max_resolution": "unknown",
            "recommended_for": "manual verification required",
        }
    metadata_flags = _metadata_capability_flags(metadata)
    # Live metadata is allowed to fill in unknown capabilities, but it never
    # weakens a stronger built-in guarantee (for example gpt-image-2 editing).
    for key in ("generation", "editing", "streaming", "transparent_background"):
        if metadata_flags[key] and record.get(key) is not True:
            record[key] = True
    reported_vendor = infer_model_vendor(model, metadata)
    enabled_vendor = enabled_vendor_for_model(model)
    vendor = enabled_vendor or reported_vendor
    record["vendor"] = vendor
    record["vendor_name"] = vendor_label(vendor)
    record["reported_vendor"] = reported_vendor
    record["enabled_for_execution"] = enabled_vendor is not None
    record["execution_status"] = "enabled" if enabled_vendor else "discovered_not_enabled"
    if enabled_vendor is None:
        record["recommended_for"] = "discovery only; adapter not enabled in the current GPT/Grok scope"
    record["image_model_candidate"] = True
    return record


def is_image_model_id(model: str, metadata: dict[str, Any] | None = None) -> bool:
    if model in MODEL_CAPABILITIES or metadata_indicates_image_model(metadata):
        return True
    if _metadata_explicitly_disables_image(metadata):
        return False
    # Broad image-family markers such as ``grok-imagine`` also occur in video
    # model ids. Do not expose an obviously video-only model as a still-image
    # candidate unless its live metadata explicitly advertises image support.
    if any(pattern.search(model) for pattern in VIDEO_ID_PATTERNS):
        return False
    return any(pattern.search(model) for pattern in IMAGE_ID_PATTERNS)


def supports_selection_operation(record: dict[str, Any], operation: str) -> bool:
    if not record.get("enabled_for_execution", is_enabled_image_model(str(record.get("id", "")))):
        return False
    if operation == "any":
        return True
    capability_key = "generation" if operation == "generate" else "editing"
    capability = record.get(capability_key)
    if capability is False:
        return False
    model = str(record["id"]).lower()
    if operation == "generate" and capability is not True:
        edit_only_markers = ("-edit", "_edit", "inpaint", "upscale", "controlnet")
        if any(marker in model for marker in edit_only_markers):
            return False
    return True


def model_preference_key(record: dict[str, Any], configured_model: str) -> tuple[int, int, str]:
    model = str(record["id"])
    lower = model.lower()
    if lower == str(configured_model).lower():
        configured_rank = 0
    elif lower == DEFAULT_MODEL.lower():
        configured_rank = 1
    else:
        configured_rank = 2
    penalty = 0
    if record.get("status") in {"retired_alias", "gateway_alias_unverified"}:
        penalty += 20
    if any(marker in lower for marker in ("-edit", "_edit", "inpaint", "upscale")):
        penalty += 15
    if any(marker in lower for marker in ("lite", "preview", "experimental", "deprecated", "legacy")):
        penalty += 10
    if any(marker in lower for marker in ("quality", "pro", "ultra", "4k")):
        penalty += 3
    return configured_rank, penalty, lower


def group_image_model_records(
    records: list[dict[str, Any]],
    configured_model: str,
    operation: str = "generate",
    preferred_vendor: str | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if not record.get("enabled_for_execution", is_enabled_image_model(str(record.get("id", "")))):
            continue
        if not supports_selection_operation(record, operation):
            continue
        grouped.setdefault(str(record["vendor"]), []).append(record)
    vendor_order = {vendor: index for index, vendor in enumerate(VENDOR_PRIORITY)}
    groups: list[dict[str, Any]] = []
    for vendor, vendor_records in grouped.items():
        ordered_models = sorted(vendor_records, key=lambda item: model_preference_key(item, configured_model))
        groups.append(
            {
                "id": vendor,
                "name": vendor_label(vendor),
                "default_model": ordered_models[0]["id"],
                "models": ordered_models,
            }
        )
    normalized_preference = normalize_vendor_id(preferred_vendor or "") if preferred_vendor else None
    return sorted(
        groups,
        key=lambda item: (
            0 if normalized_preference and normalize_vendor_id(str(item["id"])) == normalized_preference else 1,
            vendor_order.get(str(item["id"]), len(vendor_order)),
            str(item["name"]),
        ),
    )


def apply_routing_mode(args: argparse.Namespace, config: dict[str, Any]) -> None:
    mode = config["routing_mode"]
    if mode != "auto" and getattr(args, "preset", None):
        raise SystemExit("Use either --mode or --preset, not both; direct flags may override either one.")
    settings = ROUTING_MODES[mode]
    image_model_arg = "tool_model" if args.command == "responses" else "model"
    image_model_key = "tool_model" if args.command == "responses" else "model"
    explicit_image_model = getattr(args, image_model_arg, None)
    source_key = "tool_model_source" if image_model_key == "tool_model" else "model_source"
    if settings.get("model") and not explicit_image_model:
        config[image_model_key] = str(settings["model"])
        config[source_key] = "routing_mode"
        config["selection_reason"] = "routing_mode"
        config["selection_mode"] = "routing"
        config["selected_vendor"] = enabled_vendor_for_model(config[image_model_key])
    for field, value in settings.items():
        if field == "model" or not hasattr(args, field):
            continue
        if getattr(args, field) is None:
            setattr(args, field, value)
    image_model = str(config[image_model_key])
    config["provider_profile"] = resolve_provider_profile(image_model, config["provider_profile_requested"])


def validate_gpt_image_2_size(size: str) -> None:
    if size == "auto":
        return
    width, height = parse_requested_dimensions(size) or (0, 0)
    if width % 16 or height % 16:
        raise SystemExit("gpt-image-2 width and height must both be multiples of 16.")
    if max(width, height) > GPT_IMAGE_2_MAX_EDGE:
        raise SystemExit(f"gpt-image-2 longest edge must not exceed {GPT_IMAGE_2_MAX_EDGE}px.")
    if max(width, height) / min(width, height) > 3:
        raise SystemExit("gpt-image-2 long edge to short edge ratio must not exceed 3:1.")
    pixels = width * height
    if not GPT_IMAGE_2_MIN_PIXELS <= pixels <= GPT_IMAGE_2_MAX_PIXELS:
        raise SystemExit(
            f"gpt-image-2 total pixels must be between {GPT_IMAGE_2_MIN_PIXELS} and {GPT_IMAGE_2_MAX_PIXELS}."
        )


def grok_shape_from_args(args: argparse.Namespace) -> tuple[str, str]:
    aspect_ratio = args.aspect_ratio
    if not aspect_ratio:
        if args.size == "auto":
            aspect_ratio = "auto"
        else:
            width, height = parse_requested_dimensions(args.size) or (1, 1)
            divisor = gcd(width, height)
            aspect_ratio = f"{width // divisor}:{height // divisor}"
            if aspect_ratio not in GROK_ASPECT_RATIO_VALUES:
                raise SystemExit(
                    f"The requested size maps to unsupported Grok aspect ratio {aspect_ratio}; pass --aspect-ratio explicitly."
                )
    resolution = args.resolution
    if not resolution:
        dimensions = parse_requested_dimensions(args.size)
        resolution = "2k" if dimensions and max(dimensions) > 1024 else "1k"
    return aspect_ratio, resolution


def validate_generation_args(args: argparse.Namespace, config: dict[str, Any]) -> None:
    resolve_prompt(args)
    require_prompt(args)
    validate_image_options(args)
    validate_provider_image_options(args, config, "generate")


def resolve_prompt(args: argparse.Namespace) -> None:
    if getattr(args, "prompt_file", None):
        prompt_path = Path(args.prompt_file)
        if not prompt_path.exists():
            raise SystemExit(f"prompt file not found: {prompt_path}")
        args.prompt = prompt_path.read_text(encoding="utf-8").strip()


def require_prompt(args: argparse.Namespace) -> None:
    if not getattr(args, "prompt", None):
        raise SystemExit("--prompt or --prompt-file is required.")


def validate_image_options(args: argparse.Namespace) -> None:
    if args.size != "auto" and not SIZE_PATTERN.match(args.size):
        raise SystemExit("--size must be auto or WIDTHxHEIGHT.")
    if args.quality not in QUALITY_VALUES:
        raise SystemExit("--quality must be auto, low, medium, or high.")
    if args.output_format not in FORMAT_VALUES:
        raise SystemExit("--format must be png, jpeg, or webp.")
    if args.size_policy not in SIZE_POLICY_VALUES:
        raise SystemExit("--size-policy must be normalize, strict, or provider.")
    if getattr(args, "compression", None) is not None:
        if not 0 <= args.compression <= 100:
            raise SystemExit("--compression must be between 0 and 100.")
        if args.output_format not in {"jpeg", "webp"}:
            raise SystemExit("--compression is only valid with --format jpeg or webp.")
    if getattr(args, "n", 1) < 1 or getattr(args, "n", 1) > 10:
        raise SystemExit("--n must be between 1 and 10.")
    background = getattr(args, "background", None)
    if background is not None and background not in BACKGROUND_VALUES:
        raise SystemExit("--background must be auto, opaque, or transparent.")
    if background == "transparent" and args.output_format == "jpeg":
        raise SystemExit("Transparent backgrounds require --format png or webp; jpeg is not supported.")
    moderation = getattr(args, "moderation", None)
    if moderation is not None and moderation not in MODERATION_VALUES:
        raise SystemExit("--moderation must be auto or low.")
    response_format = getattr(args, "response_format", None)
    if response_format is not None and response_format not in RESPONSE_FORMAT_VALUES:
        raise SystemExit("--response-format must be url or b64_json.")
    aspect_ratio = getattr(args, "aspect_ratio", None)
    if aspect_ratio is not None and aspect_ratio not in GROK_ASPECT_RATIO_VALUES:
        raise SystemExit(f"--aspect-ratio must be one of: {', '.join(GROK_ASPECT_RATIO_VALUES)}.")
    resolution = getattr(args, "resolution", None)
    if resolution is not None and resolution not in GROK_RESOLUTION_VALUES:
        raise SystemExit("--resolution must be 1k or 2k.")
    partial_images = getattr(args, "partial_images", None)
    if partial_images is not None and not 0 <= partial_images <= 3:
        raise SystemExit("--partial-images must be between 0 and 3.")
    if partial_images is not None and not getattr(args, "stream", False):
        raise SystemExit("--partial-images requires --stream.")
    if getattr(args, "stream", False) and getattr(args, "n", 1) > 1:
        raise SystemExit("--stream cannot be combined with --n greater than 1.")


def validate_provider_image_options(args: argparse.Namespace, config: dict[str, Any], command: str) -> None:
    image_model = config["tool_model"] if command == "responses" else config["model"]
    profile = config["provider_profile"]
    validate_enabled_image_model(image_model, command)
    if profile not in EXECUTABLE_PROVIDER_PROFILES:
        raise SystemExit(
            f"Provider profile '{profile}' is compatibility-only. "
            "Use auto/openai for GPT Image or auto/grok for Grok Image."
        )
    if profile == "openai":
        if is_gpt_image_2_model(image_model):
            validate_gpt_image_2_size(args.size)
            if getattr(args, "response_format", None):
                raise SystemExit("Do not send --response-format for gpt-image-2; image data is returned as base64.")
            if getattr(args, "background", None) == "transparent" and image_model != "gpt-image-2":
                raise SystemExit("Transparent background is confirmed only for gpt-image-2, not gateway aliases.")
        return
    if profile == "grok":
        if command == "responses":
            raise SystemExit("Grok image models use generate or edit; the Responses image_generation route is OpenAI-style.")
        if getattr(args, "stream", False) or getattr(args, "partial_images", None) is not None:
            raise SystemExit("Streaming partial images are not enabled for the Grok provider profile.")
        if getattr(args, "n", 1) != 1:
            raise SystemExit("The Grok provider profile sends one image per request; use the provider Batch API separately.")
        if getattr(args, "background", None) not in (None, "auto"):
            raise SystemExit("Transparent or forced opaque backgrounds are not supported by the Grok provider profile.")
        if getattr(args, "moderation", None) is not None:
            raise SystemExit("--moderation is not a Grok request parameter.")
        if getattr(args, "compression", None) is not None:
            raise SystemExit("--compression is not a Grok request parameter.")
        if image_model == "grok-imagine-image-2.0" and args.quality not in {"low", "medium"}:
            raise SystemExit("grok-imagine-image-2.0 supports --quality low or medium.")
        grok_shape_from_args(args)


def parse_json_object(text: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} must be a JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object.")
    return value


def read_json_object_file(path_text: str, label: str) -> dict[str, Any]:
    path = Path(path_text).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"{label} file not found: {path}")
    return parse_json_object(path.read_text(encoding="utf-8"), label)


def collect_extra_json(args: argparse.Namespace, json_attr: str = "extra_json", file_attr: str = "extra_json_file") -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path_text in getattr(args, file_attr, None) or []:
        merged.update(read_json_object_file(path_text, f"--{file_attr.replace('_', '-')}"))
    for text in getattr(args, json_attr, None) or []:
        merged.update(parse_json_object(text, f"--{json_attr.replace('_', '-')}"))
    return merged


def merge_extra_json(target: dict[str, Any], args: argparse.Namespace, json_attr: str = "extra_json", file_attr: str = "extra_json_file") -> None:
    target.update(collect_extra_json(args, json_attr, file_attr))


def apply_preset(args: argparse.Namespace) -> None:
    if not getattr(args, "preset", None):
        return
    preset = PRESETS[args.preset]
    for field, value in preset.items():
        if getattr(args, field) is None:
            setattr(args, field, value)


def fill_generation_defaults(args: argparse.Namespace) -> None:
    if args.size is None:
        args.size = DEFAULT_SIZE
    if args.quality is None:
        args.quality = DEFAULT_QUALITY
    if args.output_format is None:
        args.output_format = DEFAULT_FORMAT
    if args.size_policy is None:
        args.size_policy = DEFAULT_SIZE_POLICY
    if hasattr(args, "output") and args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.output = f"output/imagegen/{timestamp}.{args.output_format}"


def prepare_common_image_args(args: argparse.Namespace, config: dict[str, Any]) -> None:
    apply_preset(args)
    if config["provider_profile"] == "grok" and args.size is None and (args.aspect_ratio or args.resolution):
        args.size = "auto"
    fill_generation_defaults(args)
    validate_image_options(args)


def prompt_choice(name: str, current: str, allowed: list[str]) -> str:
    allowed_text = "/".join(allowed)
    print(f"{name} [{current}] ({allowed_text}): ", end="", file=sys.stderr, flush=True)
    value = clean_interactive_input(sys.stdin.readline())
    if not value:
        return current
    if value not in allowed:
        raise SystemExit(f"Invalid {name}: {value}. Choose one of: {', '.join(allowed)}")
    return value


def clean_interactive_input(value: str) -> str:
    cleaned = value.strip().lstrip("\ufeff")
    if cleaned.startswith("\u9518\udcbf") or cleaned.startswith("\u9518?"):
        cleaned = cleaned[2:]
    for prefix in ("ďťż", "ďť?", "ďť", "п»ї", "п»?", "п»"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    if cleaned and all(ord(char) >= 0xDC80 and ord(char) <= 0xDCFF for char in cleaned):
        return ""
    return cleaned.strip()


def run_interactive_confirmation(args: argparse.Namespace, config: dict[str, Any], url: str, payload: dict[str, Any], mode: str) -> None:
    if not getattr(args, "interactive", False):
        return
    print("Interactive image parameter confirmation", file=sys.stderr)
    args.size = prompt_choice("size", args.size, SIZE_VALUES)
    args.quality = prompt_choice("quality", args.quality, QUALITY_VALUES)
    args.output_format = prompt_choice("format", args.output_format, FORMAT_VALUES)
    validate_image_options(args)
    validate_provider_image_options(args, config, mode)
    apply_interactive_payload_values(payload, args, mode, config)
    summary = {
        "url": url,
        "auth": mask_key(config["api_key"]),
        "mode": mode,
        "model": payload.get("model"),
        "size": args.size,
        "quality": args.quality,
        "format": args.output_format,
        "provider_profile": config["provider_profile"],
        "routing_mode": config["routing_mode"],
        "size_policy": args.size_policy,
        "output": getattr(args, "output", None),
        "dry_run": bool(getattr(args, "dry_run", False)),
    }
    if mode == "responses":
        summary["model"] = payload.get("model")
        summary["tool_model"] = payload.get("tools", [{}])[0].get("model", config["tool_model"])
    print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
    print("Proceed? [y/N]: ", end="", file=sys.stderr, flush=True)
    confirmed = clean_interactive_input(sys.stdin.readline()).lower()
    if confirmed not in {"y", "yes"}:
        raise SystemExit("Cancelled by user.")


def apply_interactive_payload_values(
    payload: dict[str, Any],
    args: argparse.Namespace,
    mode: str,
    config: dict[str, Any],
) -> None:
    if mode == "responses":
        tool = payload["tools"][0]
        tool["size"] = args.size
        tool["quality"] = args.quality
        tool["output_format"] = args.output_format
        return
    if config["provider_profile"] == "grok":
        aspect_ratio, resolution = grok_shape_from_args(args)
        payload["aspect_ratio"] = aspect_ratio
        payload["resolution"] = resolution
        if config["model"] == "grok-imagine-image-2.0":
            payload["quality"] = args.quality
        else:
            payload.pop("quality", None)
        return
    payload["size"] = args.size
    payload["quality"] = args.quality
    if mode == "edit":
        payload["output_format"] = args.output_format
    elif args.output_format:
        payload["output_format"] = args.output_format


def build_openai_generation_request(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config["model"],
        "prompt": args.prompt,
        "size": args.size,
        "quality": args.quality,
        "n": args.n,
    }
    if args.output_format:
        payload["output_format"] = args.output_format
    if args.compression is not None:
        payload["output_compression"] = args.compression
    if args.user:
        payload["user"] = args.user
    if args.background:
        payload["background"] = args.background
    if args.moderation:
        payload["moderation"] = args.moderation
    if args.stream:
        payload["stream"] = True
    if args.partial_images is not None:
        payload["partial_images"] = args.partial_images
    merge_extra_json(payload, args)
    return payload


def build_grok_generation_request(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    aspect_ratio, resolution = grok_shape_from_args(args)
    payload: dict[str, Any] = {
        "model": config["model"],
        "prompt": args.prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "response_format": args.response_format or "b64_json",
    }
    if config["model"] == "grok-imagine-image-2.0":
        payload["quality"] = args.quality
    merge_extra_json(payload, args)
    return payload


def build_generic_generation_request(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    payload = build_openai_generation_request(args, config)
    if args.response_format:
        payload["response_format"] = args.response_format
    return payload


def build_generation_request(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if config["provider_profile"] == "grok":
        return build_grok_generation_request(args, config)
    if config["provider_profile"] == "openai":
        return build_openai_generation_request(args, config)
    raise SystemExit("Only OpenAI GPT Image and xAI/Grok image request adapters are enabled.")


def classify_error(status: int | None, body: str, reason: str = "") -> dict[str, Any]:
    text = body.strip()
    lower = text.lower()
    if status in {401} or "invalid_api_key" in lower:
        return {
            "status": status,
            "category": "authentication_failed",
            "retryable": False,
            "summary": "The API key was rejected. The current process may still be using an old or invalid key.",
            "next_steps": [
                "Re-read the key tail from the current process and confirm it changed.",
                "Check whether the provider expects IMAGE_GENERATION_API_KEY, GPT_IMAGE_API_KEY, OPENAI_API_KEY, or a project-scoped key.",
                "Do not retry the same request until the key or endpoint changes.",
            ],
            "body_excerpt": text[:1000],
        }
    if status == 403:
        return {
            "status": status,
            "category": "permission_or_gateway_forbidden",
            "retryable": False,
            "summary": "The provider returned 403 Forbidden. This is usually key permission, model access, base_url routing, WAF, IP, or region policy.",
            "next_steps": [
                "Check base_url includes the provider's exact /v1 path and is not the web dashboard URL.",
                "Verify the key has image-generation permission and access to the configured model.",
                "If using a proxy or third-party gateway, check IP allowlists, Cloudflare/WAF rules, and account balance.",
                "Retry only after changing key, base_url, model, or provider-side permissions.",
            ],
            "body_excerpt": text[:1000],
        }
    if status in {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 523, 524}:
        category = "gateway_timeout" if status in {504, 522, 524} else "transient_provider_error"
        summary = (
            "Cloudflare or an upstream gateway timed out before the image request completed."
            if status in {522, 524}
            else "The provider returned a transient error that may succeed on retry."
        )
        return {
            "status": status,
            "category": category,
            "retryable": True,
            "summary": summary,
            "next_steps": [
                "Retry with limited exponential backoff.",
                "Lower size or quality for third-party gateways with short timeouts.",
                "Switch to the official OpenAI endpoint or a provider route without Cloudflare if 524 repeats.",
                "Save the raw error body and provider request id when available.",
            ],
            "body_excerpt": text[:1000],
        }
    if status is None:
        return {
            "status": None,
            "category": "network_error",
            "retryable": True,
            "summary": f"Network error before an HTTP response was received: {reason}",
            "next_steps": [
                "Check DNS, proxy, VPN, and whether the base_url is reachable from this machine.",
                "Retry once after confirming network connectivity.",
            ],
            "body_excerpt": text[:1000],
        }
    return {
        "status": status,
        "category": "api_error",
        "retryable": False,
        "summary": f"The provider returned HTTP {status}.",
        "next_steps": [
            "Read the saved error body for provider-specific details.",
            "Check base_url, model name, request parameters, and provider documentation.",
        ],
        "body_excerpt": text[:1000],
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_model_catalog(config: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(config["catalog_path"])
    if not path.exists():
        return None
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Model catalog is unreadable: {path} ({exc}). Run configure again.") from exc
    if not isinstance(catalog, dict) or catalog.get("schema_version") != MODEL_CATALOG_SCHEMA_VERSION:
        raise SystemExit(f"Model catalog schema is unsupported: {path}. Run configure again.")
    if str(catalog.get("base_url", "")).rstrip("/") != config["base_url"].rstrip("/"):
        raise SystemExit("Model catalog belongs to a different Base URL. Run configure to refresh it.")
    choices = catalog.get("choices")
    if not isinstance(choices, list):
        raise SystemExit(f"Model catalog choices are invalid: {path}. Run configure again.")
    seen_numbers: set[int] = set()
    for item in choices:
        if not isinstance(item, dict):
            raise SystemExit(f"Model catalog contains an invalid choice: {path}. Run configure again.")
        model = str(item.get("model") or item.get("id") or "").strip()
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"Model catalog contains an invalid choice number: {path}. Run configure again.") from exc
        if number < 1 or number in seen_numbers:
            raise SystemExit(f"Model catalog contains duplicate/invalid choice numbers: {path}. Run configure again.")
        seen_numbers.add(number)
        if not model or not is_enabled_image_model(model) or not is_image_model_id(model, {}):
            raise SystemExit(
                f"Model catalog contains a non-executable model '{model or '<missing>'}': {path}. Run configure again."
            )
    if seen_numbers and sorted(seen_numbers) != list(range(1, len(seen_numbers) + 1)):
        raise SystemExit(f"Model catalog choice numbers are not contiguous: {path}. Run configure again.")
    return catalog


def resolve_catalog_choice(config: dict[str, Any], choice: str, operation: str) -> dict[str, Any]:
    catalog = load_model_catalog(config)
    if catalog is None:
        raise SystemExit(
            "No global model catalog found. Run `image_generation_api.py configure` once, "
            "then use --choice N."
        )
    choices = catalog.get("choices", [])
    selected = resolve_model_choice(choice, choices)
    if selected is None:
        available = ", ".join(f"{item.get('number')}={item.get('model')}" for item in choices)
        raise SystemExit(f"Invalid catalog model choice '{choice}'. Available: {available or 'none'}")
    model = str(selected.get("model") or selected.get("id") or "").strip()
    if not model:
        raise SystemExit(f"Catalog choice '{choice}' has no model id. Run configure again.")
    validate_enabled_image_model(model, operation)
    if operation == "edit" and selected.get("editing") is False:
        raise SystemExit(f"Catalog model {selected['model']} does not support editing.")
    if operation == "generate" and selected.get("generation") is False:
        raise SystemExit(f"Catalog model {selected['model']} does not support generation.")
    return selected


def redact_for_output(value: Any, key: str | None = None) -> Any:
    if key and SENSITIVE_KEY_PATTERN.search(key):
        return mask_key(str(value)) if isinstance(value, str) else "<redacted>"
    if isinstance(value, dict):
        return {str(child_key): redact_for_output(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [redact_for_output(item) for item in value]
    if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
        prefix = value.split(",", 1)[0]
        return f"{prefix},<base64 omitted>"
    return value


def save_request_record(args: argparse.Namespace, record: dict[str, Any]) -> str | None:
    if not getattr(args, "save_request", None):
        return None
    path = Path(args.save_request).expanduser().resolve()
    write_json(path, redact_for_output(record))
    return str(path)


def build_request_record(url: str, config: dict[str, Any], request_payload: Any, args: argparse.Namespace, request_key: str = "request") -> dict[str, Any]:
    return {
        "url": url,
        "auth": mask_key(config["api_key"]),
        request_key: request_payload,
        "model": config.get("tool_model") if args.command == "responses" else config.get("model"),
        "model_requested": config.get("tool_model_requested") if args.command == "responses" else config.get("model_requested"),
        "model_alias": config.get("tool_model_alias") if args.command == "responses" else config.get("model_alias"),
        "model_source": config.get("tool_model_source") if args.command == "responses" else config.get("model_source"),
        "selected_vendor": config.get("selected_vendor"),
        "selection_reason": config.get("selection_reason"),
        "selection_mode": config.get("selection_mode", "default"),
        "selection_choice": getattr(args, "choice", None),
        "selection_choice_alias": model_choice_alias(getattr(args, "choice", None)),
        "provider_profile": config.get("provider_profile"),
        "routing_mode": config.get("routing_mode"),
        "timeout": config["timeout"],
        "retries": config["retries"],
        "retry_delay": config["retry_delay"],
        "user_agent": config["user_agent"],
        "preset": getattr(args, "preset", None),
        "size_policy": getattr(args, "size_policy", None),
    }


def print_request_record(args: argparse.Namespace, record: dict[str, Any]) -> None:
    redacted = redact_for_output(record)
    request_saved = save_request_record(args, record)
    if request_saved:
        redacted["request_saved"] = request_saved
    print(json.dumps(redacted, ensure_ascii=False, indent=2))


def parse_requested_dimensions(size: str) -> tuple[int, int] | None:
    if size == "auto":
        return None
    width_text, height_text = size.lower().split("x", 1)
    return int(width_text), int(height_text)


def aspect_ratios_match(actual: tuple[int, int], expected: tuple[int, int], tolerance: float = 0.01) -> bool:
    actual_cross = actual[0] * expected[1]
    expected_cross = expected[0] * actual[1]
    return abs(actual_cross - expected_cross) / max(actual_cross, expected_cross) <= tolerance


def encode_resized_image(image: Any, target: Path, provider_format: str) -> tuple[bytes, str]:
    from PIL import Image

    target_format = {
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".webp": "WEBP",
    }.get(target.suffix.lower(), provider_format or "PNG")
    image_to_save = image
    if target_format == "JPEG" and image.mode not in {"RGB", "L"}:
        if "A" in image.getbands():
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            image_to_save = background
        else:
            image_to_save = image.convert("RGB")
    elif target_format == "WEBP" and image.mode not in {"RGB", "RGBA"}:
        image_to_save = image.convert("RGBA" if "transparency" in image.info else "RGB")

    save_options: dict[str, Any] = {}
    if target_format == "PNG":
        save_options["optimize"] = True
    elif target_format == "JPEG":
        save_options.update({"quality": 95, "subsampling": 0})
    elif target_format == "WEBP":
        save_options.update({"quality": 95, "method": 6})

    buffer = BytesIO()
    image_to_save.save(buffer, format=target_format, **save_options)
    return buffer.getvalue(), target_format.lower()


def target_image_format(target: Path) -> str | None:
    return {
        ".jpg": "jpeg",
        ".jpeg": "jpeg",
        ".png": "png",
        ".webp": "webp",
    }.get(target.suffix.lower())


def normalize_image_bytes(
    image_bytes: bytes,
    target: Path,
    requested_size: str,
    size_policy: str,
) -> tuple[bytes, dict[str, Any]]:
    expected = parse_requested_dimensions(requested_size)
    try:
        from PIL import Image
    except ImportError as exc:
        if expected is None or size_policy == "provider":
            return image_bytes, {
                "requested_size": list(expected) if expected else None,
                "provider_size": None,
                "final_size": None,
                "normalized": False,
                "size_policy": size_policy,
                "warning": "Pillow is unavailable, so output dimensions were not inspected.",
            }
        raise OutputProcessingError(
            "image_processing_unavailable",
            "Pillow is required to enforce exact output dimensions. Install Pillow or use --size-policy provider.",
        ) from exc

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            source.load()
            provider_size = (source.width, source.height)
            provider_format = (source.format or "unknown").lower()
            final_size = provider_size
            final_format = provider_format
            normalized = False
            format_normalized = False
            processed_bytes = image_bytes
            image_for_output = source

            if expected and provider_size != expected:
                details = {
                    "requested_size": list(expected),
                    "provider_size": list(provider_size),
                    "size_policy": size_policy,
                }
                if size_policy == "strict":
                    raise OutputProcessingError(
                        "output_dimension_mismatch",
                        "The provider returned dimensions that do not match the requested size.",
                        details,
                    )
                if size_policy == "normalize":
                    if not aspect_ratios_match(provider_size, expected):
                        raise OutputProcessingError(
                            "output_aspect_ratio_mismatch",
                            "The provider returned a different aspect ratio; automatic stretching or cropping was refused.",
                            details,
                        )
                    resampling = getattr(Image, "Resampling", Image).LANCZOS
                    resized = source.resize(expected, resampling)
                    processed_bytes, final_format = encode_resized_image(resized, target, source.format or "PNG")
                    final_size = expected
                    normalized = True
                    image_for_output = resized

            requested_format = target_image_format(target)
            if requested_format and final_format != requested_format:
                processed_bytes, final_format = encode_resized_image(image_for_output, target, source.format or "PNG")
                format_normalized = True

            return processed_bytes, {
                "requested_size": list(expected) if expected else None,
                "provider_size": list(provider_size),
                "final_size": list(final_size),
                "provider_format": provider_format,
                "final_format": final_format,
                "normalized": normalized,
                "format_normalized": format_normalized,
                "size_policy": size_policy,
            }
    except OutputProcessingError:
        raise
    except Exception as exc:
        raise OutputProcessingError(
            "invalid_image_output",
            "The provider returned data that could not be decoded as an image.",
            {"path": str(target)},
        ) from exc


def save_image_byte_records(
    records: list[bytes],
    output: Path,
    requested_size: str,
    size_policy: str,
) -> dict[str, Any]:
    saved: list[str] = []
    artifacts: list[dict[str, Any]] = []
    for index, image_bytes in enumerate(records):
        target = output if len(records) == 1 else output.with_name(f"{output.stem}-{index + 1}{output.suffix or '.png'}")
        processed_bytes, artifact = normalize_image_bytes(image_bytes, target, requested_size, size_policy)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(processed_bytes)
        resolved = str(target.resolve())
        saved.append(resolved)
        artifact["path"] = resolved
        artifacts.append(artifact)
    return {"saved": saved, "artifacts": artifacts}


def decode_and_save_images(
    response_payload: dict[str, Any],
    output: Path,
    requested_size: str,
    size_policy: str,
) -> dict[str, Any]:
    data = response_payload.get("data")
    if not isinstance(data, list) or not data:
        raise SystemExit("Response did not contain data[].")

    saved: list[str] = []
    artifacts: list[dict[str, Any]] = []
    multiple = len(data) > 1
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        b64_value = item.get("b64_json") or item.get("base64") or item.get("image_base64")
        if not b64_value and item.get("url"):
            expected = parse_requested_dimensions(requested_size)
            saved.append(str(item["url"]))
            artifacts.append(
                {
                    "url": str(item["url"]),
                    "requested_size": list(expected) if expected else None,
                    "provider_size": None,
                    "final_size": None,
                    "normalized": False,
                    "size_policy": size_policy,
                    "warning": "Remote URL output was not downloaded, so dimensions were not inspected.",
                }
            )
            continue
        if not b64_value:
            continue
        target = output
        if multiple:
            stem = output.stem
            suffix = output.suffix or ".png"
            target = output.with_name(f"{stem}-{index + 1}{suffix}")
        try:
            image_bytes = base64.b64decode(normalize_image_base64(str(b64_value)))
        except Exception as exc:
            raise OutputProcessingError(
                "invalid_image_output",
                "The provider returned image data that was not valid base64.",
            ) from exc
        processed_bytes, artifact = normalize_image_bytes(image_bytes, target, requested_size, size_policy)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(processed_bytes)
        resolved = str(target.resolve())
        saved.append(resolved)
        artifact["path"] = resolved
        artifacts.append(artifact)
    if not saved:
        raise SystemExit("Response did not contain base64 image data or image URLs.")
    return {"saved": saved, "artifacts": artifacts}


def collect_response_image_records(value: Any) -> list[str]:
    records: list[str] = []
    if isinstance(value, dict):
        for key in ("b64_json", "base64", "image_base64", "partial_image_b64", "result"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                records.append(candidate)
        for nested in value.values():
            records.extend(collect_response_image_records(nested))
    elif isinstance(value, list):
        for item in value:
            records.extend(collect_response_image_records(item))
    return records


def normalize_image_base64(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("data:") and ";base64," in candidate:
        candidate = candidate.split(",", 1)[1]
    return "".join(candidate.split())


def form_value_to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def decode_and_save_response_images(
    response_payload: dict[str, Any],
    output: Path,
    requested_size: str,
    size_policy: str,
) -> dict[str, Any]:
    b64_values = collect_response_image_records(response_payload)
    decoded: list[bytes] = []
    seen: set[str] = set()
    for b64_value in b64_values:
        normalized = normalize_image_base64(b64_value)
        if not normalized or normalized in seen:
            continue
        try:
            image_bytes = base64.b64decode(normalized, validate=True)
        except Exception:
            continue
        seen.add(normalized)
        decoded.append(image_bytes)
    if not decoded:
        raise SystemExit("Response did not contain base64 image data.")
    return save_image_byte_records(decoded, output, requested_size, size_policy)


def post_json(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: int,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        response_body = response.read().decode("utf-8")
        return json.loads(response_body)


def parse_sse_payload(raw_text: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush_event() -> None:
        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not data or data == "[DONE]":
            return
        try:
            decoded = json.loads(data)
        except json.JSONDecodeError:
            events.append({"type": "unparsed_sse_data", "data": data})
            return
        if isinstance(decoded, dict):
            events.append(decoded)
        else:
            events.append({"type": "sse_data", "data": decoded})

    for raw_line in raw_text.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            flush_event()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush_event()
    return {"events": events}


def post_json_stream(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: int,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": user_agent,
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        response_body = response.read().decode("utf-8", errors="replace")
        if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
            return json.loads(response_body)
        return parse_sse_payload(response_body)


def encode_multipart(fields: dict[str, Any], files: list[tuple[str, Path]]) -> tuple[str, bytes]:
    boundary = f"----image-generation-api-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        if value in (None, ""):
            continue
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(form_value_to_text(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for field_name, path in files:
        mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'.encode("utf-8")
        )
        chunks.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


def post_multipart(
    url: str,
    api_key: str,
    content_type: str,
    body: bytes,
    timeout: int,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        response_body = response.read().decode("utf-8")
        return json.loads(response_body)


def post_multipart_stream(
    url: str,
    api_key: str,
    content_type: str,
    body: bytes,
    timeout: int,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "Accept": "text/event-stream",
            "User-Agent": user_agent,
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        response_body = response.read().decode("utf-8", errors="replace")
        if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
            return json.loads(response_body)
        return parse_sse_payload(response_body)


def get_json(url: str, api_key: str, timeout: int, user_agent: str = DEFAULT_USER_AGENT) -> dict[str, Any]:
    req = request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        response_body = response.read().decode("utf-8")
        return {"status": response.status, "body": json.loads(response_body)}


def generation_sidecar_payload(
    args: argparse.Namespace,
    config: dict[str, Any],
    output_path: Path,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    command = getattr(args, "command", "")
    model = config["tool_model"] if command == "responses" else config["model"]
    parameters: dict[str, Any] = {}
    for key in (
        "preset",
        "size",
        "quality",
        "output_format",
        "aspect_ratio",
        "resolution",
        "background",
        "n",
        "compression",
        "stream",
    ):
        value = getattr(args, key, None)
        if value is not None and value is not False:
            parameters[key] = value
    prompt = getattr(args, "prompt", None)
    if prompt is None:
        prompt = " ".join(getattr(args, "input_text", []) or []) or None
    payload: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "image-generation-sidecar",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": command,
        "model": model,
        "choice": getattr(args, "choice", None),
        "base_url": config["base_url"],
        "provider_profile": config["provider_profile"],
        "prompt": prompt,
        "reference_images": getattr(args, "image", None) or getattr(args, "input_image", None) or None,
        "mask": getattr(args, "mask", None),
        "parameters": parameters,
        "output": {
            "path": str(output_path.resolve()),
            "bytes": output_path.stat().st_size,
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            "final_size": artifact.get("final_size"),
            "final_format": artifact.get("final_format"),
        },
    }
    if command == "responses":
        payload["text_model"] = config.get("responses_model")
    return payload


def write_generation_sidecars(
    args: argparse.Namespace,
    config: dict[str, Any],
    saved: list[str],
    artifacts: list[dict[str, Any]],
) -> list[str]:
    """Write one reproducibility record next to each saved local image.

    The sidecar captures the exact prompt, model selection, and parameters so
    any past generation can be understood and reproduced later. It never
    contains credentials, and it is skipped entirely with --no-sidecar.
    """
    if getattr(args, "no_sidecar", False):
        return []
    sidecars: list[str] = []
    for index, saved_path in enumerate(saved):
        candidate = Path(saved_path)
        if not candidate.is_file():
            continue
        artifact = artifacts[index] if index < len(artifacts) else {}
        sidecar_path = candidate.with_name(candidate.name + ".json")
        write_json(sidecar_path, generation_sidecar_payload(args, config, candidate, artifact))
        sidecars.append(str(sidecar_path.resolve()))
    return sidecars


def run_retrying_request(args: argparse.Namespace, config: dict[str, Any], request_fn: Any, save_fn: Any) -> int:
    last_error: dict[str, Any] | None = None
    attempts = max(0, config["retries"]) + 1
    for attempt in range(1, attempts + 1):
        try:
            response_payload = request_fn()
            save_result = save_fn(response_payload)
            if isinstance(save_result, dict):
                saved = save_result.get("saved", [])
                artifacts = save_result.get("artifacts", [])
            else:
                saved = save_result
                artifacts = []
            sidecars = write_generation_sidecars(args, config, saved, artifacts)
            summary = {
                "ok": True,
                "url": args._request_url,
                "auth": mask_key(config["api_key"]),
                "model": config["tool_model"] if args.command == "responses" else config["model"],
                "model_requested": config.get("tool_model_requested") if args.command == "responses" else config.get("model_requested"),
                "model_alias": config.get("tool_model_alias") if args.command == "responses" else config.get("model_alias"),
                "model_source": config.get("tool_model_source") if args.command == "responses" else config.get("model_source"),
                "provider_profile": config["provider_profile"],
                "routing_mode": config["routing_mode"],
                "selected_vendor": config.get("selected_vendor"),
                "selection_reason": config.get("selection_reason"),
                "selection_mode": config.get("selection_mode", "default"),
                "selection_choice": getattr(args, "choice", None),
                "selection_choice_alias": model_choice_alias(getattr(args, "choice", None)),
                "attempt": attempt,
                "saved": saved,
                "sidecars": sidecars,
                "artifacts": artifacts,
                "raw_saved": str(Path(args.save_response).resolve()) if getattr(args, "save_response", None) else None,
            }
            if getattr(args, "save_response", None):
                write_json(Path(args.save_response), response_payload)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        except OutputProcessingError as exc:
            last_error = {
                "status": None,
                "category": exc.category,
                "retryable": False,
                "summary": exc.summary,
                "details": exc.details,
                "attempt": attempt,
            }
            if getattr(args, "save_error", None):
                write_json(Path(args.save_error), last_error)
            print(json.dumps(last_error, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = classify_error(exc.code, body, exc.reason)
            last_error["attempt"] = attempt
            if getattr(args, "save_error", None):
                write_json(Path(args.save_error), last_error)
            if not last_error["retryable"] or attempt == attempts:
                print(json.dumps(last_error, ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
        except error.URLError as exc:
            last_error = classify_error(None, "", str(exc.reason))
            last_error["attempt"] = attempt
            if getattr(args, "save_error", None):
                write_json(Path(args.save_error), last_error)
            if attempt == attempts:
                print(json.dumps(last_error, ensure_ascii=False, indent=2), file=sys.stderr)
                return 1
        if attempt < attempts:
            time.sleep(min(config["retry_delay"] * (2 ** (attempt - 1)), 8))
    if last_error:
        print(json.dumps(last_error, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


def requires_live_model_selection(
    args: argparse.Namespace,
    image_model_arg: str = "model",
    config: dict[str, Any] | None = None,
) -> bool:
    """Whether a live auto request lacks a command-scoped image-model choice."""
    if getattr(args, "dry_run", False):
        return False
    if getattr(args, image_model_arg, None):
        return False
    mode = config.get("routing_mode") if config else getattr(args, "mode", None)
    if mode not in (None, "auto"):
        return False
    return True


def apply_catalog_model_selection(
    args: argparse.Namespace,
    config: dict[str, Any],
    operation: str,
    image_model_key: str = "model",
) -> None:
    """Resolve a persisted catalog choice without contacting the provider.

    Model inventory is intentionally a configure-time concern. Generation,
    editing, and Responses calls must remain deterministic and must never make
    a hidden ``GET /v1/models`` request before the paid image request.
    """
    image_model_arg = "tool_model" if image_model_key == "tool_model" else "model"
    choice = getattr(args, "choice", None)
    explicit_model = getattr(args, image_model_arg, None)
    if choice and explicit_model:
        raise SystemExit("--choice cannot be combined with an explicit --model or --tool-model.")
    if choice and config.get("routing_mode") not in (None, "auto"):
        raise SystemExit("--choice cannot be combined with an explicit non-auto --mode.")
    if not choice:
        # No number means the invariant global default unless the caller used
        # the existing explicit --model or --tool-model compatibility flags.
        selected_model = str(config[image_model_key])
        source_key = "tool_model_source" if image_model_key == "tool_model" else "model_source"
        if explicit_model:
            config["selection_reason"] = "explicit_model"
            config["selection_mode"] = "explicit"
            config["selected_vendor"] = enabled_vendor_for_model(selected_model)
        else:
            config["selection_reason"] = "default_gpt_image_2"
            config["selection_mode"] = "default"
            config["selected_vendor"] = enabled_vendor_for_model(selected_model) or "openai"
        config[source_key] = config.get(source_key) or ("cli" if explicit_model else "default")
        return

    selected = resolve_catalog_choice(config, str(choice), operation)
    requested_vendor = config.get("vendor_requested")
    if requested_vendor:
        catalog = load_model_catalog(config)
        expected_vendor = _catalog_vendor_id(catalog or {}, str(requested_vendor))
        if expected_vendor and str(selected.get("vendor")) != expected_vendor:
            raise SystemExit(
                f"Catalog choice {choice} belongs to vendor '{selected.get('vendor')}', "
                f"but --vendor requested '{requested_vendor}'. Choose a number from that vendor."
            )
    config[image_model_key] = str(selected["model"])
    config["provider_profile"] = resolve_provider_profile(
        config[image_model_key], config["provider_profile_requested"]
    )
    source_key = "tool_model_source" if image_model_key == "tool_model" else "model_source"
    config[source_key] = "catalog"
    config["selected_vendor"] = selected.get("vendor")
    config["selection_choice"] = choice
    config["selection_choice_alias"] = model_choice_alias(choice)
    config["selection_reason"] = "catalog_choice"
    config["selection_mode"] = "catalog"


def cmd_generate(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    apply_catalog_model_selection(args, config, "generate")
    apply_routing_mode(args, config)
    prepare_common_image_args(args, config)
    validate_generation_args(args, config)
    validate_base_url(config["base_url"])
    if not config["api_key"] and not args.dry_run:
        raise SystemExit("Missing API key. Set IMAGE_GENERATION_API_KEY, GPT_IMAGE_API_KEY, OPENAI_API_KEY, or pass --api-key.")

    url = f"{config['base_url']}/images/generations"
    payload = build_generation_request(args, config)
    run_interactive_confirmation(args, config, url, payload, "generate")
    if args.dry_run:
        print_request_record(args, build_request_record(url, config, payload, args))
        return 0

    args._request_url = url
    request_fn = post_json_stream if args.stream else post_json
    save_fn = decode_and_save_response_images if args.stream else decode_and_save_images
    return run_retrying_request(
        args,
        config,
        lambda: request_fn(url, config["api_key"], payload, config["timeout"], config["user_agent"]),
        lambda response_payload: save_fn(
            response_payload,
            Path(args.output),
            args.size,
            args.size_policy,
        ),
    )


def validate_file(path_text: str, label: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"{label} file not found: {path}")
    return path


def build_openai_edit_request(args: argparse.Namespace, config: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, Path]]]:
    fields: dict[str, Any] = {
        "model": config["model"],
        "prompt": args.prompt,
        "size": args.size,
        "quality": args.quality,
        "n": args.n,
        "output_format": args.output_format,
    }
    if args.compression is not None:
        fields["output_compression"] = args.compression
    if args.user:
        fields["user"] = args.user
    if args.background:
        fields["background"] = args.background
    if args.moderation:
        fields["moderation"] = args.moderation
    if args.stream:
        fields["stream"] = True
    if args.partial_images is not None:
        fields["partial_images"] = args.partial_images
    if config["provider_profile"] == "generic" and args.response_format:
        fields["response_format"] = args.response_format
    merge_extra_json(fields, args)
    files: list[tuple[str, Path]] = []
    for image_path in args.image:
        files.append((args.image_field, validate_file(image_path, "image")))
    if args.mask:
        files.append(("mask", validate_file(args.mask, "mask")))
    return fields, files


def build_grok_edit_request(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if args.mask:
        raise SystemExit("The Grok JSON edit route does not support --mask; use gpt-image-2 for masked edits.")
    if len(args.image) > 3:
        raise SystemExit("The Grok JSON edit route supports at most three --image inputs.")
    images = [file_to_data_url(validate_file(image_path, "image")) for image_path in args.image]
    aspect_ratio, resolution = grok_shape_from_args(args)
    payload: dict[str, Any] = {
        "model": config["model"],
        "prompt": args.prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "response_format": args.response_format or "b64_json",
    }
    if len(images) == 1:
        payload["image"] = {"url": images[0]}
    else:
        payload["images"] = [{"type": "image_url", "url": image} for image in images]
    if config["model"] == "grok-imagine-image-2.0":
        payload["quality"] = args.quality
    merge_extra_json(payload, args)
    return payload


def cmd_edit(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    apply_catalog_model_selection(args, config, "edit")
    apply_routing_mode(args, config)
    prepare_common_image_args(args, config)
    resolve_prompt(args)
    require_prompt(args)
    if not args.image:
        raise SystemExit("edit requires at least one --image.")
    validate_provider_image_options(args, config, "edit")
    validate_base_url(config["base_url"])
    if not config["api_key"] and not args.dry_run:
        raise SystemExit("Missing API key. Set IMAGE_GENERATION_API_KEY, GPT_IMAGE_API_KEY, OPENAI_API_KEY, or pass --api-key.")
    url = f"{config['base_url']}/images/edits"
    if config["provider_profile"] == "grok":
        payload = build_grok_edit_request(args, config)
        run_interactive_confirmation(args, config, url, payload, "edit")
        if args.dry_run:
            print_request_record(args, build_request_record(url, config, payload, args))
            return 0
        args._request_url = url
        return run_retrying_request(
            args,
            config,
            lambda: post_json(url, config["api_key"], payload, config["timeout"], config["user_agent"]),
            lambda response_payload: decode_and_save_images(
                response_payload,
                Path(args.output),
                args.size,
                args.size_policy,
            ),
        )

    fields, files = build_openai_edit_request(args, config)
    run_interactive_confirmation(args, config, url, fields, "edit")
    if args.dry_run:
        multipart_summary = {
            "fields": fields,
            "files": [{"field": field, "path": str(path), "bytes": path.stat().st_size} for field, path in files],
        }
        print_request_record(args, build_request_record(url, config, multipart_summary, args, "multipart"))
        return 0
    content_type, body = encode_multipart(fields, files)
    args._request_url = url
    request_fn = post_multipart_stream if args.stream else post_multipart
    save_fn = decode_and_save_response_images if args.stream else decode_and_save_images
    return run_retrying_request(
        args,
        config,
        lambda: request_fn(
            url,
            config["api_key"],
            content_type,
            body,
            config["timeout"],
            config["user_agent"],
        ),
        lambda response_payload: save_fn(
            response_payload,
            Path(args.output),
            args.size,
            args.size_policy,
        ),
    )


def validate_responses_args(args: argparse.Namespace, config: dict[str, Any]) -> None:
    prepare_common_image_args(args, config)
    if config["responses_model"].startswith("gpt-image-"):
        raise SystemExit("For responses image_generation, use a text-capable Responses model instead of gpt-image-2 as the top-level model.")
    if not args.input_text and not args.prompt and not args.prompt_file and not args.input_image:
        raise SystemExit("responses requires --input-text, --prompt, --prompt-file, or --input-image.")
    resolve_prompt(args)
    validate_provider_image_options(args, config, "responses")


def file_to_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_responses_request(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for text in args.input_text:
        content.append({"type": "input_text", "text": text})
    if args.prompt:
        content.append({"type": "input_text", "text": args.prompt})
    for image_path in args.input_image:
        content.append({"type": "input_image", "image_url": file_to_data_url(validate_file(image_path, "input image"))})
    tool: dict[str, Any] = {
        "type": "image_generation",
        "size": args.size,
        "quality": args.quality,
        "output_format": args.output_format,
    }
    include_tool_model = config["tool_model_policy"] == "include" or (
        config["tool_model_policy"] == "auto"
        and (
            config.get("tool_model_source") in {"cli", "configured", "discovered", "routing_mode"}
            or not is_official_openai_base_url(config["base_url"])
        )
    )
    if include_tool_model:
        tool["model"] = config["tool_model"]
    if args.compression is not None:
        tool["output_compression"] = args.compression
    if args.background:
        tool["background"] = args.background
    if args.moderation:
        tool["moderation"] = args.moderation
    if args.partial_images is not None:
        tool["partial_images"] = args.partial_images
    if args.action:
        tool["action"] = args.action
    if args.mask:
        tool["input_image_mask"] = {"image_url": file_to_data_url(validate_file(args.mask, "mask"))}
    merge_extra_json(tool, args, "tool_extra_json", "tool_extra_json_file")
    payload: dict[str, Any] = {
        "model": config["responses_model"],
        "input": [{"role": "user", "content": content}],
        "tools": [tool],
        "tool_choice": {"type": "image_generation"},
    }
    if args.stream:
        payload["stream"] = True
    if args.previous_response_id:
        payload["previous_response_id"] = args.previous_response_id
    merge_extra_json(payload, args)
    return payload


def cmd_responses(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    apply_catalog_model_selection(args, config, "generate", image_model_key="tool_model")
    apply_routing_mode(args, config)
    validate_responses_args(args, config)
    validate_base_url(config["base_url"])
    if not config["api_key"] and not args.dry_run:
        raise SystemExit("Missing API key. Set IMAGE_GENERATION_API_KEY, GPT_IMAGE_API_KEY, OPENAI_API_KEY, or pass --api-key.")
    url = f"{config['base_url']}/responses"
    payload = build_responses_request(args, config)
    run_interactive_confirmation(args, config, url, payload, "responses")
    if args.dry_run:
        print_request_record(args, build_request_record(url, config, payload, args))
        return 0
    args._request_url = url
    request_fn = post_json_stream if args.stream else post_json
    return run_retrying_request(
        args,
        config,
        lambda: request_fn(url, config["api_key"], payload, config["timeout"], config["user_agent"]),
        lambda response_payload: decode_and_save_response_images(
            response_payload,
            Path(args.output),
            args.size,
            args.size_policy,
        ),
    )


def cmd_classify_error(args: argparse.Namespace) -> int:
    status = None if args.status.lower() == "none" else int(args.status)
    print(json.dumps(classify_error(status, args.body or "", args.reason or ""), ensure_ascii=False, indent=2))
    return 0


def cmd_configure(args: argparse.Namespace) -> int:
    """Discover once and persist the deterministic model-number catalog."""
    config = resolve_config(args)
    result: dict[str, Any] = {
        "ok": False,
        "command": "configure",
        "source": "/v1/models",
        "base_url": config["base_url"],
        "catalog_path": config["catalog_path"],
        "auth": mask_key(config["api_key"]),
        "default_model": DEFAULT_MODEL,
        "default_model_guard": {
            "required": DEFAULT_MODEL,
            "available": None,
            "warning": False,
        },
    }
    try:
        validate_base_url(config["base_url"])
    except SystemExit as exc:
        result["error"] = {
            "category": "invalid_base_url",
            "summary": str(exc),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    if not config["api_key"]:
        result["error"] = {
            "category": "missing_api_key",
            "summary": "Missing API key. Set IMAGE_GENERATION_API_KEY, GPT_IMAGE_API_KEY, OPENAI_API_KEY, or pass --api-key.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    try:
        # This is deliberately the only network call made by configure.
        response = get_json(
            f"{config['base_url']}/models",
            config["api_key"],
            config["timeout"],
            config["user_agent"],
        )
        catalog = build_model_catalog(config, response["body"])
        catalog_path = Path(config["catalog_path"])
        write_model_catalog(catalog_path, catalog)
        model_ids = extract_model_ids(response["body"])
        result.update(
            {
                "ok": True,
                "status": response["status"],
                "total_model_count": len(model_ids),
                "choice_count": catalog["choice_count"],
                "default_choice": catalog["default_choice"],
                "gpt_image_2_listed": catalog["gpt_image_2_listed"],
                "default_model_guard": {
                    "required": DEFAULT_MODEL,
                    "available": catalog["gpt_image_2_listed"],
                    "warning": not catalog["gpt_image_2_listed"],
                    "category": "ok" if catalog["gpt_image_2_listed"] else "gpt_image_2_not_listed",
                },
                "choices": catalog["choices"],
                "vendors": catalog["vendors"],
                "selection_mode": "catalog",
                "next_steps": [
                    f"Use --choice N with generate/edit/responses; the catalog is bound to {config['base_url']}.",
                    f"Without --choice, the default is always {DEFAULT_MODEL}.",
                ],
            }
        )
        if not catalog["gpt_image_2_listed"]:
            result["warning"] = (
                "The gateway did not list gpt-image-2. It remains the built-in default and choice "
                f"{catalog['default_choice']}; verify gateway access before a paid request."
            )
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        result["error"] = classify_error(exc.code, body, exc.reason)
    except error.URLError as exc:
        result["error"] = classify_error(None, "", str(exc.reason))
    except (ValueError, TypeError) as exc:
        result["error"] = {
            "category": "invalid_model_list",
            "summary": f"The provider returned an unreadable /v1/models payload: {exc}",
        }
    except OSError as exc:
        result["error"] = {
            "category": "catalog_write_failed",
            "summary": f"Could not write the model catalog at {config['catalog_path']}: {exc}",
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def cmd_options(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            {
                "presets": PRESETS,
                "sizes": SIZE_VALUES,
                "quality": QUALITY_VALUES,
                "formats": FORMAT_VALUES,
                "size_policies": SIZE_POLICY_VALUES,
                "provider_profiles": PROVIDER_PROFILE_VALUES,
                "executable_provider_profiles": list(EXECUTABLE_PROVIDER_PROFILES),
                "enabled_vendors": list(ENABLED_IMAGE_VENDORS),
                "enabled_model_families": list(ENABLED_IMAGE_FAMILIES),
                "routing_modes": ROUTING_MODE_VALUES,
                "backgrounds": BACKGROUND_VALUES,
                "moderation": MODERATION_VALUES,
                "response_formats": RESPONSE_FORMAT_VALUES,
                "grok_aspect_ratios": GROK_ASPECT_RATIO_VALUES,
                "grok_resolutions": GROK_RESOLUTION_VALUES,
                "model_aliases": MODEL_ALIASES,
                "defaults": {
                    "model": DEFAULT_MODEL,
                    "required_default_model": "gpt-image-2",
                    "provider_profile": DEFAULT_PROVIDER_PROFILE,
                    "routing_mode": DEFAULT_ROUTING_MODE,
                    "tool_model_policy": DEFAULT_TOOL_MODEL_POLICY,
                    "size": DEFAULT_SIZE,
                    "quality": DEFAULT_QUALITY,
                    "format": DEFAULT_FORMAT,
                    "timeout": DEFAULT_TIMEOUT,
                    "retries": DEFAULT_RETRIES,
                    "retry_delay": DEFAULT_RETRY_DELAY,
                    "size_policy": DEFAULT_SIZE_POLICY,
                    "user_agent": DEFAULT_USER_AGENT,
                },
                "notes": [
                    "gpt-image-2 is always retained in the built-in registry and remains explicitly selectable.",
                    "Run configure once to fetch /v1/models and persist one stable number for every executable GPT Image or Grok Image model.",
                    "Generation, editing, and Responses resolve --choice from the local catalog and never perform hidden model discovery.",
                    "Without --choice, and unless an explicit --model/--tool-model or routing mode is supplied, the model is always gpt-image-2.",
                    "Configured IMAGE_GENERATION_MODEL and legacy model variables are retained for diagnostics but cannot change the no-choice default.",
                    "Only GPT Image and Grok Image models enter the numbered catalog or image requests; other recognized vendors are discovery-only.",
                    "Use a model alias such as gpt2, gpt4k, grok, grok2, or grok-quality; output always records the canonical provider id.",
                    "Use --preset to choose a preset, then override individual fields with --size, --quality, or --format.",
                    "A third-party gateway may expose many image models, but this skill sends image requests only for gpt-image-* and grok-imagine-image* model IDs.",
                    "The default --size-policy normalize resamples same-aspect provider output to the requested dimensions and rejects aspect-ratio changes.",
                    "Use edit for multipart image edits with --image and optional --mask.",
                    "Use responses for image_generation tool flows; keep the top-level --model text-capable and set the image model with --tool-model.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def extract_model_ids(payload: Any) -> list[str]:
    return [record["id"] for record in extract_model_records(payload)]


def _first_non_empty_model_list(*values: Any) -> list[Any] | None:
    """Return the first non-empty model list from common gateway wrappers.

    Some gateways emit an empty ``data`` field alongside the real inventory in
    ``models`` or ``items``. A plain truthiness helper would stop at the empty
    list and hide the available models, so empty lists are retained only as a
    final fallback.
    """
    empty_list: list[Any] | None = None
    for value in values:
        if isinstance(value, list):
            if value:
                return value
            if empty_list is None:
                empty_list = value
            continue
        if isinstance(value, dict):
            nested = _first_non_empty_model_list(
                value.get("data"), value.get("models"), value.get("items")
            )
            if nested:
                return nested
            if nested is not None and empty_list is None:
                empty_list = nested
    return empty_list


def extract_model_records(payload: Any) -> list[dict[str, Any]]:
    """Normalize OpenAI-compatible model-list variants without dropping metadata."""
    data: Any = payload
    if isinstance(payload, dict):
        # `/v1/models` is normally {data: [...]}, but several gateways use
        # `models`, `items`, or wrap the list one level deeper.
        data = _first_non_empty_model_list(
            payload.get("data"), payload.get("models"), payload.get("items")
        )
    if not isinstance(data, list):
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data:
        if isinstance(item, dict) and item.get("id") not in (None, ""):
            model_id = str(item["id"])
            metadata = dict(item)
        elif isinstance(item, str):
            model_id = item
            metadata = {"id": model_id}
        else:
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        records.append({"id": model_id, "metadata": metadata})
    return records


def classify_live_image_models(
    payload: Any,
    configured_model: str,
    operation: str = "generate",
    preferred_vendor: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    for raw_record in extract_model_records(payload):
        model_id = str(raw_record["id"])
        metadata = raw_record.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        if not is_image_model_id(model_id, metadata):
            continue
        record = capability_for_model(model_id, metadata)
        record["listed"] = True
        record["verification"] = "listed_only"
        record["metadata"] = {
            key: metadata[key]
            for key in MODEL_METADATA_VENDOR_KEYS + MODEL_METADATA_IMAGE_KEYS
            if key in metadata
        }
        records.append(record)
    return records, group_image_model_records(records, configured_model, operation, preferred_vendor)


def _choice_is_recommended(record: dict[str, Any]) -> bool:
    status = str(record.get("status", "listed")).lower()
    if status in UNVERIFIED_MODEL_STATUSES:
        return False
    return status in STABLE_MODEL_STATUSES or status == "listed"


def flatten_model_choices(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build one deterministic, auditable list for a single user prompt."""
    raw_choices: list[dict[str, Any]] = []
    vendor_order = {vendor: index for index, vendor in enumerate(VENDOR_PRIORITY)}
    ordered_groups = sorted(
        groups,
        key=lambda group: (
            vendor_order.get(normalize_vendor_id(str(group["id"])), len(vendor_order)),
            str(group.get("name") or group["id"]),
        ),
    )
    for group in ordered_groups:
        vendor_id = str(group["id"])
        vendor_name = str(group["name"])
        for item in group["models"]:
            model_id = str(item["id"])
            raw_choices.append(
                {
                    "vendor": vendor_id,
                    "vendor_name": vendor_name,
                    "model": model_id,
                    "id": model_id,
                    "status": item.get("status", "listed"),
                    "provider_profile": item.get("provider_profile"),
                    "generation": item.get("generation"),
                    "editing": item.get("editing"),
                    "listed": bool(item.get("listed", False)),
                    "recommended": _choice_is_recommended(item),
                    "default": model_id == str(group.get("default_model")),
                    "aliases": model_aliases_for_model(model_id),
                }
            )

    # Vendor order is the primary contract: all OpenAI entries precede all
    # xAI entries. Recommendation tiers remain metadata and never split a
    # vendor's contiguous block.
    return [{"number": index, **item} for index, item in enumerate(raw_choices, start=1)]


def build_model_catalog(config: dict[str, Any], payload: Any) -> dict[str, Any]:
    records, _ = classify_live_image_models(payload, DEFAULT_MODEL, "any", None)
    deduplicated: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("enabled_for_execution"):
            deduplicated.setdefault(str(record["id"]).lower(), record)
    gpt_image_2_key = DEFAULT_MODEL.lower()
    gpt_image_2_listed = gpt_image_2_key in deduplicated
    if not gpt_image_2_listed:
        guarded_default = capability_for_model(DEFAULT_MODEL)
        guarded_default.update({"listed": False, "verification": "built_in_default_guard"})
        deduplicated[gpt_image_2_key] = guarded_default
    executable_records = list(deduplicated.values())
    groups = group_image_model_records(executable_records, DEFAULT_MODEL, "any", None)
    choices = flatten_model_choices(groups)
    # ``flatten_model_choices`` also marks each vendor's preferred model. The
    # persisted catalog has one global default, so expose that distinction
    # explicitly and keep gpt-image-2 as the only ``default`` entry.
    for item in choices:
        item["vendor_default"] = bool(item.get("default"))
        item["default"] = str(item.get("model", "")).lower() == DEFAULT_MODEL.lower()
    return {
        "schema_version": MODEL_CATALOG_SCHEMA_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "/v1/models",
        "base_url": config["base_url"],
        "default_model": DEFAULT_MODEL,
        "default_choice": next(
            (item["number"] for item in choices if str(item["model"]).lower() == DEFAULT_MODEL.lower()),
            None,
        ),
        "gpt_image_2_listed": gpt_image_2_listed,
        "choice_count": len(choices),
        "choices": choices,
        "vendors": compact_vendor_groups(groups),
    }


def write_model_catalog(path: Path, catalog: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def selection_choice_bundle(groups: list[dict[str, Any]]) -> dict[str, Any]:
    choices = flatten_model_choices(groups)
    return {
        "selection_mode": "ask-once",
        "choice_input": "number, exact model id, or alias",
        "choice_count": len(choices),
        "choices": choices,
        # Keep the response compact: full choice objects live only in
        # ``choices``; these arrays are stable number indexes into it.
        "recommended_choices": [item["number"] for item in choices if item["recommended"]],
        "more_choices": [item["number"] for item in choices if not item["recommended"]],
    }


def _clean_model_choice_input(value: Any) -> str:
    text = clean_interactive_input(str(value or "")).strip()
    if "=" in text:
        prefix, suffix = text.split("=", 1)
        if prefix.strip().lower() in {"model", "choice", "select", "模型", "选择"}:
            text = suffix.strip()
    return text


def resolve_model_choice(value: Any, choices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve a number, exact id, or friendly alias to one flat choice."""
    text = _clean_model_choice_input(value)
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return next((item for item in choices if int(item["number"]) == number), None)
    resolved, _ = canonical_model_reference(text)
    lowered = resolved.lower()
    raw_lowered = text.lower()
    for item in choices:
        if str(item["model"]).lower() == lowered or str(item["model"]).lower() == raw_lowered:
            return item
        if raw_lowered in {str(alias).lower() for alias in item.get("aliases", [])}:
            return item
    return None


def model_choice_alias(value: Any) -> str | None:
    text = _clean_model_choice_input(value)
    if not text or text.isdigit():
        return None
    _, alias = canonical_model_reference(text)
    return alias


def find_vendor_group(groups: list[dict[str, Any]], requested_vendor: str) -> dict[str, Any] | None:
    """Resolve a CLI vendor id/name using the same rules everywhere."""
    vendor_key = str(requested_vendor or "").strip().lower()
    if not vendor_key:
        return None
    by_id = {str(group["id"]): group for group in groups}
    by_name = {str(group["name"]).strip().lower(): group for group in groups}
    by_normalized_id = {normalize_vendor_id(str(group["id"])): group for group in groups}
    return (
        by_id.get(vendor_key)
        or by_name.get(vendor_key)
        or by_normalized_id.get(normalize_vendor_id(vendor_key))
    )


def selection_required_details(
    groups: list[dict[str, Any]],
    operation: str,
    summary: str,
    selected_vendor: str | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "ok": False,
        "selection_required": True,
        "category": "model_selection_required",
        "summary": summary,
        "operation": operation,
        "selected_vendor": None,
        "selected_model": None,
        **selection_choice_bundle(groups),
        "vendors": [
            {
                "id": group["id"],
                "name": group["name"],
                "default_model": group["default_model"],
                "models": [item["id"] for item in group["models"]],
            }
            for group in groups
        ],
        "next_steps": [
            "Reply once with a choice number, exact model id, or alias; pass that exact id to the next command.",
            "The selected model may be reused for later requests in this task until the user asks to change it.",
            "Keep gpt-image-2 selectable when the OpenAI image route is required.",
        ],
    }
    if selected_vendor:
        group = next((item for item in groups if str(item["id"]) == str(selected_vendor)), None)
        if group:
            details["selected_vendor"] = group["id"]
            details["selected_vendor_name"] = group["name"]
            details["models"] = [item["id"] for item in group["models"]]
    return details


def prompt_for_flat_model_choice(groups: list[dict[str, Any]]) -> tuple[str, str]:
    choices = flatten_model_choices(groups)
    if len(groups) > 1:
        print("Available image vendors and models (choose one entry):", file=sys.stderr)
    else:
        print(f"Available {groups[0]['name']} image models (choose one entry):", file=sys.stderr)
    for item in choices:
        recommendation = "recommended" if item["recommended"] else "more"
        aliases = f" aliases={','.join(item['aliases'])}" if item.get("aliases") else ""
        print(
            f"  {item['number']}. {item['vendor_name']} / {item['model']} "
            f"[{item['status']}; {recommendation}]{aliases}",
            file=sys.stderr,
        )
    print("Choose one number, exact model id, or alias: ", end="", file=sys.stderr, flush=True)
    selected = resolve_model_choice(sys.stdin.readline(), choices)
    if selected is None:
        raise SystemExit("Invalid model choice. Reply with a listed number, exact model id, or alias.")
    return str(selected["vendor"]), str(selected["model"])


def select_model_from_groups(
    groups: list[dict[str, Any]],
    preferred_model: str | None = None,
    requested_vendor: str | None = None,
    interactive: bool = False,
    choice: str | None = None,
    operation: str = "generate",
) -> tuple[str, str]:
    if not groups:
        raise SystemExit("No image-generation models were found in the provider model list.")
    del preferred_model  # Ordering is already encoded by group_image_model_records().
    candidate_groups = groups
    if requested_vendor:
        selected_group = find_vendor_group(groups, requested_vendor)
        if selected_group is None:
            choices = ", ".join(f"{item['id']} ({item['name']})" for item in groups)
            raise SystemExit(f"Unknown vendor '{requested_vendor}'. Choose one of: {choices}")
        candidate_groups = [selected_group]

    candidate_choices = flatten_model_choices(candidate_groups)
    if choice is not None:
        selected = resolve_model_choice(choice, candidate_choices)
        if selected is None:
            available = ", ".join(f"{item['number']}={item['model']}" for item in candidate_choices)
            raise SystemExit(f"Invalid model choice '{choice}'. Choose one of: {available}")
        return str(selected["vendor"]), str(selected["model"])

    if len(candidate_groups) == 1 and len(candidate_groups[0]["models"]) == 1:
        item = candidate_groups[0]["models"][0]
        return str(candidate_groups[0]["id"]), str(item["id"])

    if not interactive:
        if len(candidate_groups) == 1:
            summary = (
                f"Vendor {candidate_groups[0]['name']} exposes multiple image models; "
                "choose one entry before the paid request."
            )
        else:
            summary = "Multiple image vendors/models are available; choose one complete vendor/model entry before the paid request."
        raise ModelSelectionRequired(
            selection_required_details(
                candidate_groups,
                operation,
                summary,
                str(candidate_groups[0]["id"]) if len(candidate_groups) == 1 else None,
            )
        )

    return prompt_for_flat_model_choice(candidate_groups)


def compact_vendor_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": group["id"],
            "name": group["name"],
            "default_model": group["default_model"],
            "models": [
                {
                    "id": item["id"],
                    "status": item.get("status"),
                    "provider_profile": item.get("provider_profile"),
                    "generation": item.get("generation"),
                    "editing": item.get("editing"),
                    "recommended": _choice_is_recommended(item),
                    "aliases": model_aliases_for_model(item["id"]),
                }
                for item in group["models"]
            ],
        }
        for group in groups
    ]


def _catalog_choices_for_operation(catalog: dict[str, Any], operation: str) -> list[dict[str, Any]]:
    choices = [item for item in catalog.get("choices", []) if isinstance(item, dict)]
    if operation == "any":
        return choices
    capability_key = "generation" if operation == "generate" else "editing"
    return [item for item in choices if item.get(capability_key) is not False]


def _catalog_vendor_id(catalog: dict[str, Any], requested: str | None) -> str | None:
    if not requested:
        return None
    groups = [item for item in catalog.get("vendors", []) if isinstance(item, dict)]
    group = find_vendor_group(groups, requested)
    if group is None:
        choices = ", ".join(f"{item.get('id')} ({item.get('name')})" for item in groups)
        raise SystemExit(f"Unknown vendor '{requested}'. Choose one of: {choices or 'none'}")
    return str(group["id"])


def cmd_select_model(args: argparse.Namespace) -> int:
    """Display or resolve the fixed local catalog; never perform discovery."""
    config = resolve_config(args)
    result: dict[str, Any] = {
        "ok": False,
        "command": "select-model",
        "source": "catalog",
        "base_url": config["base_url"],
        "catalog_path": config["catalog_path"],
        "operation": args.operation,
        "default_model": DEFAULT_MODEL,
        "selection_mode": "catalog",
        "selection_choice": getattr(args, "choice", None),
        "selection_choice_alias": model_choice_alias(getattr(args, "choice", None)),
        "choice_input": "number, exact model id, or alias",
        "selected_vendor": None,
        "selected_model": None,
        "selection_required": False,
    }
    try:
        validate_base_url(config["base_url"])
        catalog = load_model_catalog(config)
        if catalog is None:
            result["error"] = {
                "category": "model_catalog_missing",
                "summary": "No model catalog found. Run `image_generation_api.py configure` once first.",
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        choices = _catalog_choices_for_operation(catalog, args.operation)
        requested_vendor = _catalog_vendor_id(catalog, config.get("vendor_requested"))
        if requested_vendor:
            choices = [item for item in choices if str(item.get("vendor")) == requested_vendor]
        result.update(
            {
                "gpt_image_2_listed": bool(catalog.get("gpt_image_2_listed")),
                "default_choice": catalog.get("default_choice"),
                "choice_count": len(choices),
                "choices": choices,
                "vendors": catalog.get("vendors", []),
                "requested_vendor": config.get("vendor_requested"),
                "filtered_vendor": requested_vendor,
                "recommended_choices": [item["number"] for item in choices if item.get("recommended")],
                "more_choices": [item["number"] for item in choices if not item.get("recommended")],
            }
        )
        explicit_model_input = getattr(args, "model", None)
        choice_input = getattr(args, "choice", None)
        if explicit_model_input and choice_input:
            raise SystemExit("Use either --model/alias or --choice, not both.")
        selected: dict[str, Any] | None = None
        selection_reason = "catalog_default"
        if explicit_model_input:
            selected = resolve_model_choice(explicit_model_input, choices)
            if selected is None:
                raise SystemExit(
                    f"Model '{explicit_model_input}' is not in the catalog for {args.operation}. "
                    "Run configure again if the provider inventory changed."
                )
            selection_reason = "explicit_model"
        elif choice_input is not None:
            selected = resolve_model_choice(choice_input, choices)
            if selected is None:
                available = ", ".join(f"{item['number']}={item['model']}" for item in choices)
                raise SystemExit(f"Invalid catalog model choice '{choice_input}'. Available: {available or 'none'}")
            selection_reason = "catalog_choice"
        elif getattr(args, "interactive", False):
            if not choices:
                raise SystemExit("The catalog has no model for the requested operation. Run configure again.")
            print("Available catalog image models (choose one entry):", file=sys.stderr)
            for item in choices:
                aliases = f" aliases={','.join(item.get('aliases', []))}" if item.get("aliases") else ""
                print(
                    f"  {item['number']}. {item.get('vendor_name', item.get('vendor'))} / {item['model']}"
                    f" [{item.get('status', 'listed')}]" + aliases,
                    file=sys.stderr,
                )
            print("Choose one number, exact model id, or alias: ", end="", file=sys.stderr, flush=True)
            selected = resolve_model_choice(sys.stdin.readline(), choices)
            if selected is None:
                raise SystemExit("Invalid model choice. Reply with a listed number, exact model id, or alias.")
            selection_reason = "catalog_choice"
        else:
            default_choice = catalog.get("default_choice")
            selected = next(
                (item for item in choices if item.get("number") == default_choice),
                next((item for item in choices if str(item.get("model")).lower() == DEFAULT_MODEL.lower()), None),
            )
        if selected is not None:
            model = str(selected.get("model") or selected.get("id") or "")
            validate_enabled_image_model(model, args.operation)
            result.update(
                {
                    "ok": True,
                    "selected_vendor": selected.get("vendor"),
                    "selected_vendor_name": selected.get("vendor_name"),
                    "selected_model": model,
                    "selected_choice": selected.get("number"),
                    "provider_profile": infer_provider_profile(model),
                    "selection_reason": selection_reason,
                    "next_command_args": ["--choice", str(selected.get("number"))],
                    "reuse_for_task": {
                        "scope": "current_task",
                        "model": model,
                        "vendor": selected.get("vendor"),
                        "choice": selected.get("number"),
                        "flag": "--choice",
                    },
                }
            )
        else:
            result.update({"ok": True, "selection_reason": "catalog_list"})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except SystemExit as exc:
        result["error"] = {"category": "catalog_selection_error", "summary": str(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1


def cmd_capabilities(args: argparse.Namespace) -> int:
    models = [capability_for_model(model_id) for model_id in MODEL_CAPABILITIES]
    vendors = group_image_model_records(models, DEFAULT_MODEL, "any")
    print(
        json.dumps(
            {
                "default_model": DEFAULT_MODEL,
                "default_model_guard": DEFAULT_MODEL,
                "gpt_image_2_supported": True,
                "automatic_fallback": False,
                "provider_profiles": PROVIDER_PROFILE_VALUES,
                "executable_provider_profiles": list(EXECUTABLE_PROVIDER_PROFILES),
                "enabled_vendors": list(ENABLED_IMAGE_VENDORS),
                "enabled_model_families": list(ENABLED_IMAGE_FAMILIES),
                "routing_modes": ROUTING_MODE_VALUES,
                "models": models,
                "model_count": len(models),
                "vendor_count": len(vendors),
                "vendors": compact_vendor_groups(vendors),
                **selection_choice_bundle(vendors),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    validate_base_url(config["base_url"])
    config["provider_profile"] = resolve_provider_profile(config["model"], config["provider_profile_requested"])
    result: dict[str, Any] = {
        "ok": False,
        "source": "/v1/models",
        "catalog_path": config["catalog_path"],
        "configured_model": config["model"],
        "configured_model_input": config.get("model_requested"),
        "model_alias": config.get("model_alias"),
        "default_model": DEFAULT_MODEL,
        "gpt_image_2_supported": True,
        "enabled_vendors": list(ENABLED_IMAGE_VENDORS),
        "enabled_model_families": list(ENABLED_IMAGE_FAMILIES),
        "gpt_image_2_available": None,
        "default_model_guard": {
            "required": DEFAULT_MODEL,
            "available": None,
            "warning": False,
            "category": "not_checked",
        },
        "image_model_count": 0,
        "executable_image_model_count": 0,
        "discovery_only_image_model_count": 0,
        "vendor_count": 0,
        "models": [],
        "vendors": [],
    }
    if not config["api_key"]:
        result["error"] = {
            "category": "missing_api_key",
            "summary": "Missing API key. Set IMAGE_GENERATION_API_KEY, GPT_IMAGE_API_KEY, OPENAI_API_KEY, or pass --api-key.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    try:
        response = get_json(
            f"{config['base_url']}/models",
            config["api_key"],
            config["timeout"],
            config["user_agent"],
        )
        model_ids = extract_model_ids(response["body"])
        image_records, vendor_groups = classify_live_image_models(
            response["body"],
            config["model"],
            "generate",
            config.get("vendor_preference"),
        )
        executable_records, discovery_only_records = execution_inventory(image_records)
        if args.image_only:
            records = image_records
        else:
            image_ids = {record["id"] for record in image_records}
            records = list(image_records)
            for raw_record in extract_model_records(response["body"]):
                model_id = str(raw_record["id"])
                if model_id in image_ids:
                    continue
                record = capability_for_model(model_id, raw_record.get("metadata"))
                record["listed"] = True
                record["verification"] = "listed_only"
                record["image_model_candidate"] = False
                records.append(record)
        gpt_image_2_available = "gpt-image-2" in model_ids
        result.update(
            {
                "ok": bool(image_records),
                "status": response["status"],
                "total_model_count": len(model_ids),
                "returned_model_count": len(records),
                "image_model_count": len(image_records),
                "executable_image_model_count": len(executable_records),
                "discovery_only_image_model_count": len(discovery_only_records),
                "vendor_count": len(vendor_groups),
                "configured_model_available": config["model"] in model_ids,
                "gpt_image_2_available": gpt_image_2_available,
                "default_model_guard": {
                    "required": DEFAULT_MODEL,
                    "available": gpt_image_2_available,
                    "warning": not gpt_image_2_available,
                    "category": "ok" if gpt_image_2_available else "gpt_image_2_not_listed",
                },
                "models": records,
                "vendors": compact_vendor_groups(vendor_groups),
                **selection_choice_bundle(vendor_groups),
            }
        )
        if not gpt_image_2_available:
            result["gpt_image_2_status"] = {
                "category": "gpt_image_2_not_listed_on_current_gateway",
                "warning": True,
                "summary": "gpt-image-2 remains supported by the skill but is not listed by the current gateway.",
            }
        if image_records and not executable_records:
            result["execution_warning"] = {
                "category": "no_enabled_image_models",
                "warning": True,
                "summary": no_enabled_models_message(image_records),
            }
        if not image_records:
            result["error"] = {
                "category": "no_image_models_discovered",
                "summary": "The provider model list did not expose any recognizable image-generation models or image capability metadata.",
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        result["error"] = classify_error(exc.code, body, exc.reason)
    except error.URLError as exc:
        result["error"] = classify_error(None, "", str(exc.reason))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    config["provider_profile"] = resolve_provider_profile(config["model"], config["provider_profile_requested"])
    validate_base_url(config["base_url"])
    model_url = f"{config['base_url']}/models/{quote(config['model'], safe='')}"
    result: dict[str, Any] = {
        "ok": False,
        "base_url": config["base_url"],
        "catalog_path": config["catalog_path"],
        "model": config["model"],
        "default_model": DEFAULT_MODEL,
        "provider_profile": config["provider_profile"],
        "auth": mask_key(config["api_key"]),
        "timeout": config["timeout"],
        "retries": config["retries"],
        "retry_delay": config["retry_delay"],
        "user_agent": config["user_agent"],
        "model_check": None,
    }
    if not config["api_key"]:
        result["model_check"] = {
            "status": None,
            "category": "missing_api_key",
            "retryable": False,
            "summary": "Missing API key. Set IMAGE_GENERATION_API_KEY, GPT_IMAGE_API_KEY, OPENAI_API_KEY, or pass --api-key.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    try:
        response_payload = get_json(model_url, config["api_key"], config["timeout"], config["user_agent"])
        result["model_check"] = {
            "status": response_payload["status"],
            "category": "ok",
            "retryable": False,
            "summary": "Model endpoint is reachable.",
            "body_excerpt": json.dumps(response_payload["body"], ensure_ascii=False)[:1000],
        }
        result["ok"] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            models_url = f"{config['base_url']}/models"
            try:
                models_response = get_json(models_url, config["api_key"], config["timeout"], config["user_agent"])
                model_ids = extract_model_ids(models_response["body"])
                if config["model"] in model_ids:
                    result["ok"] = True
                    result["model_check"] = {
                        "status": models_response["status"],
                        "detail_status": 404,
                        "category": "ok_via_model_list",
                        "retryable": False,
                        "warning": False,
                        "summary": "The model is available in /v1/models; the provider simply does not expose the per-model endpoint.",
                        "available_model_count": len(model_ids),
                    }
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 0
                if model_ids:
                    result["model_check"] = {
                        "status": models_response["status"],
                        "detail_status": 404,
                        "category": "model_not_listed",
                        "retryable": False,
                        "warning": False,
                        "summary": "The configured image model was not present in the provider's /v1/models response.",
                        "available_model_count": len(model_ids),
                        "next_steps": [
                            "Check the exact image model id in the provider dashboard or documentation.",
                            "Update IMAGE_GENERATION_MODEL or pass --model before sending a paid generation request.",
                        ],
                    }
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 1
            except error.HTTPError as list_exc:
                list_body = list_exc.read().decode("utf-8", errors="replace")
                if list_exc.code != 404:
                    result["model_check"] = classify_error(list_exc.code, list_body, list_exc.reason)
                    result["model_check"]["detail_status"] = 404
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 1
            except error.URLError as list_exc:
                result["model_check"] = classify_error(None, "", str(list_exc.reason))
                result["model_check"]["detail_status"] = 404
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1

            result["ok"] = True
            result["model_check"] = {
                "status": 404,
                "category": "model_probe_not_supported",
                "retryable": False,
                "warning": True,
                "summary": "The provider exposes neither a usable per-model probe nor a usable model list. Image generation may still work.",
                "next_steps": [
                    "Run a generate --dry-run to verify the image request shape.",
                    "If dry-run is correct, use a low-cost --preset fast smoke generation to validate the provider.",
                    "If generation fails, use the structured generation error rather than this model probe result.",
                ],
                "body_excerpt": body.strip()[:1000],
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        result["model_check"] = classify_error(exc.code, body, exc.reason)
    except error.URLError as exc:
        result["model_check"] = classify_error(None, "", str(exc.reason))

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover image models and generate or edit with GPT Image and Grok Image adapters."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"image-generation-api {SCRIPT_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Call POST /v1/images/generations.")
    generate.add_argument("--prompt")
    generate.add_argument("--prompt-file", help="Read the full prompt from a UTF-8 text file.")
    generate.add_argument("--output")
    generate.add_argument("--api-key")
    generate.add_argument("--base-url")
    generate.add_argument("--user-agent", help="HTTP User-Agent. Overrides IMAGE_GENERATION_USER_AGENT or GPT_IMAGE_USER_AGENT.")
    generate.add_argument("--model", help="Exact provider model id or a supported shorthand alias.")
    generate.add_argument("--choice", help="Resolve a persisted catalog choice by number, exact id, or alias.")
    generate.add_argument("--catalog", help="Model catalog path. Defaults to %USERPROFILE%\\.codex\\image-generation-api-model-catalog.json.")
    generate.add_argument("--vendor", help="Vendor id/name used to filter the persisted catalog.")
    generate.add_argument("--provider-profile", choices=PROVIDER_PROFILE_VALUES)
    generate.add_argument("--mode", choices=ROUTING_MODE_VALUES)
    generate.add_argument("--env-file")
    generate.add_argument("--timeout", type=int)
    generate.add_argument("--retries", type=int)
    generate.add_argument("--retry-delay", type=float, dest="retry_delay", help="Base retry delay in seconds.")
    generate.add_argument("--preset", choices=sorted(PRESETS), help="Parameter preset. CLI flags override preset fields.")
    generate.add_argument("--size")
    generate.add_argument("--quality")
    generate.add_argument("--format", dest="output_format")
    generate.add_argument(
        "--size-policy",
        choices=SIZE_POLICY_VALUES,
        default=DEFAULT_SIZE_POLICY,
        help="Output dimension policy: normalize same-aspect mismatches, reject all mismatches, or accept provider dimensions.",
    )
    generate.add_argument("--compression", type=int)
    generate.add_argument("--background", choices=BACKGROUND_VALUES)
    generate.add_argument("--moderation", choices=MODERATION_VALUES)
    generate.add_argument("--response-format", choices=RESPONSE_FORMAT_VALUES)
    generate.add_argument("--aspect-ratio", choices=GROK_ASPECT_RATIO_VALUES)
    generate.add_argument("--resolution", choices=GROK_RESOLUTION_VALUES)
    generate.add_argument("--stream", action="store_true")
    generate.add_argument("--partial-images", type=int)
    generate.add_argument("--n", type=int, default=1)
    generate.add_argument("--user")
    generate.add_argument("--extra-json", action="append", default=[], help="Merge provider-specific JSON object fields into the request body. Repeatable.")
    generate.add_argument("--extra-json-file", action="append", default=[], help="Read provider-specific request fields from a JSON object file. Repeatable.")
    generate.add_argument("--save-request", help="Save the redacted request payload for debugging.")
    generate.add_argument("--save-response")
    generate.add_argument("--save-error", default="output/imagegen/error.json")
    generate.add_argument("--no-sidecar", action="store_true", help="Skip the automatic sidecar JSON record next to the output image.")
    generate.add_argument("--interactive", action="store_true", help="Resolve one model choice, then prompt for size, quality, format, and confirmation.")
    generate.add_argument("--dry-run", action="store_true")

    edit = subparsers.add_parser("edit", help="Call POST /v1/images/edits with multipart image uploads.")
    edit.add_argument("--prompt")
    edit.add_argument("--prompt-file", help="Read the full edit prompt from a UTF-8 text file.")
    edit.add_argument("--image", action="append", default=[], help="Input image path. Repeat for multiple references.")
    edit.add_argument("--mask", help="Optional mask image path.")
    edit.add_argument("--image-field", default="image[]", help="Multipart image field name. Default: image[].")
    edit.add_argument("--output")
    edit.add_argument("--api-key")
    edit.add_argument("--base-url")
    edit.add_argument("--user-agent", help="HTTP User-Agent. Overrides IMAGE_GENERATION_USER_AGENT or GPT_IMAGE_USER_AGENT.")
    edit.add_argument("--model", help="Exact provider model id or a supported shorthand alias.")
    edit.add_argument("--choice", help="Resolve a persisted catalog choice by number, exact id, or alias.")
    edit.add_argument("--catalog", help="Model catalog path. Defaults to %USERPROFILE%\\.codex\\image-generation-api-model-catalog.json.")
    edit.add_argument("--vendor", help="Vendor id/name used to filter the persisted catalog.")
    edit.add_argument("--provider-profile", choices=PROVIDER_PROFILE_VALUES)
    edit.add_argument("--mode", choices=ROUTING_MODE_VALUES)
    edit.add_argument("--env-file")
    edit.add_argument("--timeout", type=int)
    edit.add_argument("--retries", type=int)
    edit.add_argument("--retry-delay", type=float, dest="retry_delay")
    edit.add_argument("--preset", choices=sorted(PRESETS))
    edit.add_argument("--size")
    edit.add_argument("--quality")
    edit.add_argument("--format", dest="output_format")
    edit.add_argument(
        "--size-policy",
        choices=SIZE_POLICY_VALUES,
        default=DEFAULT_SIZE_POLICY,
        help="Output dimension policy: normalize same-aspect mismatches, reject all mismatches, or accept provider dimensions.",
    )
    edit.add_argument("--compression", type=int)
    edit.add_argument("--background", choices=BACKGROUND_VALUES)
    edit.add_argument("--moderation", choices=MODERATION_VALUES)
    edit.add_argument("--response-format", choices=RESPONSE_FORMAT_VALUES)
    edit.add_argument("--aspect-ratio", choices=GROK_ASPECT_RATIO_VALUES)
    edit.add_argument("--resolution", choices=GROK_RESOLUTION_VALUES)
    edit.add_argument("--stream", action="store_true")
    edit.add_argument("--partial-images", type=int)
    edit.add_argument("--n", type=int, default=1)
    edit.add_argument("--user")
    edit.add_argument("--extra-json", action="append", default=[], help="Merge provider-specific JSON object fields into multipart fields. Repeatable.")
    edit.add_argument("--extra-json-file", action="append", default=[], help="Read provider-specific multipart fields from a JSON object file. Repeatable.")
    edit.add_argument("--save-request", help="Save the redacted multipart request summary for debugging.")
    edit.add_argument("--save-response")
    edit.add_argument("--save-error", default="output/imagegen/error.json")
    edit.add_argument("--no-sidecar", action="store_true", help="Skip the automatic sidecar JSON record next to the output image.")
    edit.add_argument("--interactive", action="store_true", help="Resolve one model choice, then prompt for size, quality, format, and confirmation.")
    edit.add_argument("--dry-run", action="store_true")

    responses = subparsers.add_parser("responses", help="Call POST /v1/responses with the image_generation tool.")
    responses.add_argument("--input-text", action="append", default=[])
    responses.add_argument("--prompt")
    responses.add_argument("--prompt-file")
    responses.add_argument("--input-image", action="append", default=[])
    responses.add_argument("--mask")
    responses.add_argument("--output")
    responses.add_argument("--api-key")
    responses.add_argument("--base-url")
    responses.add_argument("--user-agent", help="HTTP User-Agent. Overrides IMAGE_GENERATION_USER_AGENT or GPT_IMAGE_USER_AGENT.")
    responses.add_argument("--model", dest="responses_model", help="Top-level text-capable Responses model.")
    responses.add_argument("--tool-model", help="Image model inside the image_generation tool.")
    responses.add_argument("--choice", help="Resolve a persisted catalog choice by number, exact id, or alias.")
    responses.add_argument("--catalog", help="Model catalog path. Defaults to %USERPROFILE%\\.codex\\image-generation-api-model-catalog.json.")
    responses.add_argument("--vendor", help="Vendor id/name used to filter the persisted catalog.")
    responses.add_argument("--tool-model-policy", choices=TOOL_MODEL_POLICY_VALUES)
    responses.add_argument("--provider-profile", choices=PROVIDER_PROFILE_VALUES)
    responses.add_argument("--mode", choices=ROUTING_MODE_VALUES)
    responses.add_argument("--env-file")
    responses.add_argument("--timeout", type=int)
    responses.add_argument("--retries", type=int)
    responses.add_argument("--retry-delay", type=float, dest="retry_delay")
    responses.add_argument("--preset", choices=sorted(PRESETS))
    responses.add_argument("--size")
    responses.add_argument("--quality")
    responses.add_argument("--format", dest="output_format")
    responses.add_argument("--compression", type=int)
    responses.add_argument("--background", choices=BACKGROUND_VALUES)
    responses.add_argument("--moderation", choices=MODERATION_VALUES)
    responses.add_argument("--response-format", choices=RESPONSE_FORMAT_VALUES)
    responses.add_argument("--aspect-ratio", choices=GROK_ASPECT_RATIO_VALUES)
    responses.add_argument("--resolution", choices=GROK_RESOLUTION_VALUES)
    responses.add_argument("--stream", action="store_true")
    responses.add_argument("--partial-images", type=int)
    responses.add_argument(
        "--size-policy",
        choices=SIZE_POLICY_VALUES,
        default=DEFAULT_SIZE_POLICY,
        help="Output dimension policy: normalize same-aspect mismatches, reject all mismatches, or accept provider dimensions.",
    )
    responses.add_argument("--action", choices=["auto", "generate", "edit"])
    responses.add_argument("--previous-response-id")
    responses.add_argument("--extra-json", action="append", default=[], help="Merge provider-specific JSON object fields into the top-level Responses request. Repeatable.")
    responses.add_argument("--extra-json-file", action="append", default=[], help="Read provider-specific top-level Responses fields from a JSON object file. Repeatable.")
    responses.add_argument("--tool-extra-json", action="append", default=[], help="Merge provider-specific JSON object fields into the image_generation tool. Repeatable.")
    responses.add_argument("--tool-extra-json-file", action="append", default=[], help="Read provider-specific image_generation tool fields from a JSON object file. Repeatable.")
    responses.add_argument("--save-request", help="Save the redacted Responses request payload for debugging.")
    responses.add_argument("--save-response")
    responses.add_argument("--save-error", default="output/imagegen/error.json")
    responses.add_argument("--no-sidecar", action="store_true", help="Skip the automatic sidecar JSON record next to the output image.")
    responses.add_argument("--interactive", action="store_true", help="Resolve one model choice, then prompt for size, quality, format, and confirmation.")
    responses.add_argument("--dry-run", action="store_true")

    configure = subparsers.add_parser(
        "configure",
        help="Fetch /v1/models once and persist the numbered GPT/Grok image model catalog.",
    )
    configure.add_argument("--api-key")
    configure.add_argument("--base-url")
    configure.add_argument("--user-agent", help="HTTP User-Agent. Overrides IMAGE_GENERATION_USER_AGENT or GPT_IMAGE_USER_AGENT.")
    configure.add_argument("--env-file")
    configure.add_argument("--timeout", type=int)
    configure.add_argument("--catalog", help="Output model catalog path. Defaults to %USERPROFILE%\\.codex\\image-generation-api-model-catalog.json.")

    classify = subparsers.add_parser("classify-error", help="Classify a saved HTTP error.")
    classify.add_argument("--status", required=True)
    classify.add_argument("--body", default="")
    classify.add_argument("--reason", default="")

    subparsers.add_parser("options", help="List supported presets and common parameter values.")

    subparsers.add_parser("capabilities", help="List the built-in image model capability registry without network access.")

    models = subparsers.add_parser("models", help="Read /v1/models and classify available image models without generating images.")
    models.add_argument("--api-key")
    models.add_argument("--base-url")
    models.add_argument("--catalog", help="Optional persisted catalog path to include in the report.")
    models.add_argument("--user-agent", help="HTTP User-Agent. Overrides IMAGE_GENERATION_USER_AGENT or GPT_IMAGE_USER_AGENT.")
    models.add_argument("--model")
    models.add_argument("--provider-profile", choices=PROVIDER_PROFILE_VALUES)
    models.add_argument("--env-file")
    models.add_argument("--timeout", type=int)
    models.add_argument("--retries", type=int)
    models.add_argument("--retry-delay", type=float, dest="retry_delay")
    models.add_argument("--image-only", action="store_true", help="Return only image-model candidates.")
    models.add_argument(
        "--allow-missing-gpt-image-2",
        action="store_true",
        help="Deprecated compatibility flag; missing gpt-image-2 is now a warning when other image models exist.",
    )

    select_model = subparsers.add_parser(
        "select-model",
        help="Read and resolve the fixed local model-number catalog without network access.",
    )
    select_model.add_argument("--api-key")
    select_model.add_argument("--base-url")
    select_model.add_argument("--catalog", help="Model catalog path. Defaults to %USERPROFILE%\\.codex\\image-generation-api-model-catalog.json.")
    select_model.add_argument("--user-agent", help="HTTP User-Agent. Overrides IMAGE_GENERATION_USER_AGENT or GPT_IMAGE_USER_AGENT.")
    select_model.add_argument("--model", help="Explicit provider model id or supported shorthand alias.")
    select_model.add_argument("--choice", help="Select one catalog entry by number, exact id, or alias.")
    select_model.add_argument("--vendor", help="Vendor id or display name used to filter the catalog.")
    select_model.add_argument("--operation", choices=["generate", "edit", "any"], default="generate")
    select_model.add_argument("--provider-profile", choices=PROVIDER_PROFILE_VALUES)
    select_model.add_argument("--env-file")
    select_model.add_argument("--timeout", type=int)
    select_model.add_argument("--retries", type=int)
    select_model.add_argument("--retry-delay", type=float, dest="retry_delay")
    select_model.add_argument(
        "--interactive",
        action="store_true",
        help="Show the catalog list and read one choice from the terminal.",
    )

    doctor = subparsers.add_parser("doctor", help="Validate configuration and model endpoint reachability without generating an image.")
    doctor.add_argument("--api-key")
    doctor.add_argument("--base-url")
    doctor.add_argument("--catalog", help="Optional persisted catalog path to include in the report.")
    doctor.add_argument("--user-agent", help="HTTP User-Agent. Overrides IMAGE_GENERATION_USER_AGENT or GPT_IMAGE_USER_AGENT.")
    doctor.add_argument("--model")
    doctor.add_argument("--provider-profile", choices=PROVIDER_PROFILE_VALUES)
    doctor.add_argument("--env-file")
    doctor.add_argument("--timeout", type=int)
    doctor.add_argument("--retries", type=int)
    doctor.add_argument("--retry-delay", type=float, dest="retry_delay")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.command == "generate":
        return cmd_generate(args)
    if args.command == "edit":
        return cmd_edit(args)
    if args.command == "responses":
        return cmd_responses(args)
    if args.command == "configure":
        return cmd_configure(args)
    if args.command == "classify-error":
        return cmd_classify_error(args)
    if args.command == "options":
        return cmd_options(args)
    if args.command == "capabilities":
        return cmd_capabilities(args)
    if args.command == "models":
        return cmd_models(args)
    if args.command == "select-model":
        return cmd_select_model(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
