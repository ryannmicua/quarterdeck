#!/usr/bin/env python3
"""Quarterdeck: a local page of the things the captain asked Firstmate to save by hand."""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import functools
import gzip
import hashlib
import html
import ipaddress
import json
import os
import re
import shlex
import signal
import socket
import ssl
import stat
import statistics
import subprocess
import sys
import tempfile
import threading
import unicodedata
import uuid
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


DEFAULT_TITLE = "Quarterdeck"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
ALLOWED_CONFIG_KEYS = {"page_title", "output_dir", "base_path", "firstmate_home"}
# Legacy configuration. Only a sole registered home can supply the note destination.
LEGACY_CONFIG_KEYS = {"homes", "captain_waits"}
# Split so the privacy guard, which rejects committed generated pages by this marker, does not flag the source.
GENERATED_MARKER = '<meta name="quarterdeck-' + 'generated" content="read-only review page">'

# The URL path prefix a reverse proxy serves Quarterdeck under ("" when it is served at the root).
BASE_PATH_RE = re.compile(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*")


def normalize_base_path(value: object) -> str:
    """Return the canonical prefix ("/qd"), or "" for none; raise ValueError when it is not a plain path."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("base path must be a string such as /qd")
    text = value.strip()
    if text.strip("/") == "":
        return ""
    text = text.rstrip("/")
    if not BASE_PATH_RE.fullmatch(text) or any(part in {".", ".."} for part in text.split("/")):
        raise ValueError(f"base path must start with / and use only letters, digits and - . _ ~ between slashes: {value!r}")
    return text


def strip_base_path(path: str, base: str) -> str | None:
    """The request path without the prefix, or None when a redirect is required."""
    if not base:
        return path
    if path == base:
        return None
    if path.startswith(base + "/"):
        return path[len(base):]
    return None


# --- Configuration and locations ---------------------------------------------------


def default_config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
    return (config_home / "quarterdeck.json").resolve()


def state_dir() -> Path:
    """Quarterdeck's own state directory; the item store and the default rendered page live here."""
    return Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser() / "quarterdeck"


def store_path() -> Path:
    return state_dir() / "items.json"


def inbox_path(value: object) -> Path:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("invalid inbox path")
    try:
        path = Path(value).expanduser()
        os.fsencode(path)
    except (RuntimeError, UnicodeEncodeError) as exc:
        raise ValueError("invalid inbox path") from exc
    return path


def load_config(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read config {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    unknown = sorted(set(config) - ALLOWED_CONFIG_KEYS - LEGACY_CONFIG_KEYS)
    if unknown:
        raise ValueError(f"unsupported config key(s): {', '.join(unknown)}")
    if "firstmate_home" in config:
        try:
            home = inbox_path(config["firstmate_home"])
        except ValueError as exc:
            raise ValueError("config firstmate_home must be a valid absolute path") from exc
        if not home.is_absolute():
            raise ValueError("config firstmate_home must be a valid absolute path")
        config["firstmate_home"] = str(home)
    if "page_title" in config and not isinstance(config["page_title"], str):
        raise ValueError("config page_title must be a string")
    if "base_path" in config:
        try:
            normalize_base_path(config["base_path"])
        except ValueError as exc:
            raise ValueError(f"config {exc}") from exc
    if "output_dir" in config and not isinstance(config["output_dir"], str):
        raise ValueError("config output_dir must be a path string")
    return config


def selected_config_path(args: argparse.Namespace) -> Path | None:
    """The --config file, else the default config when it exists, else no config."""
    path = getattr(args, "config", None)
    if path is None:
        default_path = default_config_path()
        path = default_path if default_path.is_file() else None
    return path.expanduser() if path else None


def selected_config(args: argparse.Namespace) -> dict[str, object]:
    return load_config(selected_config_path(args))


def page_title(args: argparse.Namespace, config: dict[str, object]) -> str:
    configured = config.get("page_title")
    return getattr(args, "title", None) or (configured if isinstance(configured, str) and configured.strip() else DEFAULT_TITLE)


def write_private(path: Path, content: str) -> None:
    """Write a file atomically with mode 0600, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_private_bytes(path: Path, data: bytes) -> None:
    """Write a new file with mode 0600; a name already taken is an error, never overwritten."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
            return
        except OSError as exc:
            if exc.errno not in {errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS, errno.EPERM}:
                raise
    finally:
        os.unlink(temporary)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


# --- The item store -----------------------------------------------------------------
CLASSIFICATION = {
    "kind": ("pick", "confirm", "rule", "wording", "scope", "approve", "inform"),
    "tag": ("product-design", "ui", "architecture", "business-rule", "terminology", "process", "bug",
             "security-privacy", "release", "research"),
    "via": ("chat", "review-page", "elsewhere", "superseded"),
}
DESK = "desk"
WORD_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
WORD_LIMIT = 30
TAG_LIMIT = 3
PROJECT_LIMIT = 40


def clean_word(value: object, field: str, *, where: str = "", stored: bool = False) -> str:
    name = f"{where} {field}" if where else field
    if not isinstance(value, str) or len(value) > WORD_LIMIT or not WORD_RE.fullmatch(value):
        raise ValueError(f"{name} must be one lowercase word (up to {WORD_LIMIT} letters, digits and single hyphens)")
    allowed = CLASSIFICATION[field]
    if not stored and value not in allowed:
        raise ValueError(f"unknown {name} {value!r}; allowed: {', '.join(allowed)}")
    return value


def clean_tags(raw: object, where: str, *, stored: bool = False) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError(f"{where} tags must be a list")
    tags = list(dict.fromkeys(clean_word(tag, "tag", where=where, stored=stored) for tag in raw))
    if len(tags) > TAG_LIMIT:
        raise ValueError(f"{where} can carry at most {TAG_LIMIT} tags")
    return tags


def clean_project(value: object) -> str:
    project = clean_text(value, "project", PROJECT_LIMIT)
    parts = project.split("/")
    if (len(parts) > 2 or (len(parts) == 2 and any(part in (".", "..") for part in parts))
            or not all(part and part == part.strip()
                       and all(character.isalnum() or character in " ._-" for character in part) for part in parts)):
        raise ValueError("project must be a plain name or owner/name (letters, digits, spaces, hyphens, dots, underscores)")
    return project


def item_classification(raw_project: object, raw_tags: object, *, stored: bool = False) -> dict[str, object]:
    """The project and tags to store on an item, left out when empty."""
    fields: dict[str, object] = {}
    if raw_project is not None:
        fields["project"] = clean_project(raw_project)
    tags = clean_tags(raw_tags if raw_tags is not None else [], "item", stored=stored)
    if tags:
        fields["tags"] = tags
    return fields


# Item records live in one JSON file. Every item was saved by hand through `quarterdeck add`; its body is stored by value
# at save time and never read again from anywhere else. An ID is never reused, even after its item is removed.

STORE_VERSION = 1
KIND_LABELS = {"resource": "Resource", "list": "List", "questions": "Questions", "conversation": "Conversation"}
KINDS = tuple(KIND_LABELS)
# A conversation is started from the page with Talk to Firstmate, never saved by hand.
ADD_KINDS = tuple(kind for kind in KINDS if kind != "conversation")
ID_RE = re.compile(r"[Qq][1-9][0-9]*")
ITEM_PAGE_RE = re.compile(r"q[1-9][0-9]*[.]html")
TITLE_LIMIT = 200
DESCRIPTION_LIMIT = 600
TEXT_LIMIT = 20_000


def empty_store() -> dict[str, object]:
    return {"version": STORE_VERSION, "next_id": 1, "items": []}


def parse_id(value: str) -> str:
    if not ID_RE.fullmatch(value):
        raise ValueError(f"not an item ID: {value!r} (IDs look like Q7)")
    return value.upper()


def clean_text(value: object, name: str, limit: int, *, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not multiline:
        text = " ".join(text.split())
    if not text:
        raise ValueError(f"{name} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{name} is longer than {limit} characters")
    if any(unicodedata.category(character) in {"Cc", "Cs"} and character not in "\n\t" for character in text):
        raise ValueError(f"{name} contains invalid characters")
    return text


def clean_url(value: object) -> str:
    text = clean_text(value, "url", 2000)
    message = "url must be an http:// or https:// address with a valid hostname and port"
    try:
        parsed = urlparse(text)
        host = unquote(parsed.hostname or "", errors="strict")
        port = parsed.port
        authority = parsed.netloc.rsplit("@", 1)[-1]
        if authority.startswith("["):
            address, _, suffix = authority.partition("]")
            ipaddress.IPv6Address(address[1:])
            if suffix and not suffix.startswith(":"):
                raise ValueError(message)
    except ValueError as exc:
        raise ValueError(message) from exc
    if (parsed.scheme not in {"http", "https"} or not host or port == 0 or
            any(character.isspace() or character == "\\" for character in text) or
            any(character.isspace() or character in "#%/<>?@[\\]^|"
                or unicodedata.category(character) in {"Cc", "Cs"} for character in host)):
        raise ValueError(message)
    return text


def list_entries(entries: object) -> list[str]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("a list needs at least one entry")
    return [clean_text(entry, f"list entry {number}", TEXT_LIMIT, multiline=True)
            for number, entry in enumerate(entries, 1)]


def question_links(raw: object, where: str) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise ValueError(f"{where} links must be a list of objects with text and url")
    links = []
    for number, link in enumerate(raw, 1):
        name = f"{where} link {number}"
        if not isinstance(link, dict) or set(link) != {"text", "url"}:
            raise ValueError(f"{name} must be an object with text and url")
        try:
            url = clean_url(link["url"])
        except ValueError as exc:
            raise ValueError(f"{name} {exc}") from exc
        links.append({"text": clean_text(link["text"], f"{name} text", TITLE_LIMIT), "url": url})
    return links


# Question images. A file named in a questions body is copied into the store's own images folder when the item is
# saved, under the item's ID, and is never read from its original path again.
IMAGE_BYTES_LIMIT = 5_000_000
IMAGE_COUNT_LIMIT = 24
IMAGE_TEXT_LIMIT = 300
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
IMAGE_FILE_RE = re.compile(r"[0-9a-f]{16}[.](?:" + "|".join(map(re.escape, IMAGE_TYPES)) + r")")
REPLY_IMAGES = "reply-images"
IMAGE_URL_RE = re.compile(rf"(images|{REPLY_IMAGES})/(q[1-9][0-9]*)/({IMAGE_FILE_RE.pattern})")


def images_root(store: Path, tree: str = "images") -> Path:
    return store.with_name(tree)


def image_chunks(data: bytes, *, webp: bool) -> Iterator[tuple[bytes, bytes]]:
    offset = 12 if webp else 8
    while offset + 8 <= len(data):
        header = data[offset:offset + 8]
        kind = header[:4] if webp else header[4:]
        size = int.from_bytes(header[4:] if webp else header[:4], "little" if webp else "big")
        end = offset + 8 + size
        next_offset = end + (size % 2 if webp else 4)
        if next_offset > len(data):
            break
        content = data[offset + 8:end]
        if not webp and zlib.crc32(content, zlib.crc32(kind)) != int.from_bytes(data[end:next_offset], "big"):
            raise ValueError("invalid PNG checksum")
        yield kind, content
        offset = next_offset
    if offset != len(data):
        raise ValueError("incomplete image chunk")


def jpeg_has_scan(data: bytes) -> bool:
    offset = 2
    frame = False
    while offset + 4 <= len(data) and data[offset] == 0xff:
        while offset + 1 < len(data) and data[offset + 1] == 0xff:
            offset += 1
        if offset + 4 > len(data):
            break
        marker = data[offset + 1]
        size = int.from_bytes(data[offset + 2:offset + 4], "big")
        end = offset + 2 + size
        if size < 2 or end > len(data) - 2:
            break
        if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
            frame = size >= 11 and any(data[offset + 5:offset + 7]) and any(data[offset + 7:offset + 9])
        if marker == 0xda:
            return frame and size >= 8 and end < len(data) - 2
        offset = end
    return False


def image_kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        chunks = image_chunks(data, webp=False)
        kind, header = next(chunks, (b"", b""))
        if kind != b"IHDR" or len(header) != 13 or not any(header[:4]) or not any(header[4:8]):
            return None
        has_pixels = False
        for kind, content in chunks:
            has_pixels |= kind == b"IDAT" and bool(content)
            if kind == b"IEND":
                return "png" if not content and has_pixels and next(chunks, None) is None else None
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9") and jpeg_has_scan(data):
        return "jpg"
    if (data[:4] == b"RIFF" and data[8:12] == b"WEBP"
            and int.from_bytes(data[4:8], "little") == len(data) - 8):
        has_pixels = False
        for kind, content in image_chunks(data, webp=True):
            has_pixels |= (
                (kind == b"VP8 " and len(content) > 10 and content[3:6] == b"\x9d\x01\x2a")
                or (kind == b"VP8L" and len(content) > 5 and content[0] == 0x2f)
            )
        if has_pixels:
            return "webp"
    return None


def open_nonblocking(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NONBLOCK)


def read_images(named: list[tuple[dict[str, str], str]], pending: dict[str, bytes]) -> None:
    """Snapshot the files a body names: resolve each path text once, then read each real file once."""
    real: dict[str, str] = {}
    for image, where in named:  # image["file"] holds the path text until it is read
        value = image["file"]
        if value not in real:
            try:
                real[value] = os.path.realpath(os.path.expanduser(value))
            except (OSError, ValueError) as exc:
                raise ValueError(f"{where} could not be read: {value!r} ({exc})") from exc
    files: dict[str, str] = {}
    for image, where in named:
        value = image["file"]
        if real[value] not in files:
            try:
                if not stat.S_ISREG(os.stat(real[value]).st_mode):
                    raise ValueError(f"{where} must be a regular file: {value!r}")
                with open(real[value], "rb", opener=open_nonblocking) as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError(f"{where} must be a regular file: {value!r}")
                    data = stream.read(IMAGE_BYTES_LIMIT + 1)
            except OSError as exc:
                raise ValueError(f"{where} could not be read: {value!r} ({exc})") from exc
            if len(data) > IMAGE_BYTES_LIMIT:
                raise ValueError(f"{where} is larger than {IMAGE_BYTES_LIMIT // 1_000_000} MB: {value!r}")
            try:
                kind = image_kind(data)
            except ValueError:
                kind = None
            if kind is None:
                raise ValueError(f"{where} must be a PNG, JPEG or still WebP image: {value!r}")
            filename = hashlib.sha256(data).hexdigest()[:16] + "." + kind
            files[real[value]] = filename
            pending[filename] = data
        image["file"] = files[real[value]]


def image_entry(raw: object, where: str, named: list[tuple[dict[str, str], str]] | None) -> dict[str, str]:
    """Check one image. With `named`, it is a body being saved (a `path`); without, it is stored (a `file`)."""
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object with a path and alt text")
    source = "path" if named is not None else "file"
    unknown = sorted(set(raw) - {source, "alt"})
    if unknown:
        raise ValueError(f"{where} has unsupported field(s): {', '.join(unknown)}")
    if "alt" not in raw:
        raise ValueError(f"{where} needs alt text that describes the image")
    cleaned: dict[str, str] = {}
    if named is not None:
        path = raw.get("path")
        if not isinstance(path, str):
            raise ValueError(f"{where} path must be text")
        if not path or "\0" in path or len(path) > 4000:
            raise ValueError(f"{where} has an invalid path")
        cleaned["file"] = path
        named.append((cleaned, where))
    else:
        if not isinstance(raw.get("file"), str) or not IMAGE_FILE_RE.fullmatch(raw["file"]):
            raise ValueError(f"{where} has an invalid file")
        cleaned["file"] = raw["file"]
    cleaned["alt"] = clean_text(raw["alt"], f"{where} alt text", IMAGE_TEXT_LIMIT)
    return cleaned


def option_label(option: object) -> str:
    return str(option["label"] if isinstance(option, dict) else option)  # type: ignore[index]


def item_image_files(item: dict[str, object], tree: str = "images") -> set[str]:
    files: set[str] = set()
    if tree == REPLY_IMAGES:
        for messages in item.get("conversation", {}).values():  # type: ignore[attr-defined]
            for message in messages:
                files.update(image["file"] for image in message.get("images", []))
    elif item["kind"] == "questions":
        for question in item["body"]["questions"]:  # type: ignore[index]
            files.update(image["file"] for image in question.get("images", []))
            files.update(option["image"]["file"] for option in question["options"] if isinstance(option, dict))
    return files


def question_entries(raw: object, pending: dict[str, bytes] | None = None, *,
                     stored: bool = False) -> list[dict[str, object]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions body must be a non-empty JSON array")
    image_count = 0
    questions = []
    named: list[tuple[dict[str, str], str]] = []
    for number, entry in enumerate(raw, 1):
        where = f"question {number}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        options, images = entry.get("options", []), entry.get("images", [])
        if not isinstance(options, list):
            raise ValueError(f"{where} options must be a list of text or objects with a label and image")
        if not isinstance(images, list):
            raise ValueError(f"{where} images must be a list")
        image_count += len(images)
        unknown = sorted(set(entry) - {"question", "context", "options", "suggestion", "links", "images", "kind", "tags"})
        if unknown:
            raise ValueError(f"{where} has unsupported field(s): {', '.join(unknown)}")
        choices: list[object] = []
        seen: set[str] = set()
        pictured: set[str] = set()
        for position, option in enumerate(options, 1):
            name = f"{where} option {position}"
            if not isinstance(option, dict):
                label = clean_text(option, name, TEXT_LIMIT)
                if label in pictured:
                    raise ValueError(f"{name} duplicates a pictured option label")
                seen.add(label)
                choices.append(label)
                continue
            unknown = sorted(set(option) - {"label", "image"})
            if unknown:
                raise ValueError(f"{name} has unsupported field(s): {', '.join(unknown)}")
            label = clean_text(option.get("label"), f"{name} label", TEXT_LIMIT)
            if label in seen:
                raise ValueError(f"{name} duplicates an option label")
            seen.add(label)
            pictured.add(label)
            choices.append({"label": label, "image": image_entry(option.get("image"), f"{name} image", named if pending is not None else None)})
            image_count += 1
        if image_count > IMAGE_COUNT_LIMIT:
            raise ValueError(f"an item can hold at most {IMAGE_COUNT_LIMIT} images")
        cleaned: dict[str, object] = {
            "question": clean_text(entry.get("question"), f"{where} question", TEXT_LIMIT, multiline=True),
            "context": clean_text(entry.get("context"), f"{where} context", TEXT_LIMIT, multiline=True),
            "options": choices,
        }
        if images:
            cleaned["images"] = [image_entry(image, f"{where} image {position}", named if pending is not None else None)
                                 for position, image in enumerate(images, 1)]
        if entry.get("suggestion") not in (None, ""):
            cleaned["suggestion"] = clean_text(entry["suggestion"], f"{where} suggestion", TEXT_LIMIT, multiline=True)
        links = question_links(entry.get("links", []), where)
        if links:
            cleaned["links"] = links
        if "kind" in entry:
            cleaned["kind"] = clean_word(entry["kind"], "kind", where=where, stored=stored)
        tags = clean_tags(entry.get("tags", []), where, stored=stored)
        if tags:
            cleaned["tags"] = tags
        if not cleaned["options"] and "suggestion" not in cleaned:
            raise ValueError(f"{where} needs options or a suggestion to decide from")
        questions.append(cleaned)
    if pending is not None:
        read_images(named, pending)
    return questions


def validated_body(kind: str, body: object, pending: dict[str, bytes] | None = None, *,
                   stored: bool = False) -> dict[str, object]:
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    if kind == "resource":
        return {"url": clean_url(body.get("url")), "link_text": clean_text(body.get("link_text"), "link text", TITLE_LIMIT)}
    if kind == "list":
        return {"entries": list_entries(body.get("entries"))}
    if kind == "conversation":
        if body:
            raise ValueError("a conversation has no body")
        return {}
    return {"questions": question_entries(body.get("questions"), pending, stored=stored)}


def make_body(kind: str, *, url: str | None, link_text: str | None, body: str | None,
              images: dict[str, bytes]) -> dict[str, object]:
    if kind == "resource":
        if url is None or link_text is None:
            raise ValueError("a resource needs --url and --link-text")
        if body is not None:
            raise ValueError("a resource takes --url and --link-text, not a body")
        return validated_body(kind, {"url": url, "link_text": link_text})
    if url is not None or link_text is not None:
        raise ValueError("--url and --link-text are only for a resource")
    if body is None:
        raise ValueError(f"a {kind} needs a body: --body-file PATH, or --body-file - to read standard input")
    if kind == "list":
        return validated_body(kind, {"entries": [line for line in body.splitlines() if line.strip()]})
    try:
        questions = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"questions body is not valid JSON: {exc}") from exc
    return validated_body(kind, {"questions": questions}, images)


def check_store(data: object) -> dict[str, object]:
    """Validate a store read from disk; raise ValueError when it is not one Quarterdeck wrote."""
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != STORE_VERSION:
        raise ValueError(f"the item store is not a version {STORE_VERSION} Quarterdeck store")
    items, next_id = data.get("items"), data.get("next_id")
    if not isinstance(items, list) or type(next_id) is not int or next_id < 1:
        raise ValueError("the item store is damaged: it lacks items or next_id")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or item.get("kind") not in KINDS or not isinstance(item.get("id"), str):
            raise ValueError("the item store is damaged: an item has no id or an unknown kind")
        item_id = item["id"]
        if parse_id(item_id) != item_id:
            raise ValueError(f"the item store is damaged: {item_id!r} is not canonical")
        if item_id in seen or int(item_id[1:]) >= next_id:
            raise ValueError(f"the item store is damaged: {item_id} is repeated or above next_id")
        seen.add(item_id)
        try:
            title = clean_text(item.get("title"), "title", TITLE_LIMIT)
            description = clean_text(item.get("description"), "description", DESCRIPTION_LIMIT, multiline=True)
            saved_at = item.get("saved_at")
            if not isinstance(saved_at, str):
                raise ValueError("saved_at must be a UTC timestamp")
            try:
                timestamp = datetime.strptime(saved_at, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError as exc:
                raise ValueError("saved_at must be a UTC timestamp") from exc
            if timestamp.isoformat() + "Z" != saved_at:
                raise ValueError("saved_at must be a UTC timestamp")
            body = validated_body(item["kind"], item.get("body"), stored=True)
            if (title, description, body) != (item["title"], item["description"], item["body"]):
                raise ValueError("item text or body is not in the saved format")
            if item_classification(item.get("project"), item.get("tags"), stored=True) != {
                    k: item[k] for k in ("project", "tags") if k in item}:
                raise ValueError("item project or tags are not in the saved format")
            if "answers" in item:
                validate_records(item)
            if "conversation" in item:
                validate_conversation(item)
            elif item["kind"] == "conversation":
                raise ValueError("a conversation needs its messages")
        except ValueError as exc:
            raise ValueError(f"the item store is damaged: {item_id}: {exc}") from exc
    return data


class StoreReadError(ValueError):
    pass


def read_store(path: Path) -> dict[str, object]:
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_store()
    except (OSError, UnicodeDecodeError) as exc:
        raise StoreReadError(f"could not read the item store {path}: {exc}") from exc
    try:
        data = json.loads(source)
    except (ValueError, RecursionError) as exc:
        raise StoreReadError(f"the item store {path} is not valid JSON: {exc}") from exc
    try:
        return check_store(data)
    except (ValueError, RecursionError) as exc:
        raise StoreReadError(str(exc)) from exc


@contextmanager
def locked_store(path: Path, *, read_only: bool = False) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    if read_only:
        try:
            lock = lock_path.open("rb")
        except OSError:
            yield
        else:
            with lock:
                fcntl.flock(lock, fcntl.LOCK_SH)
                yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write_store(path: Path, data: dict[str, object]) -> None:
    write_private(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


@contextmanager
def changing_store(path: Path) -> Iterator[dict[str, object]]:
    """Read the store under an exclusive lock, let the caller change it, then write it back atomically."""
    with locked_store(path):
        data = read_store(path)
        yield data
        write_store(path, data)


def find_item(data: dict[str, object], item_id: str) -> dict[str, object]:
    wanted = parse_id(item_id)
    for item in data["items"]:  # type: ignore[union-attr]
        if item["id"] == wanted:
            return item
    raise ValueError(f"no item {wanted}")


def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def saved_day(item: dict[str, object]) -> str:
    """The saved date as YYYY-MM-DD (UTC)."""
    return str(item["saved_at"])[:10]


def newest_first(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(items, key=lambda item: int(str(item["id"])[1:]), reverse=True)


# --- Item commands --------------------------------------------------------------------


def read_body(value: str | None) -> str | None:
    """The text of --body-file: a path, or "-" for standard input."""
    if value is None:
        return None
    if value == "-":
        return sys.stdin.read()
    try:
        return Path(value).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"could not read the body file {value}: {exc}") from exc


def question_parts(item):
    if item["kind"] != "questions":
        raise ValueError("Choose a questions item.")
    return range(1, len(item["body"]["questions"]) + 1)


def part_record(item, part):
    return item.get("answers", {}).get(str(part), {})


DESK_FIELDS = ("answer", "answered_at", "delivery", "request_id")


def answer_marks(record):
    if "answer_marks" in record:
        return record["answer_marks"]
    if "answered_at" in record:
        return [{"via": record.get("via", DESK), "answered_at": record["answered_at"]}]
    if record.get("state") == "answered":
        return [{"via": "elsewhere", "answered_at": None}]
    return []


def latest_off_desk_mark(record):
    return next((mark for mark in reversed(answer_marks(record)) if mark["via"] != DESK), None)


def delivery_pending(record):
    return record.get("delivery") == "pending" and record.get("state") != "closed"


def validate_records(item):
    parts = question_parts(item)
    answers = item["answers"]
    if not isinstance(answers, dict):
        raise ValueError("invalid answers")
    for part, record in answers.items():
        if not re.fullmatch(r"[1-9][0-9]*", part) or int(part) not in parts or not isinstance(record, dict):
            raise ValueError("invalid question record")
        if record.get("state") not in ("open", "answered", "closed") or not re.fullmatch(
                r"[0-9a-f]{32}", str(record.get("revision"))):
            raise ValueError("invalid question state")
        if any(k in record for k in (*DESK_FIELDS, "via")):
            via = record.get("via", DESK)
            if via != DESK:
                clean_word(via, "via", stored=True)
                if any(k in record for k in ("answer", "delivery", "request_id")):
                    raise ValueError("invalid saved answer")
            elif (clean_text(record.get("answer"), "answer", TEXT_LIMIT, multiline=True) != record["answer"]
                    or record.get("delivery") not in ("pending", "sent", "cancelled")
                    or not re.fullmatch(r"quarterdeck-[0-9a-f]{32}", str(record.get("request_id")))):
                raise ValueError("invalid saved answer")
            parse_utc(record.get("answered_at"))
        if "answer_marks" in record:
            marks = record["answer_marks"]
            if not isinstance(marks, list) or not marks:
                raise ValueError("invalid answer marks")
            for mark in marks:
                if not isinstance(mark, dict) or mark.keys() != {"via", "answered_at"}:
                    raise ValueError("invalid answer mark")
                clean_word(mark["via"], "via", stored=True)
                if mark["answered_at"] is not None or mark["via"] != "elsewhere":
                    parse_utc(mark["answered_at"])


# A question's conversation: the asks the owner sent and the replies Firstmate wrote, oldest first, kept apart from the
# answer so that a reply never changes what was decided.
AUTHOR_LABELS = {"you": "You", "firstmate": "Firstmate"}


def validate_conversation(item):
    parts = range(0) if item["kind"] == "conversation" else question_parts(item)
    conversation = item["conversation"]
    if not isinstance(conversation, dict) or not conversation:
        raise ValueError("invalid conversation")
    seen = set()
    for part, messages in conversation.items():
        if ((part != "item" and (not re.fullmatch(r"[1-9][0-9]*", part) or int(part) not in parts))
                or not isinstance(messages, list) or not messages):
            raise ValueError("invalid conversation")
        asks = set()
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError("invalid message")
            author = message.get("author")
            if not isinstance(author, str) or author not in AUTHOR_LABELS:
                raise ValueError("invalid message")
            you = author == "you"
            required = {"id", "author", "text", "at"} | ({"delivery", "request_id", "home"} if you else {"read", "ask"})
            if not required <= set(message) <= required | (set() if you else {"links", "images"}):
                raise ValueError("invalid message")
            if (not isinstance(message["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", message["id"])
                    or message["id"] in seen
                    or clean_text(message["text"], "message", TEXT_LIMIT, multiline=True) != message["text"]):
                raise ValueError("invalid message")
            seen.add(message["id"])
            datetime.strptime(str(message["at"]), "%Y-%m-%dT%H:%M:%SZ")
            if you:
                if (message["delivery"] not in ("pending", "sent")
                        or not isinstance(message["request_id"], str)
                        or not re.fullmatch(r"quarterdeck-[0-9a-f]{32}", message["request_id"])
                        or not isinstance(message["home"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", message["home"])):
                    raise ValueError("invalid ask")
                asks.add(message["id"])
                continue
            links, images = message.get("links", []), message.get("images", [])
            if (not isinstance(message["ask"], str) or message["ask"] not in asks
                    or type(message["read"]) is not bool or question_links(links, "reply") != links
                    or not isinstance(images, list)
                    or [image_entry(image, "reply image", None) for image in images] != images):
                raise ValueError("invalid reply")


def thread_key(part):
    return "item" if part is None else str(part)


def part_thread(item, part):
    return item.get("conversation", {}).get(thread_key(part), [])


def unread_reply(message):
    return message["author"] == "firstmate" and not message["read"]


def thread_unread(item, part):
    return any(map(unread_reply, part_thread(item, part)))


def part_state(item, part, *, replies=True):
    state = part_record(item, part).get("state", "open")
    return "replied" if replies and thread_unread(item, part) else state


def put_record(item, part, state, *, mark=None, **desk_fields):
    previous = part_record(item, part)
    if "answer" in previous and not desk_fields:
        desk_fields = {k: previous[k] for k in DESK_FIELDS}
    marks = answer_marks(previous)
    if mark is not None:
        marks = [*marks, mark]
    record = {**desk_fields, "state": state, "revision": uuid.uuid4().hex}
    if marks:
        record["answer_marks"] = marks
    item.setdefault("answers", {})[str(part)] = record
    return record


def part_numbers(item, target):
    """The question numbers a command names: one question (Q7.3) or all of an item (Q7)."""
    parts = question_parts(item)
    if "." not in target:
        return list(parts)
    _, part = parse_part(target.upper())
    if part not in parts:
        raise ValueError("Unknown question.")
    return [part]


def mark_item(args):
    def action():
        via = None
        if args.state == "answered":
            allowed = CLASSIFICATION["via"]
            if args.via is None:
                raise ValueError(f"marking answered needs --via, where the answer came from: {', '.join(allowed)}")
            via = clean_word(args.via, "via")
        elif args.via is not None:
            raise ValueError("--via goes with answered")
        item_id = args.id.partition(".")[0]
        messages = []
        changed = False
        store = store_path()
        with locked_store(store):
            data = read_store(store)
            item = find_item(data, item_id)
            for number in part_numbers(item, args.id):
                previous = part_record(item, number)
                if via:
                    state = previous.get("state", "open")
                    if state != "open":
                        messages.append(f"{item['id']}.{number} skipped (already {state})")
                        continue
                    record = put_record(item, number, "answered", mark={"via": via, "answered_at": now_text()})
                    if delivery_pending(record):
                        record["delivery"] = "cancelled"
                    messages.append(f"{item['id']}.{number} Answered via {via}")
                else:
                    put_record(item, number, args.state)
                changed = True
            if changed:
                write_store(store, data)
        if via:
            print("\n".join(messages))
        else:
            print(f"{args.id.upper()} {args.state.capitalize()}")
        return 0
    return run_item_command(action)


def classify_item(args):
    def action():
        if args.project is None and args.tags is None and args.kind is None:
            raise ValueError("give at least one of --project, --tag or --kind")
        item_id, dot, _ = args.id.partition(".")
        with changing_store(store_path()) as data:
            item = find_item(data, item_id)
            if dot:
                if args.project is not None:
                    raise ValueError("a project belongs to the whole item; classify " + item["id"] + " instead")
                number = part_numbers(item, args.id)[0]
                question = item["body"]["questions"][number - 1]
                if args.kind is not None:
                    question["kind"] = clean_word(args.kind, "kind")
                if args.tags is not None:
                    question["tags"] = clean_tags(args.tags, "question")
            else:
                if args.kind is not None:
                    raise ValueError("a kind belongs to one question; classify a question such as " + item["id"] + ".1")
                if args.project is not None:
                    item["project"] = clean_project(args.project)
                if args.tags is not None:
                    item["tags"] = clean_tags(args.tags, "item")
        print(f"{args.id.upper()} Classified")
        return 0
    return run_item_command(action)


class AnswerConflict(ValueError):
    pass


class SavedAskConflict(AnswerConflict):
    pass


def find_answer_item(data, item_id):
    try:
        return find_item(data, item_id)
    except ValueError as exc:
        if int(item_id[1:]) < data["next_id"]:
            raise AnswerConflict("Item removed. Reload before sending.") from exc
        raise


def deliver_note(home, request_id, text):
    """Hand one note to the home's inbox command; True when it accepted the note."""
    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith("FM_")}
        result = subprocess.run([home / "bin/fm-inbox.sh", "note", "--request-id", request_id, "--json", "--", text],
                                cwd=home, stdin=subprocess.DEVNULL, timeout=10,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**env, "FM_HOME": str(home)})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def send_answer(store, home, payload):
    if not isinstance(payload, dict) or set(payload) != {"part", "answer", "revision"}:
        raise ValueError("Invalid answer fields.")
    if not isinstance(payload["revision"], str) or not re.fullmatch(r"(?:[0-9a-f]{32})?", payload["revision"]):
        raise ValueError("Invalid question revision.")
    item_id, part = parse_part(payload["part"])
    answer = clean_text(payload["answer"], "Answer", TEXT_LIMIT, multiline=True)
    with changing_store(store) as data:
        item = find_answer_item(data, item_id)
        if part not in question_parts(item):
            raise ValueError("Unknown question.")
        old = part_record(item, part)
        state = old.get("state")
        if state == "closed":
            raise AnswerConflict("Question closed. Reopen it before sending.")
        same = old.get("answer") == answer
        repeat = same and state == "answered" and old.get("delivery") != "cancelled"
        if not repeat and payload["revision"] != old.get("revision", ""):
            raise AnswerConflict("Question changed. Reload before sending.")
        if same:
            record = old
        else:
            at = now_text()
            record = put_record(item, part, "answered", mark={"via": DESK, "answered_at": at}, answer=answer, answered_at=at,
                                delivery="pending", request_id="quarterdeck-" + uuid.uuid4().hex)
    # The first transaction commits before the external call. Serialize delivery against edits and reuse its note ID on retries.
    if home and delivery_pending(record):
        with changing_store(store) as data:
            record_now = part_record(find_answer_item(data, item_id), part)
            if record_now["revision"] != record["revision"]:
                raise AnswerConflict("Question changed. Reload before sending.")
            record = record_now
            if delivery_pending(record) and deliver_note(home, record["request_id"], f"{item_id}.{part} {record['answer']}"):
                record["delivery"] = "sent"
    return {**{k: record[k] for k in ("state", "revision", "answer", "answered_at", "delivery")},
            "off_desk_answer": latest_off_desk_mark(record)}


def parse_part(value):
    match = re.fullmatch(r"(Q[1-9][0-9]*)[.]([1-9][0-9]*)", str(value))
    if not match:
        raise ValueError("Unknown question.")
    return match[1], int(match[2])


def parse_thread_target(value):
    if isinstance(value, str) and value == value.upper() and ID_RE.fullmatch(value):
        return value, None
    return parse_part(value)


def check_conversation_target(item, part, target):
    if item["kind"] == "conversation":
        if part is not None:
            raise ValueError(f"no question {target}")
        return
    parts = question_parts(item)
    if part is not None and part not in parts:
        raise ValueError(f"no question {target}")


def conversation_editable(item, part):
    if part is None:
        return item["kind"] == "conversation" or has_answer_controls(item)
    return part_record(item, part).get("state") != "closed"


def ask_result(item, part, message, *, include_thread):
    noun = "Message" if item["kind"] == "conversation" else "Ask"
    result = {"message": f"{noun} sent." if message["delivery"] == "sent" else f"{noun} saved. Not delivered."}
    if include_thread:
        result["thread"] = thread_html(item, part, editable=conversation_editable(item, part))
    return result


def new_ask(message_id, text, binding):
    return {"id": message_id, "author": "you", "text": text, "at": now_text(), "delivery": "pending",
            "request_id": "quarterdeck-" + uuid.uuid4().hex, "home": binding}


def ask_note(item, target, message):
    """The inbox note: a card ask names its question; a conversation message names the item and its topic."""
    if item["kind"] == "conversation":
        return f"CONVERSATION {item['id']} {message['id']} Topic: {item['title']}\n{message['text']}"
    return f"ASK {target} {message['id']} {message['text']}"


def conversation_home_matches(item, binding):
    return item["kind"] != "conversation" or part_thread(item, None)[0]["home"] == binding


def ask_message(store, home, binding, payload, *, include_thread=True):
    if not isinstance(payload, dict):
        raise ValueError("Invalid ask fields.")
    fields = set(payload)
    target = payload.get("part")
    item_id, part = parse_thread_target(target)
    key = thread_key(part)
    message_id = payload.get("id")
    if not isinstance(message_id, str) or not re.fullmatch(r"[0-9a-f]{32}", message_id):
        raise ValueError("Invalid ask ID.")
    changed_home = "Firstmate home changed. This ask belongs to the original home."
    create_fields = {"part", "id", "text", "home"}
    retry = payload.get("action") == "retry"
    if fields == create_fields or (retry and fields == create_fields | {"action"}):
        text = clean_text(payload["text"], "Ask", TEXT_LIMIT, multiline=True)
        with locked_store(store):
            data = read_store(store)
            item = find_answer_item(data, item_id)
            check_conversation_target(item, part, target)
            home_changed = payload["home"] != binding or not conversation_home_matches(item, binding)
            for number, messages in item.get("conversation", {}).items():
                message = next((m for m in messages if m["id"] == message_id), None)
                if message is None:
                    continue
                if number != key or message["author"] != "you" or message["text"] != text:
                    raise AnswerConflict("Ask ID already in use. Reload the question.")
                if home_changed or (message["delivery"] == "pending" and message["home"] != binding):
                    raise SavedAskConflict(changed_home)
                if not retry:
                    return ask_result(item, part, message, include_thread=include_thread)
                break
            else:
                if home_changed:
                    raise AnswerConflict(changed_home)
                if not conversation_editable(item, part):
                    raise AnswerConflict("Question closed. Reopen it before asking.")
                thread = item.setdefault("conversation", {}).setdefault(key, [])
                thread.append(new_ask(message_id, text, binding))
                write_store(store, data)
    elif not retry or fields != {"part", "id", "action"}:
        raise ValueError("Invalid ask fields.")
    # The ask is saved before the external call, and delivery runs under the lock so a retry reuses the same note ID.
    with changing_store(store) as data:
        item = find_answer_item(data, item_id)
        check_conversation_target(item, part, target)
        message = next((m for m in part_thread(item, part) if m["id"] == message_id and m["author"] == "you"), None)
        if message is None:
            raise AnswerConflict("Ask not found. Reload the question.")
        if not conversation_home_matches(item, binding):
            raise SavedAskConflict(changed_home)
        if message["delivery"] == "pending":
            if message["home"] != binding:
                raise SavedAskConflict(changed_home)
            if not conversation_editable(item, part):
                raise SavedAskConflict("Question closed. Reopen it before sending the ask.")
            if deliver_note(home, message["request_id"], ask_note(item, target, message)):
                message["delivery"] = "sent"
        return ask_result(item, part, message, include_thread=include_thread)


def start_conversation(store, home, binding, payload):
    """Save a new conversation item holding its first message, then deliver that message like any ask."""
    if not isinstance(payload, dict) or set(payload) != {"id", "title", "text", "home"}:
        raise ValueError("Invalid conversation fields.")
    message_id = payload["id"]
    if not isinstance(message_id, str) or not re.fullmatch(r"[0-9a-f]{32}", message_id):
        raise ValueError("Invalid message ID.")
    title = clean_text(payload["title"], "Topic", TITLE_LIMIT)
    text = clean_text(payload["text"], "Message", TEXT_LIMIT, multiline=True)
    with locked_store(store):
        data = read_store(store)
        for item in data["items"]:
            first = item.get("conversation", {}).get("item", [None])[0] if item["kind"] == "conversation" else None
            if first and first["id"] == message_id:
                if (item["title"], first["text"]) != (title, text):
                    raise AnswerConflict("Message ID already in use. Reload the Desk.")
                if payload["home"] != binding or first["home"] != binding:
                    raise SavedAskConflict("Firstmate home changed. This message belongs to the original home.")
                item_id = item["id"]
                break
        else:
            if payload["home"] != binding:
                raise AnswerConflict("Firstmate home changed. This message belongs to the original home.")
            item_id = f"Q{data['next_id']}"
            data["next_id"] = int(data["next_id"]) + 1
            data["items"].append({"id": item_id, "kind": "conversation", "title": title,
                                  "description": "Conversation with Firstmate.", "saved_at": now_text(), "body": {},
                                  "conversation": {"item": [new_ask(message_id, text, binding)]}})
            write_store(store, data)
    result = ask_message(store, home, binding, {"part": item_id, "id": message_id, "action": "retry"},
                         include_thread=False)
    return {**result, "href": item_page_name(item_id)}


def mark_replies_read(store, payload):
    if not isinstance(payload, dict) or set(payload) != {"part", "ids"}:
        raise ValueError("Invalid reply acknowledgment.")
    item_id, part = parse_thread_target(payload["part"])
    ids = payload["ids"]
    if not isinstance(ids, list) or not ids or not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)
                                                     for value in ids):
        raise ValueError("Invalid reply IDs.")
    with changing_store(store) as data:
        item = find_answer_item(data, item_id)
        check_conversation_target(item, part, payload["part"])
        replies = {m["id"]: m for m in part_thread(item, part) if m["author"] == "firstmate"}
        if not replies.keys() >= set(ids):
            raise ValueError("Unknown reply on this question.")
        for message_id in ids:
            replies[message_id]["read"] = True
        return {"replies": {message_id: reply["read"] for message_id, reply in replies.items()}}


def item_text(item: dict[str, object]) -> str:
    """One item as plain text, numbered the way the page numbers it."""
    item_id, body = str(item["id"]), item["body"]
    lines = [f"{item_id} · {item['title']}", f"{KIND_LABELS[str(item['kind'])]} · saved {saved_day(item)}",
             "", str(item["description"]), ""]
    if item.get("project"):
        lines.insert(2, f"Project: {item['project']}")
    if item.get("tags"):
        lines.insert(3 if item.get("project") else 2, "Tags: " + ", ".join(item["tags"]))
    if item["kind"] == "resource":
        lines.append(f"{body['link_text']}: {body['url']}")  # type: ignore[index]
    elif item["kind"] == "list":
        lines.extend(f"{item_id}.{number}  {entry}" for number, entry in enumerate(body["entries"], 1))  # type: ignore[index]
    elif item["kind"] == "conversation":
        lines = lines[:-2]
        for message in part_thread(item, None):
            who = AUTHOR_LABELS[message["author"]]
            label = f"{who} ({delivery_label(message).lower()})" if message["author"] == "you" else who
            lines.append(f"{message['at']}  {label}  {message['id']}")
            lines.extend("  " + line for line in message["text"].splitlines())
            lines.extend(f"  Link: {link['text']}: {link['url']}" for link in message.get("links", []))
            lines.extend(f"  Image: {image['alt']}: {image_url(item, image, REPLY_IMAGES)}"
                         for image in message.get("images", []))
            lines.append("")
    else:
        state, open_parts, _ = question_summary(item, replies=False)
        remaining = len(open_parts)
        lines.extend([f"{state} ({remaining} of {len(question_parts(item))} open)" if remaining else state, ""])
        for number, question in enumerate(body["questions"], 1):  # type: ignore[index]
            lines.append(f"{item_id}.{number}  {question['question']}")
            if question.get("kind"):
                lines.append(f"      Kind: {question['kind']}")
            if question.get("tags"):
                lines.append("      Tags: " + ", ".join(question["tags"]))
            lines.append(f"      Context: {question['context']}")
            for link in question.get("links", []):
                lines.append(f"      Link: {link['text']}: {link['url']}")
            if question["options"]:
                lines.append("      Options: " + " | ".join(option_label(option) for option in question["options"]))
            if question.get("suggestion"):
                lines.append(f"      Suggestion: {question['suggestion']}")
            record = part_record(item, number)
            lines.append("      " + record.get("state", "open").capitalize())
            if "answer" in record:
                lines.append(f"      Answer: {record['answer']} ({record['answered_at']}, {delivery_label(record)})")
            if mark := latest_off_desk_mark(record):
                at = f" ({mark['answered_at']})" if mark["answered_at"] else ""
                lines.append(f"      Answered elsewhere: {mark['via']}{at}")
    return "\n".join(lines).rstrip() + "\n"


def run_item_command(action) -> int:  # noqa: ANN001 - a callable returning the exit code
    try:
        return action()
    except ValueError as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"quarterdeck: could not update the item store: {exc}", file=sys.stderr)
        return 2


def image_directory(root: Path, item_id: str) -> Path:
    directory = root / item_id.lower()
    for path in (root, directory):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError(f"refusing to use an unrelated image path: {path}")
    return directory


def stored_image(store: Path, item_id: str, file: str, tree: str = "images") -> Path:
    path = image_directory(images_root(store, tree), item_id) / file
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"the image {file} of {item_id} is missing from the store")
    return path


def place_image(target: Path, data: bytes) -> None:
    """Create an image file. An identical file already there is kept; any other occupant is an error."""
    if not target.is_symlink() and not target.exists():
        try:
            write_private_bytes(target, data)
            return
        except FileExistsError:
            pass
    if (target.is_symlink() or not target.is_file() or target.stat().st_size != len(data)
            or target.read_bytes() != data):
        raise ValueError(f"refusing to overwrite an unrelated file: {target}")


def save_images(store: Path, item_id: str, images: dict[str, bytes], *, tree: str = "images") -> None:
    if not images:
        return
    root = images_root(store, tree)
    if tree == REPLY_IMAGES and root.is_file():
        raise ValueError(f"Move the file {root} before attaching reply images.")
    directory = image_directory(root, item_id)
    directory.parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory.parent, 0o700)
    directory.mkdir(mode=0o700, exist_ok=True)
    for name, data in images.items():
        place_image(directory / name, data)


def remove_images(store: Path, item: dict[str, object]) -> None:
    """Delete exactly the files this item's record names."""
    for tree in ("images", REPLY_IMAGES):
        files = item_image_files(item, tree)
        if files:
            directory = image_directory(images_root(store, tree), str(item["id"]))
            for name in files:
                (directory / name).unlink(missing_ok=True)


def add_item(args: argparse.Namespace) -> int:
    def action() -> int:
        images: dict[str, bytes] = {}
        body = make_body(args.kind, url=args.url, link_text=args.link_text, body=read_body(args.body_file), images=images)
        item: dict[str, object] = {
            "kind": args.kind, "title": clean_text(args.title, "title", TITLE_LIMIT),
            "description": clean_text(args.description, "description", DESCRIPTION_LIMIT, multiline=True),
            "saved_at": now_text(), **item_classification(args.project, args.tags), "body": body,
        }
        store = store_path()
        with locked_store(store):
            data = read_store(store)
            item_id = f"Q{data['next_id']}"
            data["next_id"] = int(data["next_id"]) + 1  # type: ignore[call-overload]
            data["items"].append({"id": item_id, **item})  # type: ignore[union-attr]
            save_images(store, item_id, images)
            write_store(store, data)
        print(item_id)
        return 0
    return run_item_command(action)


def list_items(_args: argparse.Namespace) -> int:
    def action() -> int:
        items = newest_first(read_store(store_path())["items"])  # type: ignore[arg-type]
        if not items:
            print("No saved items.")
        for item in items:
            print(f"{item['id']:<6} {item['kind']:<10} {saved_day(item)}  {item['title']}")
        return 0
    return run_item_command(action)


def show_item(args: argparse.Namespace) -> int:
    def action() -> int:
        item = find_item(read_store(store_path()), args.id)
        print(item_text(item), end="")
        return 0
    return run_item_command(action)


def remove_item(args: argparse.Namespace) -> int:
    def action() -> int:
        store = store_path()
        with changing_store(store) as data:
            item = find_item(data, args.id)
            data["items"] = [other for other in data["items"] if other is not item]  # type: ignore[union-attr]
        try:
            remove_images(store, item)
        except (OSError, ValueError) as exc:
            raise ValueError(f"removed {item['id']} from the store but could not delete its images: {exc}") from exc
        print(f"Removed {item['id']}; its ID is not reused")
        return 0
    return run_item_command(action)


# --- Report -------------------------------------------------------------------------------
# Counts questions by kind, tag and project. A question's tags are its own plus its item's.
# It reads only the store and never decides whether anything was answered elsewhere.

UNCLASSIFIED = "(unclassified)"


def parse_utc(text: object) -> datetime:
    return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def question_facts(data: dict[str, object], since: str | None) -> list[dict[str, object]]:
    facts = []
    for item in data["items"]:  # type: ignore[union-attr]
        if item["kind"] != "questions":
            continue
        saved_at = parse_utc(item["saved_at"])
        saved = since is None or saved_day(item) >= since
        for number, question in enumerate(item["body"]["questions"], 1):
            record = part_record(item, number)
            state = record.get("state", "open")
            sources, desk_waits = set(), []
            for mark in answer_marks(record):
                timestamp = mark["answered_at"]
                if timestamp is None:
                    if since is None:
                        sources.add(mark["via"])
                    continue
                at = parse_utc(timestamp)
                if since is not None and at.date().isoformat() < since:
                    continue
                sources.add(mark["via"])
                if mark["via"] == DESK:
                    desk_waits.append(max(0, int((at - saved_at).total_seconds())))
            if saved or sources:
                facts.append({
                    "kind": question.get("kind"), "project": item.get("project"), "state": state,
                    "tags": list(dict.fromkeys([*question.get("tags", []), *item.get("tags", [])])),
                    "saved": saved, "sources": sources, "desk_waits": desk_waits,
                })
    return facts


def summarize(facts: list[dict[str, object]]) -> dict[str, object]:
    desk_waits = [wait for fact in facts for wait in fact["desk_waits"]]
    return {
        "saved": sum(1 for fact in facts if fact["saved"]),
        "answered_desk": sum(1 for fact in facts if DESK in fact["sources"]),
        "answered_elsewhere": sum(1 for fact in facts if fact["sources"] - {DESK}),
        "via": {value: sum(1 for fact in facts if value in fact["sources"]) for value in CLASSIFICATION["via"]},
        "open": sum(1 for fact in facts if fact["state"] == "open"),
        "closed": sum(1 for fact in facts if fact["state"] == "closed"),
        "median_wait_desk_seconds": statistics.median(desk_waits) if desk_waits else None,
    }


def build_report(data: dict[str, object], since: str | None) -> dict[str, object]:
    facts = question_facts(data, since)
    groups: dict[str, dict[str, list[dict[str, object]]]] = {"by_kind": {}, "by_tag": {}, "by_project": {}}
    for fact in facts:
        groups["by_kind"].setdefault(fact["kind"] or UNCLASSIFIED, []).append(fact)
        groups["by_project"].setdefault(fact["project"] or UNCLASSIFIED, []).append(fact)
        for tag in fact["tags"] or [UNCLASSIFIED]:  # type: ignore[union-attr]
            groups["by_tag"].setdefault(tag, []).append(fact)
    report: dict[str, object] = {"since": since, "via_values": list(CLASSIFICATION["via"]), "total": summarize(facts)}
    for name, group in groups.items():
        report[name] = {key: summarize(group[key]) for key in sorted(group, key=lambda k: (k == UNCLASSIFIED, k))}
    return report


def wait_text(seconds: object) -> str:
    if seconds is None:
        return "none"
    minutes = int(seconds) // 60  # type: ignore[call-overload]
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def report_table(title: str, rows: dict[str, dict[str, object]]) -> list[str]:
    heads = [title, "Saved", "Desk", *(value.replace("-", " ").capitalize() for value in CLASSIFICATION["via"]),
             "Open", "Closed", "Median Desk wait"]
    table = [heads] + [[label, *(str(v) for v in (r["saved"], r["answered_desk"], *r["via"].values(),  # type: ignore[union-attr]
                                                  r["open"], r["closed"])), wait_text(r["median_wait_desk_seconds"])] for label, r in rows.items()]
    widths = [max(len(row[column]) for row in table) for column in range(len(heads))]
    return ["  ".join(cell.ljust(widths[0]) if column == 0 else cell.rjust(widths[column])
                      for column, cell in enumerate(row)).rstrip() for row in table]


def report_text(report: dict[str, object]) -> str:
    since = report["since"]
    lines = [f"Question activity since {since}" if since else "All saved questions"]
    if not report["by_kind"]:
        return lines[0] + "\nNo questions saved.\n"
    for title, key in (("All", "total"), ("Kind", "by_kind"), ("Tag", "by_tag"), ("Project", "by_project")):
        rows = {"All questions": report[key]} if key == "total" else report[key]  # type: ignore[dict-item]
        lines += ["", *report_table(title, rows)]  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


def show_stats(args: argparse.Namespace) -> int:
    def action() -> int:
        since = None
        if args.since is not None:
            try:
                since = datetime.strptime(args.since, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("--since must be a date such as 2026-11-01") from exc
        store = store_path()
        with locked_store(store, read_only=True):
            data = read_store(store)
        report = build_report(data, since)
        print(json.dumps(report, indent=2) if args.as_json else report_text(report), end="" if not args.as_json else "\n")
        return 0
    return run_item_command(action)


def reply_item(args: argparse.Namespace) -> int:
    def action() -> int:
        target = args.id.upper()
        try:
            item_id, part = parse_thread_target(target)
        except ValueError as exc:
            raise ValueError(f"reply to a conversation item, a question item or one of its questions, such as Q7 or Q7.1: {args.id!r}") from exc
        text = clean_text(args.text, "reply text", TEXT_LIMIT, multiline=True)
        link_texts, urls = args.link_text or [], args.url or []
        if len(link_texts) != len(urls):
            raise ValueError("each --link-text needs its own --url")
        links = question_links([{"text": t, "url": u} for t, u in zip(link_texts, urls)], "reply")
        paths, alts = args.image or [], args.alt or []
        if len(paths) != len(alts):
            raise ValueError("each --image needs its own --alt text that describes it")
        named: list[tuple[dict[str, str], str]] = []
        images = [image_entry({"path": path, "alt": alt}, f"reply image {number}", named)
                  for number, (path, alt) in enumerate(zip(paths, alts), 1)]
        pending: dict[str, bytes] = {}
        read_images(named, pending)
        store = store_path()
        with locked_store(store):
            data = read_store(store)
            item = find_item(data, item_id)
            check_conversation_target(item, part, target)
            thread = part_thread(item, part)
            asks = {m["id"] for m in thread if m["author"] == "you"}
            noun = "message" if item["kind"] == "conversation" else "ask"
            if not asks:
                raise ValueError(f"{target} has no {noun}s")
            ask = args.ask
            if ask not in asks:
                raise ValueError(f"No {noun} {ask!r} on {target}.")
            message: dict[str, object] = {"id": uuid.uuid4().hex, "author": "firstmate", "text": text, "at": now_text(),
                                          "read": False, "ask": ask}
            if links:
                message["links"] = links
            if images:
                message["images"] = images
            thread.append(message)
            save_images(store, item_id, pending, tree=REPLY_IMAGES)
            write_store(store, data)
        print(f"{target} Replied")
        return 0
    return run_item_command(action)


# --- Workers ------------------------------------------------------------------------------
# Roster refresh runs only the home's read-only fleet snapshot, without a model.
# Quarterdeck shows only what the snapshot states outright and keeps no copy.

SNAPSHOT_COMMAND = ("bin/fm-fleet-snapshot.sh", "--local")
SNAPSHOT_SECONDS = 60
UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
WORKER_STATES = {"working": "Working", "done": "Finished", "blocked": "Stuck", "failed": "Stuck"}
ACTIVITY_UNAVAILABLE = "Activity unavailable"
snapshot_lock = threading.Lock()


class SnapshotError(Exception):
    pass


def run_snapshot(home: Path) -> object:
    """Run the home's read-only fleet snapshot and return its parsed output."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("FM_")}
    command = [str(home / SNAPSHOT_COMMAND[0]), *SNAPSHOT_COMMAND[1:]]
    try:
        process = subprocess.Popen(command, cwd=home, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env={**env, "FM_HOME": str(home)}, start_new_session=True)
    except OSError as exc:
        raise SnapshotError("the fleet snapshot could not start") from exc
    try:
        output, _ = process.communicate(timeout=SNAPSHOT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise SnapshotError("the fleet snapshot took too long") from exc
    if process.returncode != 0:
        raise SnapshotError("the fleet snapshot failed")
    try:
        return json.loads(output)
    except (ValueError, RecursionError) as exc:
        raise SnapshotError("the fleet snapshot was not readable") from exc


def snapshot_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join("".join(c if unicodedata.category(c) not in {"Cc", "Cs"} else " " for c in value).split())
    return text or None


def utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").removesuffix("+00:00") + "Z"


def snapshot_field(record: dict, name: str, expected: type | tuple[type, ...]):
    value = record.get(name)
    if value is not None and not isinstance(value, expected):
        raise SnapshotError(f"the fleet snapshot has an invalid {name}")
    return value


def worker_status(task: dict) -> str:
    """Map only what the snapshot states outright onto the owner's status words."""
    hints = snapshot_field(task, "hints", dict) or {}
    decisions = snapshot_field(hints, "open_decisions", list) or []
    if any(not isinstance(decision, dict) or not isinstance(decision.get("key"), str) or not decision["key"].strip()
           for decision in decisions):
        raise SnapshotError("the fleet snapshot has an invalid open decision")
    if decisions:
        return "Question asked"
    current = snapshot_field(task, "current_state", dict) or {}
    if snapshot_field(current, "freshness", str) not in {None, "fresh"}:
        return "Unknown"
    return WORKER_STATES.get(snapshot_field(current, "state", str), "Unknown")


def roster_row(key: str, task: dict, observed: datetime) -> dict[str, object]:
    backlog = snapshot_field(task, "backlog", dict) or {}
    project = snapshot_field(task, "project", str)
    title = snapshot_field(backlog, "title", str)
    pr = snapshot_field(task, "pr", dict) or {}
    url = snapshot_field(pr, "url", str)
    links = []
    if url is not None:
        try:
            links.append({"text": "Pull request", "href": clean_url(url)})
        except ValueError as exc:
            raise SnapshotError("the fleet snapshot has an invalid pull request URL") from exc
    row: dict[str, object] = {
        "name": key,
        "project": (snapshot_text(Path(project).name if project else None)
                    or snapshot_text(snapshot_field(backlog, "repo", str))),
        "activity": snapshot_text(title) or ACTIVITY_UNAVAILABLE, "links": links,
    }
    if task["kind"] == "secondmate":
        endpoint = task["endpoint"]
        closed = endpoint.get("exists") is False
        alive = None if closed else snapshot_field(endpoint, "agent_alive", str)
        row["status"] = "Unreachable" if closed or alive == "dead" else worker_status(task)
        row["contact_at"] = utc_text(observed) if alive == "alive" else None
    else:
        row["status"] = worker_status(task)
        paths = snapshot_field(task, "paths", dict) or {}
        status_log = snapshot_field(paths, "status_log", dict) or {}
        last = snapshot_field(status_log, "last_event", dict) or {}
        age = snapshot_field(last, "age_seconds", (int, float))
        if age is not None and (isinstance(age, bool) or not 0 <= age < float("inf")):
            raise SnapshotError("the fleet snapshot has an invalid activity age")
        row["active_at"] = None
        if age is not None:
            with contextlib.suppress(OverflowError):
                row["active_at"] = utc_text(observed - timedelta(seconds=age))
    return row


def workers_snapshot(home: Path) -> dict[str, object]:
    """Every worker with a live terminal screen, plus the second mate roster, as the page shows them."""
    if not snapshot_lock.acquire(blocking=False):
        raise SnapshotError("the fleet snapshot is already running")
    try:
        data = run_snapshot(home)
    finally:
        snapshot_lock.release()
    tasks = data.get("tasks") if isinstance(data, dict) else None
    generated = data.get("generated") if isinstance(data, dict) else None
    if not isinstance(tasks, list) or not isinstance(generated, str) or not UTC_RE.fullmatch(generated):
        raise SnapshotError("the fleet snapshot was not in the expected shape")
    try:
        observed = datetime.strptime(generated, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SnapshotError("the fleet snapshot timestamp was invalid") from exc
    workers, second_mates = {}, {}
    unresolved_workers, unresolved_mates = set(), set()
    for task in tasks:
        if not isinstance(task, dict):
            raise SnapshotError("the fleet snapshot has an invalid task")
        kind, endpoint = task.get("kind"), task.get("endpoint")
        exists = endpoint.get("exists") if isinstance(endpoint, dict) else None
        if not isinstance(kind, str) or not kind.strip():
            exists = None
        elif kind != "secondmate" and exists is False:
            continue
        key = task.get("id")
        if not isinstance(key, str) or not key.strip() or any(unicodedata.category(c) in {"Cc", "Cs"} for c in key):
            raise SnapshotError("the fleet snapshot has an invalid task")
        if kind == "secondmate":
            if not isinstance(endpoint, dict) or (exists is not None and not isinstance(exists, bool)):
                unresolved_mates.add(key)
            else:
                second_mates.setdefault(key, task)
        elif exists is True:
            workers.setdefault(key, task)
        else:
            unresolved_workers.add(key)
    if unresolved_mates - second_mates.keys() or unresolved_workers - workers.keys() - second_mates.keys():
        raise SnapshotError("the fleet snapshot has no reliable terminal state")
    return {
        "observed_at": generated,
        "workers": [roster_row(key, task, observed) for key, task in workers.items() if key not in second_mates],
        "second_mates": [roster_row(key, task, observed) for key, task in second_mates.items()],
    }


# --- Pages ------------------------------------------------------------------------------
# The workers roster is fetched separately from the HTML. No page names a server path.

PAGE_CSS = """
/* Tokens: the only colours, type sizes and widths any Quarterdeck page uses. */
:root { color-scheme: light dark;
  --ground: #f6f4ef; --raised: #fbfaf7; --ink: #1a2129; --soft: #565f68; --rule: #d6d0c4; --faint: #e9e5dc;
  --signal: #a8321c; --signal-ground: #f6e6e1; --focus: #1d5c87;
  --serif: Charter, "Bitstream Charter", "Iowan Old Style", "Sitka Text", Cambria, "Noto Serif", Georgia, serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --text-xs: .8rem; --text-sm: .9rem; --text-code: .9em; --text-lead: 1.2rem; --text-md: 1rem; --text-body: 18px; --text-lg: 1.3rem; --text-xl: 1.6rem;
  --text-title: clamp(1.75rem, 1.4rem + 1.6vw, 2.4rem);
  --gutter: 1rem; --page: 40rem; --measure: 75ch; --side: 18rem; --tap: 48px; }
@media (prefers-color-scheme: dark) {
  :root { --ground: #131619; --raised: #1a1e22; --ink: #ebe7df; --soft: #a3a9af; --rule: #363c42; --faint: #23282d;
    --signal: #ff8c6e; --signal-ground: #3a211b; --focus: #8ec5ec; }
}
@media (max-width: 30rem) { :root { --text-body: 17px; } }
@media (width > 40rem) { :root { --gutter: 2rem; --page: 64rem; } }
@media (min-width: 90rem) { :root { --gutter: 3rem; --page: 150rem; --side: 20rem; } }

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--ground); color: var(--ink); font: var(--text-body)/1.55 var(--serif); overflow-wrap: anywhere; }
a { color: inherit; text-decoration-thickness: 1px; text-underline-offset: .18em; }
a:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.bar { display: flex; gap: 1rem; align-items: baseline; justify-content: space-between; padding: .9rem var(--gutter);
  border-bottom: 1px solid var(--rule); font-family: var(--sans); }
.bar .mark { font: 700 var(--text-md)/1.2 var(--serif); text-decoration: none; }
.bar nav a { font-size: var(--text-sm); margin-left: 1rem; }
main { max-width: var(--page); margin: 0 auto; padding: 1.5rem var(--gutter) 4rem; }
h1 { font-size: var(--text-title); line-height: 1.15; margin: 0 0 .4rem; }
h2 { font: 700 var(--text-lg)/1.3 var(--sans); margin: 2rem 0 .6rem; }
.meta, .empty, .stamp { color: var(--soft); font: var(--text-sm)/1.5 var(--sans); }
.empty { font-size: var(--text-md); margin: 2rem 0; }
.lead { font-size: var(--text-lead); max-width: var(--measure); margin: .8rem 0 1.4rem; }
.id { font: 700 var(--text-sm)/1.4 var(--mono); white-space: nowrap; }
.open-link { display: inline-block; min-height: var(--tap); padding: .7rem 1.1rem; border: 2px solid var(--ink); border-radius: 6px;
  font: 700 var(--text-md)/1.3 var(--sans); text-decoration: none; }
ol.numbered { list-style: none; padding: 0; margin: 0; max-width: var(--measure); }
ol.numbered > li { display: grid; grid-template-columns: 4.5rem 1fr; gap: .2rem .8rem; padding: .8rem 0; border-bottom: 1px solid var(--rule); }
ol.numbered > li > .id { padding-top: .2rem; }
.question p { margin: 0 0 .5rem; }
.question .ask { font-weight: 700; }
.question .label { font: 600 var(--text-sm)/1.4 var(--sans); color: var(--soft); }
.question ul { margin: .1rem 0 .5rem; padding-left: 1.2rem; }
main { max-width: 68rem; }
h3 { margin: .3rem 0; font-size: 1.55rem; line-height: 1.25; }
.eyebrow,.actions,.sendbar,label,button,output,.progress { font-family: var(--sans); }
.eyebrow,.actions { display:flex; flex-wrap:wrap; gap:.6rem 1rem; align-items:center; }
.eyebrow { color:var(--soft); }
.work,.decision { background:var(--raised); padding:clamp(1rem,3vw,2rem); border:1px solid var(--rule);
  border-radius:8px; margin:1rem 0; max-width:52rem; }
.work { border-left:5px solid var(--signal); }
.work-action { background:var(--ink); color:var(--raised); padding:.55rem 1rem; border-radius:5px; text-decoration:none; }
.decision h2 { font:700 1.45rem/1.25 var(--serif); margin:.75rem 0 .3rem; }
.decision p,.work p { margin:.5rem 0 1rem; }
.badge,.progress { font:600 1rem/1.5 var(--sans); color:var(--signal); }
.progress { margin-bottom:.4rem; }
button { cursor:pointer; background:var(--raised); color:var(--ink); border:1px solid var(--soft);
  border-radius:5px; padding:.5rem .85rem; min-height:44px; font-size:1rem; max-width:100%; overflow-wrap:anywhere; }
button.primary { color:var(--raised); background:var(--ink); }
button:disabled, button[aria-disabled="true"] { opacity:.55; cursor:default; }
:focus-visible { outline:3px solid var(--focus); outline-offset:2px; }
.decision,button,textarea { scroll-margin-top:2rem; }
output { display:block; margin:.4rem 0; }
.choices { display:flex; flex-wrap:wrap; gap:.5rem; margin-bottom:.8rem; }
.choice { display:inline-flex; gap:.4em; align-items:baseline; flex-wrap:wrap; padding:.5rem .75rem; min-height:44px; border:1px solid var(--soft); border-radius:5px; cursor:pointer; font:1rem/1.5 var(--sans); }
.choice input { accent-color:var(--signal); }
.choice small { font-size:.9rem; color:var(--signal); }
.choice:has(:checked) { outline:2px solid var(--signal); background:var(--signal-ground); }
label { display:block; font-weight:600; }
textarea { display:block; width:100%; resize:vertical; background:var(--ground); color:var(--ink);
 border:1px solid var(--soft); border-radius:4px; padding:.55rem; font:inherit; }
.record { white-space:pre-wrap; }
.ref-links { list-style:none; margin:0 0 .8rem; padding:0; font-family:var(--sans); }
.ref-links li { margin:0; }
.ref-link { display:inline-block; padding:.6rem 0; min-height:44px; box-sizing:border-box; font-size:1.1rem; font-weight:600; text-decoration:underline; color:var(--ink); }
.visual { margin:.2rem 0 1rem; font-family:var(--sans); }
.decision img,.thread img { display:block; width:100%; height:auto; max-height:30rem; object-fit:contain; background:var(--faint);
  border:1px solid var(--rule); border-radius:4px; }
.shots,.has-images { list-style:none; margin:.2rem 0 1rem; padding:0; display:grid; gap:1rem;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,13rem),1fr)); align-items:start; }
.shots li,.option { margin:0; min-width:0; }
.shot { display:block; }
.option { display:flex; flex-direction:column; gap:.4rem; }
.choice.has-image { display:flex; flex-direction:column; align-items:stretch; gap:.6rem; flex:1; }
.choice .pick { display:flex; gap:.4em; align-items:baseline; flex-wrap:wrap; }
.open-image { align-self:flex-start; display:inline-flex; align-items:center; min-height:44px; font:var(--text-sm)/1.3 var(--sans); }
.ask-row { margin:.6rem 0 0; }
.thread { margin:1.2rem 0 0; font-family:var(--sans); }
.thread summary { font:700 var(--text-md)/1.3 var(--sans); margin:0 0 .5rem; min-height:var(--tap); padding:.5rem 0; cursor:pointer; }
.exchange { list-style:none; margin:0 0 .8rem; padding:0; }
.msg { margin:0 0 .6rem; padding:.7rem .9rem; border:1px solid var(--rule); border-left:4px solid var(--soft); border-radius:6px;
  background:var(--ground); max-width:46rem; }
.msg.firstmate { background:var(--raised); border-left-color:var(--focus); }
.msg .who { display:flex; flex-wrap:wrap; gap:.2rem .8rem; align-items:baseline; margin:0 0 .3rem; color:var(--soft); font-size:var(--text-md); }
.msg .who strong { color:var(--ink); }
.msg .record { margin:0; font-family:var(--serif); }
.msg .ref-links, .msg .shots { margin-top:.5rem; }
.msg .actions { margin-top:.5rem; }
.desk { margin:0 0 1rem; }
.desk .work { max-width:none; margin:0; padding:1rem; border-width:0 0 1px; border-left:5px solid var(--rule); border-radius:0; background:transparent;
  display:grid; grid-template-columns:minmax(0,1fr) auto; gap:0 1.5rem; align-items:center; }
.desk .work.next { border-left-color:var(--signal); background:var(--signal-ground); }
.desk .work > .eyebrow, .desk .work > h3, .desk .work > p, .desk .work > ul { grid-column:1; }
.desk .work > p { margin:.2rem 0 0; }
.desk .work > ul { margin:.4rem 0 0; padding-inline-start:1.25rem; }
.desk .work > .actions { grid-column:2; grid-row:1 / span 4; justify-content:flex-end; }
.button-link, .work-action { display:inline-flex; align-items:center; min-height:44px; padding:.5rem .85rem; border-radius:5px; font:600 var(--text-md)/1.3 var(--sans);
  text-decoration:none; border:1px solid var(--soft); background:var(--raised); color:var(--ink); }
.work-action { background:var(--ink); color:var(--raised); border-color:var(--ink); }
.section-head { display:flex; flex-wrap:wrap; gap:.5rem 1rem; align-items:center; justify-content:space-between; margin-top:2rem; }
.section-head h2 { margin:0; }
.roster { list-style:none; margin:.5rem 0 0; padding:0; border-top:1px solid var(--rule); font-family:var(--sans); }
.roster > li { padding:.9rem 0; border-bottom:1px solid var(--rule); }
.roster p { margin:.15rem 0 0; }
.roster .head { display:flex; flex-wrap:wrap; gap:.3rem 1rem; align-items:baseline; justify-content:space-between; }
.roster .who { font:700 var(--text-lead)/1.3 var(--sans); }
.roster .project, .roster .when { color:var(--soft); font-size:var(--text-md); }
.roster .doing { font-size:var(--text-md); }
.roster .links { display:flex; flex-wrap:wrap; gap:.3rem 1rem; }
.status { font:600 var(--text-md)/1.3 var(--sans); padding:.15rem .6rem; border:1px solid var(--rule); border-radius:5px; white-space:nowrap; }
.status.exception { color:var(--signal); background:var(--signal-ground); border-color:var(--signal); }
.failed { color:var(--signal); }
#workers h3 { font:700 var(--text-md)/1.3 var(--sans); margin:1.4rem 0 0; }
.tags { display:flex; flex-wrap:wrap; gap:.3rem .5rem; list-style:none; margin:.35rem 0 0; padding:0; font-family:var(--sans); }
.tags li { font-size:var(--text-sm); color:var(--soft); border:1px solid var(--rule); border-radius:5px; padding:.05rem .5rem; }
.desk .work > .tags { grid-column:1; padding-inline-start:0; margin:.5rem 0 0; }
.project-name { color:var(--soft); font-size:var(--text-sm); font-weight:400; }
.group-name { font:700 var(--text-md)/1.3 var(--sans); margin:1.4rem 0 .3rem; color:var(--soft); }
.filters { display:flex; flex-wrap:wrap; gap:.6rem 1.2rem; align-items:end; margin:.6rem 0; font-family:var(--sans); }
.filters label { display:flex; flex-direction:column; gap:.2rem; min-width:0; max-width:100%; font-size:var(--text-sm); color:var(--soft); }
.filters select { min-height:var(--tap); min-width:min(100%,12rem); max-width:100%; font:var(--text-md)/1.3 var(--sans); padding:.4rem .6rem;
  background:var(--raised); color:var(--ink); border:1px solid var(--soft); border-radius:5px; }
.saved-answer .record:empty { display:none; }
@media (max-width: 40rem) {
  .desk .work { grid-template-columns:minmax(0,1fr); }
  .desk .work > .actions { grid-column:1; grid-row:auto; justify-content:flex-start; margin-top:.6rem; }
}
[hidden] { display:none !important; }
.bar nav { display:flex; flex-wrap:wrap; gap:.4rem 1rem; align-items:center; }
.bar nav a { margin-left:0; }
.bar button { min-height:40px; font-size:var(--text-sm); }
.desk .work { position:relative; cursor:pointer; }
.desk .work:hover { background:var(--faint); }
.desk .work.next:hover { background:var(--signal-ground); box-shadow:inset 0 0 0 2px var(--signal); }
.desk .work h3 { margin:.3rem 0; }
.card-link::after { content:""; position:absolute; inset:0; z-index:0; }
.card-link:focus { outline:none; }
.desk .work:has(.card-link:focus) { outline:3px solid var(--focus); outline-offset:-3px; }
.desk .work a:not(.card-link), .desk .work button { position:relative; z-index:1; }
html:has(.sendbar) { --sendbar-space:calc(var(--sendbar-height, 0px) + var(--visible-bottom, 0px)); scroll-padding-bottom:var(--sendbar-space); }
main:has(> .sendbar) { padding-bottom:var(--sendbar-space); }
.sendbar { position:fixed; inset:auto 0 var(--visible-bottom, 0px); z-index:2;
  max-height:calc(var(--visible-height, 100vh) / 2); overflow:auto;
  padding:.7rem max(var(--gutter), calc((100% - var(--page)) / 2 + var(--gutter))) calc(.7rem + env(safe-area-inset-bottom));
  background:var(--ground); border-top:1px solid var(--rule); box-shadow:0 -6px 14px -10px var(--ink); }
.sendbar output { margin:.4rem 0 0; max-height:min(8rem, calc(var(--visible-height, 100vh) / 4)); overflow:auto; }
.sendbar output:empty { display:none; }
details.done { margin:.6rem 0; max-width:52rem; border:1px solid var(--rule); border-radius:8px; background:var(--raised); }
details.done > summary { display:grid; grid-template-columns:auto minmax(0, 7fr) minmax(0, 5fr); gap:.8rem; align-items:baseline;
  padding:.7rem 1rem; min-height:var(--tap); cursor:pointer; font-family:var(--sans); }
details.done > summary:focus-visible { outline:3px solid var(--focus); outline-offset:-3px; }
details.done > summary:has(.sum-source:not([hidden])) {
  grid-template-columns:auto minmax(0, 7fr) minmax(min(50%, 7em), 5fr); }
details.done .sum-q, details.done .sum-text { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
details.done .sum-q { font-weight:700; }
details.done .sum-a { display:flex; color:var(--soft); }
details.done .sum-source { flex:none; white-space:pre; }
details.done > .decision { margin:0; border:0; border-top:1px solid var(--rule); border-radius:0 0 8px 8px; max-width:none; }
.library { position:fixed; inset-block:auto var(--visible-bottom, 0px); padding:0; border:0; background:var(--raised); color:var(--ink);
  font-family:var(--sans); max-width:none; max-height:none; overflow:hidden;
  width:100%; height:min(calc(var(--visible-height, 100vh) * .88), calc(var(--visible-height, 100vh) - 2.5rem));
  margin:0; border-radius:14px 14px 0 0; border-top:1px solid var(--rule); }
.library::backdrop { background:color-mix(in srgb, var(--ink) 55%, transparent); }
.library[open] { display:flex; flex-direction:column; }
@media (width > 40rem) {
  .library { width:min(36rem, 100vw); height:var(--visible-height, 100vh); margin:0 0 0 auto;
    border-radius:0; border-top:0; border-left:1px solid var(--rule); }
}
@media (prefers-reduced-motion: no-preference) {
  .library[open] { animation:lib-in .18s ease-out; }
  @keyframes lib-in { from { transform:translateY(1.5rem); opacity:0; } }
  @media (width > 40rem) { @keyframes lib-in { from { transform:translateX(2rem); opacity:0; } } }
}
body:has(dialog[open]) { overflow:hidden; }
.lib-top { display:flex; flex-wrap:wrap; gap:.4rem 1rem; align-items:center; justify-content:space-between; padding:.7rem var(--gutter) 0; border-bottom:1px solid var(--rule); }
[role="tablist"] { display:flex; gap:.2rem; }
[role="tab"] { background:transparent; border:0; border-bottom:4px solid transparent; border-radius:0; padding:.5rem .6rem; min-height:var(--tap);
  font:600 var(--text-lg)/1.2 var(--sans); color:var(--soft); white-space:nowrap; }
[role="tab"][aria-selected="true"] { color:var(--ink); border-bottom-color:var(--signal); }
.lib-top > button { margin-bottom:.4rem; }
@media (max-width: 40rem) {
  .lib-top { gap:.4rem; }
  [role="tab"] { padding:.5rem .45rem; font-size:var(--text-sm); }
  [role="tablist"] { gap:0; }
  .lib-top > button { padding:.5rem .7rem; flex:none; }
}
@media (max-width: 24rem) {
  [role="tab"] { padding:.5rem .25rem; font-size:.8rem; }
}
.lib-panel { display:flex; flex-direction:column; flex:1; min-height:0; }
.lib-top, .lib-tools, .lib-status { flex-shrink:0; }
.lib-tools { padding:.9rem var(--gutter) .4rem; }
.lib-tools label { font-size:var(--text-sm); color:var(--soft); margin-bottom:.2rem; }
.lib-tools input { width:100%; min-height:var(--tap); padding:.4rem .6rem; font:var(--text-md)/1.3 var(--sans); color:var(--ink); background:var(--ground);
  border:1px solid var(--soft); border-radius:5px; }
.lib-status { margin:0; padding:0 var(--gutter); color:var(--soft); font-size:var(--text-sm); }
.lib-panel > .empty { margin:1rem var(--gutter); }
.lib-list { list-style:none; margin:0; padding:0 var(--gutter) 2rem; overflow-y:auto; flex:1; min-height:0; overscroll-behavior:contain; }
.library.short[open] { height:var(--visible-height, 100vh); }
.library:is(.short,.scroll-all)[open] { display:block; overflow-y:auto; overscroll-behavior:contain; }
.library:is(.short,.scroll-all) .lib-list { overflow:visible; }
.library:is(.short,.scroll-all) button { scroll-margin-top:0; }
.lib-item { padding:.9rem 0; border-bottom:1px solid var(--rule); }
.lib-item p { margin:.2rem 0 0; }
.lib-head { display:flex; gap:.2rem .8rem; align-items:baseline; }
.lib-title { font:700 var(--text-lg)/1.3 var(--sans); }
.lib-desc { font-size:var(--text-md); color:var(--soft); }
.lib-item .meta { display:flex; flex-wrap:wrap; gap:0 1rem; font-size:var(--text-md); }
.page-section { margin:2rem 0 0; }
.page-head { display:flex; flex-wrap:wrap; gap:.6rem 1.5rem; align-items:center; justify-content:space-between; max-width:52rem; }
.page-head h1 { margin:0; }
.talk, .composer { max-width:52rem; margin:1rem 0; padding:clamp(1rem,3vw,1.5rem); background:var(--raised); border:1px solid var(--rule);
  border-radius:8px; font-family:var(--sans); }
.talk[hidden] { display:none; }
.talk label, .composer label { margin:.8rem 0 .2rem; }
.talk label:first-child, .composer label:first-child { margin-top:0; }
.talk input { display:block; width:100%; min-height:var(--tap); background:var(--ground); color:var(--ink); border:1px solid var(--soft);
  border-radius:4px; padding:.55rem; font:inherit; }
.talk p[id], .composer p[id] { margin:.4rem 0 0; color:var(--signal); font-weight:600; }
.talk .actions, .composer .actions { margin-top:.8rem; }
.conversation .composer { margin:1rem 0 0; padding:1rem 0 0; background:none; border:0; border-top:1px solid var(--rule); border-radius:0; max-width:none; }
.conversation .exchange { margin:0; }
"""


TIME_SCRIPT = """
const localTime = dateTime => new Date(dateTime).toLocaleString(undefined, {dateStyle:'medium', timeStyle:'short'});
const localizeTimes = root => {
  for (const time of root.querySelectorAll('.saved-answer time[datetime]:not([datetime=""]), .answer-source time, .thread time[datetime], .conversation time[datetime]')) {
    time.textContent = localTime(time.dateTime);
  }
};
localizeTimes(document);
for (const time of document.querySelectorAll('time.day[datetime]:not([datetime=""])')) {
  time.textContent = new Date(time.dateTime).toLocaleDateString(undefined, {dateStyle:'medium'});
}
"""


WORKERS_SCRIPT = r"""
(() => {
  const section = document.querySelector('#workers');
  const button = section && section.querySelector('#refresh-workers');
  if (!button) return;
  const heading = section.querySelector('h2');
  const status = section.querySelector('#workers-status');
  const list = section.querySelector('#workers-list');
  const exceptions = new Set(['Question asked', 'Stuck', 'Unreachable']);
  let lastObservedAt = null;
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const stamped = (label, iso) => {
    const line = make('p', 'when', label + ' ');
    const time = make('time', '', localTime(iso));
    time.dateTime = iso;
    line.append(time);
    return line;
  };
  const row = (worker, mate) => {
    const item = make('li');
    const head = make('div', 'head');
    head.append(make('strong', 'who', worker.name),
      make('span', exceptions.has(worker.status) ? 'status exception' : 'status', worker.status));
    item.append(head);
    if (worker.project) item.append(make('p', 'project', worker.project));
    item.append(make('p', 'doing', worker.activity));
    if (mate) item.append(worker.contact_at ? stamped('Reached', worker.contact_at) : make('p', 'when', 'Contact unavailable'));
    else if (worker.active_at) item.append(stamped('Active', worker.active_at));
    if (worker.links.length) {
      const links = make('p', 'links');
      for (const link of worker.links) {
        const anchor = make('a', '', link.text);
        anchor.href = link.href;
        links.append(anchor);
      }
      item.append(links);
    }
    return item;
  };
  const group = (title, rows, mate) => {
    const box = make('div');
    if (title) box.append(make('h3', '', rows.length > 1 ? title + ' (' + rows.length + ')' : title));
    const roster = make('ul', 'roster');
    for (const worker of rows) roster.append(row(worker, mate));
    box.append(roster);
    return box;
  };
  const show = data => {
    heading.textContent = data.workers.length > 1 ? 'Workers (' + data.workers.length + ')' : 'Workers';
    list.replaceChildren();
    if (data.workers.length) list.append(group('', data.workers, false));
    else list.append(make('p', 'empty', 'No workers are running. A worker with a terminal shows here.'));
    if (data.second_mates.length) list.append(group('Second mates', data.second_mates, true));
    status.className = 'meta';
    status.textContent = 'Last refreshed ';
    const time = make('time', '', localTime(data.observed_at));
    time.dateTime = data.observed_at;
    status.append(time);
  };
  const failed = () => {
    status.className = 'meta failed';
    status.textContent = lastObservedAt
      ? 'Refresh failed. Showing the list from ' + localTime(lastObservedAt) + '.'
      : 'Workers could not be loaded. Try again.';
  };
  const load = async () => {
    if (button.getAttribute('aria-disabled') === 'true') return;
    button.setAttribute('aria-disabled', 'true');
    list.setAttribute('aria-busy', 'true');
    status.className = 'meta';
    status.textContent = lastObservedAt ? 'Refreshing workers' : 'Loading workers';
    try {
      const response = await fetch('workers.json', {cache: 'no-store', headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error('refresh failed');
      const data = await response.json();
      show(data);
      lastObservedAt = data.observed_at;
    } catch (error) {
      failed();
    } finally {
      button.removeAttribute('aria-disabled');
      list.removeAttribute('aria-busy');
    }
  };
  button.addEventListener('click', load);
  load();
})();
"""


VIEWPORT_SCRIPT = """
function trackVisibleViewport(onChange) {
  const viewport = window.visualViewport;
  const style = document.documentElement.style;
  let previousHeight;
  const update = () => {
    const height = viewport?.height ?? innerHeight;
    const bottom = Math.max(0, innerHeight - height - (viewport?.offsetTop ?? 0));
    style.setProperty('--visible-height', height + 'px');
    style.setProperty('--visible-bottom', bottom + 'px');
    onChange(height, previousHeight);
    previousHeight = height;
  };
  viewport?.addEventListener('resize', update);
  viewport?.addEventListener('scroll', update);
  window.addEventListener('resize', update);
  update();
}
"""


@functools.cache
def library_case_folds() -> str:
    folds = {}
    for codepoint in range(sys.maxunicode + 1):
        character = chr(codepoint)
        folded = character.casefold()
        if folded != character:
            folds[character] = folded
    return json.dumps(folds, ensure_ascii=False, separators=(",", ":"))


LIBRARY_SCRIPT = r"""
(() => {
  window.addEventListener('pageshow', event => {
    if (event.persisted) location.reload();
  });
  const dialog = document.querySelector('#library');
  const opener = document.querySelector('#open-library');
  if (!dialog || !opener || !dialog.showModal) return;
  const header = dialog.querySelector('.lib-top');
  const tabs = [...dialog.querySelectorAll('[role="tab"]')];
  const panelOf = tab => document.getElementById(tab.getAttribute('aria-controls'));
  const searchOf = tab => panelOf(tab).querySelector('input[type="search"]');
  const filtersOf = tab => [...panelOf(tab).querySelectorAll('.filters select')];
  const selectedTab = () => tabs.find(tab => tab.getAttribute('aria-selected') === 'true');
  const libraryLookup = history.state?.libraryLookup;
  const restoredTab = tabs.find(tab => tab.id === libraryLookup?.tab);
  const updateScrollMode = () => {
    if (!dialog.open) return;
    const panel = panelOf(selectedTab());
    const controlsHeight = header.offsetHeight + panel.querySelector('.lib-tools').offsetHeight
      + panel.querySelector('[data-status]').offsetHeight;
    const list = panel.querySelector('.lib-list');
    const resultSpace = parseFloat(getComputedStyle(dialog).getPropertyValue('--tap'))
      + (list ? parseFloat(getComputedStyle(list).paddingBottom) : 0);
    dialog.classList.toggle('scroll-all', dialog.clientHeight - controlsHeight < resultSpace);
  };
  trackVisibleViewport((height, previousHeight) => {
    const shortHeight = 22 * parseFloat(getComputedStyle(document.documentElement).fontSize);
    dialog.classList.toggle('short', height <= shortHeight);
    updateScrollMode();
    const focused = document.activeElement;
    if (height < previousHeight && dialog.open && dialog.contains(focused)) {
      focused.scrollIntoView({block:'nearest', inline:'nearest'});
    }
  });
  const select = (tab, focus) => {
    for (const other of tabs) {
      const on = other === tab;
      other.setAttribute('aria-selected', String(on));
      other.tabIndex = on ? 0 : -1;
      panelOf(other).hidden = !on;
    }
    updateScrollMode();
    if (focus) tab.focus();
  };
  for (const tab of tabs) {
    tab.addEventListener('click', () => select(tab, false));
    tab.addEventListener('keydown', e => {
      const keys = {ArrowRight: 1, ArrowLeft: -1};
      const at = tabs.indexOf(tab);
      const next = e.key === 'Home' ? tabs[0] : e.key === 'End' ? tabs[tabs.length - 1]
        : e.key in keys ? tabs[(at + keys[e.key] + tabs.length) % tabs.length] : null;
      if (next) { e.preventDefault(); select(next, true); }
    });
  }
  const openLibrary = () => {
    dialog.showModal();
    updateScrollMode();
    selectedTab().focus();
  };
  opener.addEventListener('click', openLibrary);
  dialog.addEventListener('close', () => {
    const {libraryLookup, ...otherState} = history.state ?? {};
    if (libraryLookup) history.replaceState(otherState, '');
    opener.focus();
  });
  dialog.addEventListener('click', e => {
    if (e.target === dialog || e.target.closest('[data-close]')) {
      dialog.close();
    } else if (!e.defaultPrevented && e.button === 0 && !e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey
               && e.target.closest('a.lib-title')) {
      const tab = selectedTab();
      const filters = Object.fromEntries(filtersOf(tab).map(select => [select.name, select.value]));
      history.replaceState({...history.state, libraryLookup:{tab:tab.id, query:searchOf(tab).value, filters}}, '');
    }
  });

  const idQuery = /^(q\d+)(?:\.(\d+))?$/i;
  const caseFold = text => Array.from(text, character => libraryCaseFolds[character] ?? character).join('');
  for (const tab of tabs) {
    const panel = panelOf(tab);
    const search = searchOf(tab);
    const topics = tab.id === 'tab-conversations';
    const savedFilters = tab === restoredTab ? libraryLookup.filters ?? {} : {};
    if (tab === restoredTab) {
      search.value = libraryLookup.query;
      for (const template of panel.querySelectorAll('template[data-filter]')) {
        if (savedFilters[template.dataset.filter]) {
          template.parentElement.hidden = false;
          template.replaceWith(template.content);
        }
      }
    }
    const filters = filtersOf(tab);
    for (const select of filters) {
      const value = savedFilters[select.name];
      if (!value) continue;
      if (![...select.options].some(option => option.value === value)) {
        select.add(new Option(select.name === 'tag' ? value.replaceAll('-', ' ') : value, value));
      }
      select.value = value;
    }
    const status = panel.querySelector('[data-status]');
    const none = panel.querySelector('[data-none]');
    const rows = [...panel.querySelectorAll('.lib-item')];
    const run = () => {
      const query = search.value.trim();
      const id = idQuery.exec(query);
      const terms = caseFold(query).split(/\s+/);
      let hasMatch = false;
      for (const row of rows) {
        const idMatch = id && row.dataset.id.toLowerCase() === id[1].toLowerCase()
          && (id[2] === undefined || (/^[1-9]\d*$/.test(id[2]) && Number(id[2]) <= Number(row.dataset.questions)));
        const searchMatch = idMatch || ((!id || topics) && terms.every(term => row.dataset.search.includes(term)));
        const ok = searchMatch && matchesClassification(row, filters);
        row.hidden = !ok;
        hasMatch ||= ok;
      }
      const active = query || filters.some(select => select.value);
      none.hidden = Boolean(active) || rows.length > 0;
      status.textContent = active && !hasMatch ? 'No results match your search.' : '';
      if (!panel.hidden) updateScrollMode();
    };
    search.addEventListener('input', run);
    for (const select of filters) select.addEventListener('change', run);
    run();
  }
  if (restoredTab) {
    select(restoredTab, false);
    openLibrary();
  }
})();
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


URL_PATTERN = re.compile(r"(?<![\w+.-])https?://\S+", re.IGNORECASE)
URL_DELIMITERS = {'"': '"', "'": "'", "<": ">"}
URL_PUNCTUATION = ".,;:!?"


def description_url(candidate: str, opener: str) -> str:
    if closer := URL_DELIMITERS.get(opener):
        url, boundary, _ = candidate.partition(closer)
        if boundary:
            return url

    url = candidate.rstrip(URL_PUNCTUATION)
    depth = 0
    for index, character in enumerate(url):
        if character == "(":
            depth += 1
        elif character == ")":
            if depth:
                depth -= 1
            elif not url[index:].strip(")" + URL_PUNCTUATION):
                return url[:index].rstrip(URL_PUNCTUATION)
    return url


def linked_text(value: object) -> str:
    """Escape text and link valid web addresses, excluding surrounding prose punctuation."""
    text, parts, last = str(value), [], 0
    position = 0
    while match := URL_PATTERN.search(text, position):
        start = match.start()
        url = description_url(match.group(), text[start - 1:start])
        position = start + len(url)
        try:
            url = clean_url(url)
        except ValueError:
            continue
        parts.append(esc(text[last:start]))
        parts.append(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(url)}</a>')
        last = position
    return "".join(parts) + esc(text[last:])


FILTER_SCRIPT = """
const matchesClassification = (entry, filters) => filters.every(select =>
  !select.value || (select.name === 'project' ? entry.dataset.project === select.value
    : entry.dataset.tags.split('|').includes(select.value)));
for (const bar of document.querySelectorAll('main .filters')) {
  const list = document.getElementById(bar.dataset.list);
  const filters = [...bar.querySelectorAll('select')];
  const none = document.getElementById(bar.dataset.list + '-empty');
  const apply = () => {
    let hasMatch = false;
    for (const entry of list.children) {
      entry.hidden = !matchesClassification(entry, filters);
      hasMatch ||= !entry.hidden;
    }
    none.hidden = hasMatch;
  };
  bar.addEventListener('change', apply);
  apply();
}
"""


def tag_text(tag: str) -> str:
    return tag.replace("-", " ")


def tag_labels(tags: list[str]) -> str:
    return '<ul class="tags">' + "".join(f"<li>{esc(tag_text(tag))}</li>" for tag in tags) + "</ul>" if tags else ""


def all_tags(item) -> list[str]:
    """An item's tags, then those of its questions."""
    tags = list(item.get("tags", []))
    if item["kind"] == "questions":
        for question in item["body"]["questions"]:
            tags.extend(question.get("tags", []))
    return list(dict.fromkeys(tags))


def filter_data(item) -> str:
    return f' data-project="{esc(item.get("project", ""))}" data-tags="{esc("|".join(all_tags(item)))}"'


def filter_bar(list_id: str, items, *, restorable: bool = False) -> str:
    """Show distinct project choices and every tag in use."""
    project_values = {item.get("project", "") for item in items}
    projects = sorted(project_values - {""}, key=str.casefold) if len(project_values) > 1 else []
    tags = sorted({tag for item in items for tag in all_tags(item)})
    fields = ""
    for label, name, values in (("Project", "projects", projects), ("Tag", "tags", tags)):
        if values or restorable:
            options = "".join(f'<option value="{esc(v)}">{esc(tag_text(v) if name == "tags" else v)}</option>' for v in values)
            field = f'<label>{label}<select name="{label.lower()}"><option value="">(All {name})</option>{options}</select></label>'
            fields += field if values else f'<template data-filter="{label.lower()}">{field}</template>'
    if not fields:
        return ""
    hidden = "" if projects or tags else " hidden"
    return f'<div class="filters" data-list="{list_id}"{hidden}>{fields}</div>'


def no_match(list_id: str) -> str:
    return f'<p class="empty no-match" id="{list_id}-empty" hidden>Nothing matches these filters.</p>'


def item_page_name(item_id: str) -> str:
    return f"{item_id.lower()}.html"


def page_shell(title: str, site_title: str, body: str, *, served: bool, saved_name: str, interactive: bool = False,
               workers: bool = False, library: str = "", conversations: bool = False, filters: bool = False,
               talking: bool = False, starting: bool = False) -> str:
    saved_href = quote(saved_name, safe="")
    opener = ('<button type="button" id="open-library" aria-haspopup="dialog" aria-controls="library">Open library</button>'
              if library else "")
    links = (
        '<link rel="manifest" href="manifest.webmanifest">\n  <link rel="icon" href="icon.svg" type="image/svg+xml">\n'
        f'  <link rel="apple-touch-icon" href="icon-180.png">\n  <meta name="theme-color" content="{APP_NAVY}">\n  '
    ) if served else ""
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  {GENERATED_MARKER}\n  <title>{esc(title)}</title>\n  {links}<style>{PAGE_CSS}</style>\n</head>\n<body>\n"
        f'<header class="bar"><a class="mark" href="{saved_href}">{esc(site_title)}</a>'
        f'<nav><a href="{saved_href}">Desk</a>{opener}</nav></header>\n'
        f"<main>\n{body}\n</main>\n{library}<script>{TIME_SCRIPT}{VIEWPORT_SCRIPT if interactive or library else ''}{QUESTION_SCRIPT if conversations else ''}"
        f"{ANSWER_SCRIPT if interactive else ''}{CONVERSATION_SCRIPT if talking else ''}{START_SCRIPT if starting else ''}"
        f"{WORKERS_SCRIPT if workers else ''}{FILTER_SCRIPT if filters or library else ''}"
        f"{f'const libraryCaseFolds = {library_case_folds()};' + LIBRARY_SCRIPT if library else ''}</script>\n</body>\n</html>\n"
    )


def delivery_label(record):
    return {"sent": "Sent", "pending": "Not delivered", "cancelled": "Sending stopped"}[record["delivery"]]


def question_summary(item, *, replies=True) -> tuple[str, list[int], bool]:
    states = [part_record(item, n).get("state", "open") for n in question_parts(item)]
    open_parts = [n for n, state in enumerate(states, 1) if state == "open"]
    state = "Open" if open_parts else ("Closed" if all(s == "closed" for s in states) else "Answered")
    if replies and (thread_unread(item, None) or any(thread_unread(item, n) for n in question_parts(item))):
        state = "Replied"
    pending = any(delivery_pending(r) for r in item.get("answers", {}).values())
    return state, open_parts, pending


def progress_text(item, summary):
    """The one line of progress: replied first, then how many questions are still open."""
    state, open_parts, _ = summary
    remaining = f"{len(open_parts)} of {len(question_parts(item))} open" if open_parts else ""
    if state == "Replied":
        return f"Replied, {remaining}" if remaining else "Replied"
    return remaining or state


def question_card(item, summary, *, served, primary, show_project=True):
    state, open_parts, pending = summary
    remaining = len(open_parts)
    href = item_page_name(item["id"])
    if open_parts:
        href += f'#{item["id"]}.{open_parts[0]}'
    progress = progress_text(item, summary)
    action = "Read questions" if not served else ("Answer questions" if remaining else "Review answers")
    link_style = "work-action" if primary else "button-link"
    title = esc(item["title"])
    questions = item["body"]["questions"]
    prompts = "".join(f'<li>{linked_text(questions[n - 1]["question"])}</li>' for n in open_parts)
    first_link = next((link_html(questions[n - 1]["links"][0], prefix=f'{item["id"]}.{n} ')
                       for n in open_parts if questions[n - 1].get("links")), '')
    visual = f'<p class="visual">{first_link}</p>' if first_link else ''
    project = f' <span class="project-name">{esc(item["project"])}</span>' if show_project and item.get("project") else ""
    return (f'<article class="work{' next' if primary else ''}"><div class="eyebrow"><span class="id">{item["id"]}</span> Questions{project}</div>'
            f'<h3>{title}</h3><p>{linked_text(item["description"])}</p>'
            + (f'<ul>{prompts}</ul>' if prompts else '')
            + tag_labels(all_tags(item))
            + visual
            + (f'<div class="actions"><span class="badge">Replied</span>' + (f'<span>{remaining} of {len(question_parts(item))} open</span>' if remaining else '')
               if state == "Replied" else f'<div class="actions"><span class="badge">{progress}</span>')
            + ('<span>Not delivered</span>' if pending else '')
            + f'<a class="card-link {link_style}" href="{href}" aria-label="{action} for {title}">{action}</a></div></article>')


def link_html(link, *, prefix=""):
    return (f'<a class="ref-link" href="{esc(link["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{esc(prefix + link["text"])}</a>')


def links_html(record):
    links = record.get("links")
    return '<ul class="ref-links">' + ''.join(f'<li>{link_html(l)}</li>' for l in links) + '</ul>' if links else ''


def has_answer_controls(item):
    return item["kind"] == "questions" and any(
        part_record(item, n).get("state") != "closed" for n in question_parts(item))


def image_url(item, image, tree="images"):
    return f'{tree}/{str(item["id"]).lower()}/{image["file"]}'


def image_tag(item, image, *, element_id=None, tree="images"):
    identifier = f' id="{element_id}"' if element_id else ''
    return f'<img src="{esc(image_url(item, image, tree))}" alt="{esc(image["alt"])}" loading="lazy" decoding="async"{identifier}>'


def image_link(item, image, *, tree="images"):
    """The image as a link to the full image."""
    return (f'<a class="shot" href="{esc(image_url(item, image, tree))}" target="_blank" rel="noopener">'
            f'{image_tag(item, image, tree=tree)}</a>')


def shots_html(item, record, *, tree="images"):
    if not record.get("images"):
        return ""
    return '<ul class="shots">' + "".join(
        f'<li>{image_link(item, image, tree=tree)}</li>' for image in record["images"]) + '</ul>'


def choice_label(item, part, number, option, *, suggested):
    """Render a choice with its mockup inside the radio label and a separate full-size link."""
    label = option_label(option)
    attributes = f'type="radio" name="choice-{part}" data-choice="{esc(label)}"' + (' data-suggested' if suggested else '')
    if not isinstance(option, dict):
        badge = ' <small>Suggested</small>' if suggested else ''
        return f'<label class="choice"><input {attributes}> {linked_text(label)}{badge}</label>'
    image, words = option["image"], f"words-{part}-{number}"
    mockup, suggestion = f"mockup-{part}-{number}", f"suggestion-{part}-{number}"
    description = f"{mockup} {suggestion}" if suggested else mockup
    badge = f' <small id="{suggestion}">Suggested</small>' if suggested else ''
    return (f'<div class="option"><label class="choice has-image">{image_tag(item, image, element_id=mockup)}'
            f'<span class="pick"><input {attributes} aria-labelledby="{words}" aria-describedby="{description}"> '
            f'<span id="{words}">{linked_text(label)}</span>{badge}</span></label>'
            f'<a class="open-image" href="{esc(image_url(item, image))}" target="_blank" rel="noopener" '
            f'aria-label="Open image for {esc(label)}">Open image</a></div>')


def read_only_option(item, option):
    if not isinstance(option, dict):
        return f'<li>{linked_text(option)}</li>'
    return f'<li class="option">{image_link(item, option["image"])}<span>{linked_text(option["label"])}</span></li>'


def message_html(item, message, *, editable):
    you = message["author"] == "you"
    when = esc(message["at"])
    delivery = f' <span class="delivery">{delivery_label(message)}</span>' if you else ''
    controls = ''
    if editable and you and message["delivery"] == "pending":
        controls = ('<div class="actions">'
                    f'<button type="button" data-retry-ask="{message["id"]}">'
                    f'{"Send message again" if item["kind"] == "conversation" else "Send ask again"}</button></div>')
    identity = f' data-ask-id="{message["id"]}"' if you else f' data-reply-id="{message["id"]}"'
    unread = " data-unread" if unread_reply(message) else ""
    return (f'<li class="msg {message["author"]}"{identity}{unread}><p class="who"><strong>{AUTHOR_LABELS[message["author"]]}</strong> '
            f'<time datetime="{when}">{when}</time>{delivery}</p><p class="record">{linked_text(message["text"])}</p>'
            f'{links_html(message)}{shots_html(item, message, tree=REPLY_IMAGES)}{controls}</li>')


def thread_html(item, number, *, editable):
    if item["kind"] == "conversation":
        messages = ''.join(message_html(item, m, editable=editable) for m in part_thread(item, number))
        return f'<ol class="exchange">{messages}</ol>' if messages else ''
    groups = {}
    for message in part_thread(item, number):
        if message["author"] == "you":
            groups[message["id"]] = [message]
        else:
            groups[message["ask"]].append(message)
    return "".join(
        '<ol class="exchange">' + ''.join(message_html(item, m, editable=editable) for m in group) + '</ol>'
        for group in groups.values())


def conversation_thread(item, number, target, *, editable):
    thread = thread_html(item, number, editable=editable)
    if not (editable or thread):
        return ""
    hidden = "" if thread else " hidden"
    pending = '<div class="pending-asks"></div>' if editable else ''
    return (f'<details class="thread" id="thread-{target}"{hidden}><summary>Messages</summary>'
            f'<div class="thread-messages">{thread}</div>{pending}</details>')


def conversation_toolbar(item, *, asking):
    controls = '<button id="send" class="primary" disabled>Send answers</button>'
    if asking:
        controls += ('<button type="button" id="open-item-ask" aria-controls="item-ask-editor" '
                     'aria-expanded="false">Ask Firstmate</button>')
    if any(q.get("suggestion") and conversation_editable(item, n)
           for n, q in enumerate(item["body"]["questions"], 1)):
        controls += '<button id="suggest">Use all suggestions</button>'
    controls += '<button id="undo" hidden>Undo changes</button>'
    return f'<div class="sendbar"><div class="actions">{controls}</div><output role="status" tabindex="0"></output></div>'


def item_conversation(item, ask_binding):
    asking = ask_binding is not None
    target = item["id"]
    thread = conversation_thread(item, None, target, editable=asking)
    if not thread:
        return ""
    unread = thread_unread(item, None)
    badge = '<span class="badge">Replied</span>' if unread else '<span class="badge" hidden></span>'
    editor = (f'<div id="item-ask-editor" hidden><label for="ask-{target}">Message about this item</label>'
              f'<textarea id="ask-{target}" maxlength="{TEXT_LIMIT}" rows="2" aria-describedby="error-{target}"></textarea>'
              f'<p id="error-{target}" hidden></p><div class="actions ask-row">'
              f'<button type="button" class="ask-button" data-home="{esc(ask_binding)}" disabled>Send ask</button></div></div>'
              if asking else '')
    return (f'<section id="{target}" class="work item-conversation" data-conversation aria-labelledby="item-conversation-title">'
            f'<div class="eyebrow">{badge}</div><h2 id="item-conversation-title">Item conversation</h2>'
            f'{editor}{thread}</section>')


def conversation_page(item, ask_binding):
    """A conversation: every message and reply in time order, and a box for the next message."""
    target = item["id"]
    thread = thread_html(item, None, editable=bool(ask_binding))
    unread = thread_unread(item, None)
    badge = '<span class="badge">Replied</span>' if unread else '<span class="badge" hidden></span>'
    if ask_binding:
        composer = (f'<div class="composer"><label for="message-{target}">Your message</label>'
                    f'<textarea id="message-{target}" maxlength="{TEXT_LIMIT}" rows="3" aria-describedby="error-{target}"></textarea>'
                    f'<p id="error-{target}" hidden></p><div class="actions"><button type="button" class="primary" id="send-message" '
                    f'data-home="{esc(ask_binding)}" disabled>Send message</button><output role="status" tabindex="0"></output></div></div>')
    else:
        composer = '<p class="empty">Set firstmate_home to send Firstmate more messages.</p>'
    return (f'<section id="{target}" class="work item-conversation conversation" data-conversation>'
            f'<div class="eyebrow">{badge}</div><div class="thread-messages">{thread}</div>{composer}</section>')


def question_form(item, served, *, ask_binding=None):
    cards, folded = [], []
    asking = ask_binding is not None
    for number, question in enumerate(item["body"]["questions"], 1):
        part = f'{item["id"]}.{number}'
        record = part_record(item, number)
        status = record.get("state", "open")
        label = part_state(item, number, replies=served).capitalize()
        answer = record.get("answer", "")
        editable = served and status != "closed"
        saved = ""
        if answer or editable:
            at = esc(record.get("answered_at", ""))
            delivery = delivery_label(record) if answer else ""
            hidden = "" if answer else " hidden"
            saved = (f'<div class="saved-answer"{hidden}><p class="record">{esc(answer)}</p>'
                     f'<p class="meta"><span class="delivery">{delivery}</span> '
                     f'<time datetime="{at}">{at}</time></p></div>')
        elsewhere = latest_off_desk_mark(record)
        if elsewhere:
            at = esc(elsewhere["answered_at"]) if elsewhere["answered_at"] else ""
            time = f' <time datetime="{at}">{at}</time>' if at else ""
            saved += f'<p class="meta answer-source">Answered elsewhere · {esc(elsewhere["via"])}{time}</p>'
        elif editable:
            saved += '<p class="meta answer-source" hidden></p>'
        editor = f' data-state="{status}" data-conversation' if served else ""
        if editable:
            editor += (f' tabindex="-1" data-revision="{esc(record.get("revision", ""))}"'
                       f' data-pending="{int(delivery_pending(record))}"')
            suggestions = question.get("suggestion")
            choices = list(question["options"])
            if suggestions and suggestions not in map(option_label, choices):
                choices.append(suggestions)
            labels = [choice_label(item, part, n, choice, suggested=option_label(choice) == suggestions)
                      for n, choice in enumerate(choices, 1)]
            pictured = any(isinstance(choice, dict) for choice in choices)
            controls = (links_html(question) + f'<div class="choices{" has-images" if pictured else ""}">' + ''.join(labels) + '</div>'
                        + f'<label for="answer-{part}">Your answer{" or question" if asking else ""}</label><textarea id="answer-{part}" '
                        + f'maxlength="{TEXT_LIMIT}" rows="2" aria-describedby="error-{part}">{esc(answer)}</textarea>'
                        + f'<p id="error-{part}" hidden></p>')
            if asking:
                controls += (f'<div class="actions ask-row"><button type="button" class="ask-button" data-home="{esc(ask_binding)}" '
                             'disabled>Ask Firstmate</button></div>')
        else:
            pictured = any(isinstance(v, dict) for v in question["options"])
            controls = (links_html(question) + ('<ul class="has-images">' if pictured else '<ul>')
                        + ''.join(read_only_option(item, v) for v in question["options"]) + '</ul>')
            if question.get("suggestion"):
                controls += f'<p><strong>Suggested answer</strong><br>{linked_text(question["suggestion"])}</p>'
        thread = conversation_thread(item, number, part, editable=editable and asking) if served else ''
        card = (f'<section class="decision" id="{part}" aria-labelledby="title-{part}"{editor}>'
                f'<div class="eyebrow"><span class="id">{part}</span><span class="badge">{label}</span>'
                f'{f"<span>{esc(question['kind'].capitalize())}</span>" if question.get("kind") else ""}</div>'
                f'{tag_labels(question.get("tags", []))}'
                f'<h2 id="title-{part}">{linked_text(question["question"])}</h2><p>{linked_text(question["context"])}</p>'
                f'{shots_html(item, question)}{saved}{controls}{thread}</section>')
        if label == "Answered" and record.get("delivery") == "sent":
            source_hidden = "" if elsewhere else " hidden"
            folded.append(f'<details class="done"><summary><span class="id">{part}</span>'
                          f'<span class="sum-q">{esc(question["question"])}</span>'
                          f'<span class="sum-a"><span class="sum-text">{esc(answer)}</span>'
                          f'<span class="sum-source"{source_hidden}> (Desk)</span></span></summary>{card}</details>')
        else:
            cards.append(card)
    progress = progress_text(item, question_summary(item, replies=served))
    editable = served and conversation_editable(item, None)
    toolbar = conversation_toolbar(item, asking=asking) if editable else ""
    whole_item = item_conversation(item, ask_binding if editable else None) if served else ""
    return f'<p class="progress">{progress}</p>' + whole_item + '<div id="questions">' + ''.join(cards) + ''.join(folded) + '</div>' + toolbar


QUESTION_SCRIPT = """
const sections = [...document.querySelectorAll('.decision')];
const replyStates = new Map([...document.querySelectorAll('[data-conversation]')].map(section => [section, new Map()]));
const questionNumber = section => Number(section.id.split('.')[1]);
function expandAnswer(section) {
  const box = section.closest('details.done');
  if (!box) return;
  const list = box.parentElement;
  const number = questionNumber(section);
  const focused = document.activeElement;
  const restoreFocus = box.contains(focused);
  const next = [...list.children].find(child => child.matches('details.done') || questionNumber(child) > number);
  list.insertBefore(section, next);
  box.remove();
  if (restoreFocus) (section.contains(focused) ? focused : section.querySelector('a[href]') || section).focus();
}
function updateProgress() {
  const progress = document.querySelector('.progress');
  if (!progress) return;
  const replied = [...replyStates.keys()].some(s => s.querySelector('.badge').textContent === 'Replied');
  const remaining = sections.filter(s => s.dataset.state === 'open').length;
  const open = remaining ? remaining + ' of ' + sections.length + ' open' : '';
  const state = sections.every(s => s.dataset.state === 'closed') ? 'Closed' : 'Answered';
  progress.textContent = replied ? ['Replied', open].filter(Boolean).join(', ') : (open || state);
}
function reconcileReplies(section, snapshot = {}) {
  const states = replyStates.get(section);
  const replies = section.querySelectorAll('[data-reply-id]');
  for (const [id, read] of Object.entries(snapshot)) {
    if (read || !states.has(id)) states.set(id, read);
  }
  let visibleUnread = false;
  for (const reply of replies) {
    const id = reply.dataset.replyId;
    if (!reply.hasAttribute('data-unread')) states.set(id, true);
    else if (states.get(id)) reply.removeAttribute('data-unread');
    else {
      states.set(id, false);
      visibleUnread = true;
    }
  }
  const state = section.dataset.state;
  const badge = section.querySelector('.badge');
  const unread = section.classList.contains('conversation') ? visibleUnread : [...states.values()].includes(false);
  badge.textContent = unread ? 'Replied' : state ? state[0].toUpperCase() + state.slice(1) : '';
  badge.hidden = !badge.textContent;
  if (unread) expandAnswer(section);
  updateProgress();
}
async function acknowledgeReplies(section) {
  const shown = [...section.querySelectorAll('[data-reply-id][data-unread]')].map(reply => reply.dataset.replyId);
  for (let start = 0; start < shown.length; start += 1000) {
    const ids = shown.slice(start, start + 1000);
    try {
      const response = await fetch('replies/read', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({part:section.id, ids})});
      if (!response.ok) return;
      const result = await response.json();
      reconcileReplies(section, result.replies);
    } catch {
      return;
    }
  }
}
document.addEventListener('click', event => {
  const summary = event.target.closest('.thread > summary');
  if (!summary || summary.parentElement.open || event.defaultPrevented) return;
  event.preventDefault();
  summary.parentElement.open = true;
  acknowledgeReplies(summary.closest('[data-conversation]'));
});
"""


def talk_script(body):
    """Shared by the Desk form and a conversation's message box: one POST of a message with its own ID."""
    return r"""
const messageId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
const trimmed = value => value.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, '');
const SENDING_FAILED = () => navigator.onLine ? 'Send interrupted. Send again.' : 'Offline. Reconnect and send again.';
async function postJson(path, body) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (response.status === 409) {
    const {message, saved} = await response.json();
    return {ok:false, message, saved};
  }
  if ([400, 403].includes(response.status)) return {ok:false, message:await response.text()};
  if (!response.ok) throw new Error('failed');
  return {ok:true, result:await response.json()};
}
""" + body


CONVERSATION_SCRIPT = talk_script(r"""
(() => {
  const section = document.querySelector('.conversation');
  if (!section) return;
  const refreshReplies = () => {
    reconcileReplies(section);
    acknowledgeReplies(section);
  };
  refreshReplies();
  const field = section.querySelector('textarea');
  const button = section.querySelector('#send-message');
  if (!field || !button) return;
  const output = section.querySelector('output');
  const error = document.getElementById(field.getAttribute('aria-describedby'));
  const messages = section.querySelector('.thread-messages');
  let draft = null;
  const refresh = () => {
    button.disabled = !trimmed(field.value);
  };
  const show = message => {
    error.textContent = message || '';
    error.hidden = !message;
  };
  const send = async body => {
    const controls = section.querySelectorAll('button,textarea');
    controls.forEach(c => c.disabled = true);
    output.textContent = 'Sending message';
    show('');
    try {
      const outcome = await postJson('asks', body);
      if (outcome.ok) {
        messages.innerHTML = outcome.result.thread;
        localizeTimes(messages);
        refreshReplies();
        output.textContent = outcome.result.message;
        if (body.text !== undefined) {
          field.value = '';
          draft = null;
        }
      } else {
        output.textContent = '';
        show(outcome.message);
        if (body.text !== undefined && outcome.saved === false) draft = null;
      }
    } catch {
      output.textContent = SENDING_FAILED();
    }
    controls.forEach(c => c.disabled = false);
    refresh();
  };
  field.addEventListener('input', () => { show(''); refresh(); });
  button.addEventListener('click', () => {
    const text = trimmed(field.value);
    if (!text) return;
    const retry = draft?.text === text;
    if (!retry) draft = {id:messageId(), text};
    const body = {part:section.id, id:draft.id, text, home:button.dataset.home};
    send(retry ? {...body, action:'retry'} : body);
  });
  section.addEventListener('click', event => {
    const retry = event.target.closest('[data-retry-ask]');
    if (retry) send({part:section.id, id:retry.dataset.retryAsk, action:'retry'});
  });
  refresh();
})();
""")


START_SCRIPT = talk_script(r"""
(() => {
  const open = document.querySelector('#open-talk');
  const form = document.querySelector('#talk-form');
  if (!open || !form) return;
  const topic = form.querySelector('#talk-topic');
  const message = form.querySelector('#talk-message');
  const button = form.querySelector('button[type="submit"]');
  const output = form.querySelector('output');
  const error = form.querySelector('#talk-error');
  const topicText = () => trimmed(topic.value.replace(/[\p{White_Space}\u001c-\u001f]+/gu, ' '));
  let draft = null;
  const refresh = () => {
    button.disabled = !(topicText() && trimmed(message.value));
  };
  open.addEventListener('click', () => {
    form.hidden = !form.hidden;
    open.setAttribute('aria-expanded', String(!form.hidden));
    if (!form.hidden) topic.focus();
  });
  form.addEventListener('input', () => { error.hidden = true; refresh(); });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const title = topicText(), text = trimmed(message.value);
    if (!title || !text) return;
    if (draft?.title !== title || draft.text !== text) draft = {id:messageId(), title, text};
    const controls = form.querySelectorAll('button,input,textarea');
    controls.forEach(c => c.disabled = true);
    output.textContent = 'Starting conversation';
    try {
      const outcome = await postJson('conversations', {id:draft.id, title, text, home:button.dataset.home});
      if (outcome.ok) {
        output.textContent = outcome.result.message;
        location.href = outcome.result.href;
        return;
      }
      output.textContent = '';
      error.textContent = outcome.message;
      error.hidden = false;
      if (outcome.saved === false) draft = null;
    } catch {
      output.textContent = SENDING_FAILED();
    }
    controls.forEach(c => c.disabled = false);
    refresh();
  });
  refresh();
})();
""")


ANSWER_SCRIPT = r"""
const send = document.querySelector('#send');
const openItemAsk = document.querySelector('#open-item-ask');
if (send) {
  const fields = [...document.querySelectorAll('.decision textarea')];
  const editors = [...document.querySelectorAll('[data-conversation] textarea')];
  const bar = send.closest('.sendbar');
  const output = bar.querySelector('output');
  let previousBarTop;
  const reserveBarSpace = () => {
    const bounds = bar.getBoundingClientRect();
    document.documentElement.style.setProperty('--sendbar-height', bounds.height + 'px');
    const coveredTop = previousBarTop;
    previousBarTop = bounds.top;
    const focused = document.activeElement;
    if (bounds.top < coveredTop && focused && focused !== document.body && !bar.contains(focused)) {
      const control = focused.getBoundingClientRect();
      if (control.top < coveredTop && control.bottom > bounds.top) {
        focused.scrollIntoView({block:'nearest', inline:'nearest'});
      }
    }
  };
  trackVisibleViewport(reserveBarSpace);
  new ResizeObserver(reserveBarSpace).observe(bar, {box:'border-box'});
  const list = document.querySelector('#questions');
  const suggest = document.querySelector('#suggest');
  const undo = document.querySelector('#undo');
  const rejections = new Map();
  const draftOrigins = new Map(fields.filter(f => f.closest('section').querySelector('.ask-button'))
    .map(f => [f, 'filled']));
  let history = [];
  const answerText = value => value.replace(/^[\p{White_Space}\x1c-\x1f]+|[\p{White_Space}\x1c-\x1f]+$/gu, '');
  const newDraft = f => answerText(f.value) && answerText(f.value) !== answerText(f.defaultValue);
  const rejectedAsk = origin => origin?.rejected && !origin.edited;
  const handEdited = origin => origin === 'manual' || origin?.rejected || origin?.edited;
  const askDraft = f => (f.closest('.item-conversation') || handEdited(draftOrigins.get(f))) && newDraft(f);
  const changed = f => {
    const {pending} = f.closest('section').dataset;
    const answer = answerText(f.value);
    return !rejectedAsk(draftOrigins.get(f)) && !rejections.has(f) && answer && (answer !== f.defaultValue || pending === '1');
  };
  const suggestionFor = f => {
    const section = f.closest('section');
    if (!rejections.has(f) && section.dataset.state === 'open' && !f.defaultValue && !f.value) {
      return section.querySelector('[data-suggested]');
    }
  };
  function forget(field) {
    for (const step of history) step.delete(field);
    history = history.filter(step => step.size);
  }
  function refresh() {
    for (const [field, rejection] of rejections) {
      const draft = answerText(field.value);
      if (rejection.draft !== undefined && draft !== rejection.draft) {
        rejections.delete(field);
        output.textContent = '';
      }
    }
    if (send) {
      const count = fields.filter(changed).length;
      send.disabled = !count;
      send.textContent = count ? 'Send ' + count + (count === 1 ? ' answer' : ' answers') : 'Send answers';
    }
    if (suggest) suggest.disabled = !fields.some(suggestionFor);
    if (undo) undo.hidden = !history.length;
    for (const f of editors) {
      const section = f.closest('section');
      const ask = section.querySelector('.ask-button');
      if (ask) ask.disabled = !askDraft(f);
      for (const retry of section.querySelectorAll('.pending-asks button')) {
        retry.disabled = Boolean(newDraft(f));
      }
      const error = document.getElementById(f.getAttribute('aria-describedby'));
      const message = rejections.get(f)?.message || '';
      error.textContent = message;
      error.hidden = !message;
      if (message) f.setAttribute('aria-invalid', 'true');
      else f.removeAttribute('aria-invalid');
      for (const b of section.querySelectorAll('[data-choice]')) {
        b.checked = b.dataset.choice === f.value;
      }
    }
  }
  document.addEventListener('input', e => {
    const field = e.target;
    if (!field.matches('[data-conversation] textarea')) return;
    const origin = draftOrigins.get(field);
    const restoring = e.inputType === 'historyUndo' || e.inputType === 'historyRedo';
    if (origin?.choice) {
      // Native undo and redo treat matching hand-edited text as the fill. Retype it to ask.
      const edited = answerText(field.value) !== answerText(origin.choice.dataset.choice) || (!restoring && origin.edited);
      draftOrigins.set(field, {choice: origin.choice, edited});
    } else if (origin?.rejected && (!restoring || origin.edited !== undefined)) {
      const edited = answerText(field.value) !== origin.rejected;
      if (edited || origin.edited !== undefined) draftOrigins.set(field, {rejected: origin.rejected, edited});
    } else if (origin === 'filled') {
      draftOrigins.set(field, 'manual');
    }
    forget(field);
    refresh();
  });
  openItemAsk?.addEventListener('click', () => {
    const editor = document.getElementById('item-ask-editor');
    editor.hidden = !editor.hidden;
    openItemAsk.setAttribute('aria-expanded', String(!editor.hidden));
    if (!editor.hidden) editor.querySelector('textarea').focus();
  });
  document.addEventListener('click', e => {
    const choice = e.target.closest('[data-choice]');
    if (choice || e.target === suggest) {
      const step = new Map();
      for (const f of fields) {
        const section = f.closest('section');
        const option = choice ? (section.contains(choice) && choice) : suggestionFor(f);
        const origin = draftOrigins.get(f);
        if (option && (option.dataset.choice !== f.value || handEdited(origin))) {
          const askError = rejectedAsk(origin) ? rejections.get(f) : undefined;
          step.set(f, {value: f.value, origin, askError});
          if (askError) rejections.delete(f);
          f.value = option.dataset.choice;
          if (origin) draftOrigins.set(f, {choice: option, edited: false});
        }
      }
      if (step.size) history.push(step);
      refresh();
    }
    const askButton = e.target.closest('.ask-button');
    if (askButton) {
      const section = askButton.closest('section');
      const field = section.querySelector('textarea');
      if (askDraft(field)) {
        const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
        const body = {part: section.id, id, text: answerText(field.value), home: askButton.dataset.home};
        const thread = section.querySelector('.thread');
        const pending = document.createElement('ol');
        pending.className = 'exchange';
        pending.dataset.localAsk = id;
        pending.innerHTML = '<li class="msg you"><p class="who"><strong>You</strong> <span class="delivery">Pending</span></p>'
          + '<p class="record"></p><div class="actions"><button type="button">Send ask again</button></div></li>';
        pending.querySelector('.record').textContent = field.value;
        pending.querySelector('button').addEventListener('click', () => askRequest(section, {...body, action: 'retry'}, pending));
        thread.querySelector('.pending-asks').append(pending);
        field.value = field.defaultValue;
        if (section.matches('.item-conversation') || rejectedAsk(draftOrigins.get(field))) rejections.delete(field);
        if (draftOrigins.has(field)) draftOrigins.set(field, 'filled');
        forget(field);
        thread.hidden = false;
        thread.open = true;
        askRequest(section, body, pending);
      }
    }
    const threadButton = e.target.closest('[data-retry-ask]');
    if (threadButton) {
      const section = threadButton.closest('section');
      askRequest(section, {part: section.id, id: threadButton.dataset.retryAsk, action: 'retry'});
    }
    if (e.target === undo && history.length) {
      const step = history.pop();
      for (const [f, {value, origin, askError}] of step) {
        f.value = value;
        if (origin) draftOrigins.set(f, origin);
        if (askError) rejections.set(f, askError);
      }
      refresh();
      if (history.length) undo.focus();
      else if (!send.disabled) send.focus();
      else {
        const field = step.keys().next().value;
        const folded = field.closest('details:not([open])');
        (folded ? folded.querySelector('summary') : field).focus();
      }
    }
  });
  function updateFold(section, record) {
    if (record.state !== 'answered' || record.delivery !== 'sent' || section.querySelector('.badge').textContent === 'Replied') {
      expandAnswer(section);
      return;
    }
    const number = questionNumber(section);
    const focusInSection = section.contains(document.activeElement);
    let box = section.closest('details.done');
    if (!box) {
      const part = (tag, className, text) => {
        const node = document.createElement(tag);
        node.className = className;
        node.textContent = text;
        return node;
      };
      box = document.createElement('details');
      box.className = 'done';
      const summary = document.createElement('summary');
      const answer = part('span', 'sum-a', '');
      answer.append(part('span', 'sum-text', ''), part('span', 'sum-source', ' (Desk)'));
      summary.append(part('span', 'id', section.id), part('span', 'sum-q', section.querySelector('h2').textContent), answer);
      box.append(summary, section);
      const next = [...list.querySelectorAll(':scope > details.done')]
        .find(child => questionNumber(child.lastElementChild) > number);
      list.insertBefore(box, next ?? null);
    }
    box.querySelector('.sum-text').textContent = record.answer;
    box.querySelector('.sum-source').hidden = !record.off_desk_answer;
    box.open = false;
    if (focusInSection) box.querySelector('summary').focus();
  }
  async function askRequest(section, body, pending) {
    let failedDraft;
    if (undo) undo.hidden = true;
    const controls = document.querySelectorAll('button,input,textarea');
    controls.forEach(c => c.disabled = true);
    output.textContent = 'Sending ask';
    try {
      const response = await fetch('asks', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body)});
      if ([400, 403, 409].includes(response.status)) {
        const {message, saved} = response.status === 409 ? await response.json() :
          {message: await response.text(), saved: false};
        output.textContent = message;
        const blockedRetry = response.status === 403 && body.action === 'retry';
        if (pending && saved === false && !blockedRetry) {
          failedDraft = section.querySelector('textarea');
          failedDraft.value = pending.querySelector('.record').textContent;
          forget(failedDraft);
          if (draftOrigins.has(failedDraft)) draftOrigins.set(failedDraft, {rejected: body.text});
          rejections.set(failedDraft, {message, draft: body.text});
          pending.remove();
          const thread = section.querySelector('.thread');
          thread.hidden = !thread.querySelector('.thread-messages > *, .pending-asks > *');
        }
      } else if (!response.ok) {
        throw new Error(await response.text());
      } else {
        const result = await response.json();
        const thread = section.querySelector('.thread');
        thread.querySelector('.thread-messages').innerHTML = result.thread;
        const saved = new Set([...thread.querySelectorAll('[data-ask-id]')].map(ask => ask.dataset.askId));
        for (const pending of thread.querySelectorAll('[data-local-ask]')) {
          if (saved.has(pending.dataset.localAsk)) pending.remove();
        }
        localizeTimes(thread);
        reconcileReplies(section);
        output.textContent = result.message;
      }
    } catch (err) {
      output.textContent = navigator.onLine ? 'Send interrupted. ' + err.message : 'Offline. Reconnect and send again.';
    }
    controls.forEach(c => c.disabled = false);
    refresh();
    if (failedDraft) failedDraft.focus();
  }
  function showAnswer(section, field, record) {
    section.dataset.state = record.state;
    section.dataset.revision = record.revision;
    section.dataset.pending = record.delivery === 'pending' ? '1' : '0';
    field.defaultValue = field.value = record.answer;
    if (draftOrigins.has(field)) draftOrigins.set(field, 'filled');
    forget(field);

    const saved = section.querySelector('.saved-answer');
    saved.querySelector('.record').textContent = record.answer;
    saved.querySelector('.delivery').textContent = {sent: 'Sent', pending: 'Not delivered', cancelled: 'Sending stopped'}[record.delivery];
    const time = saved.querySelector('time');
    time.dateTime = record.answered_at;
    time.textContent = localTime(record.answered_at);
    saved.hidden = false;

    const source = section.querySelector('.answer-source');
    const elsewhere = record.off_desk_answer;
    source.hidden = !elsewhere;
    source.textContent = elsewhere ? 'Answered elsewhere · ' + elsewhere.via : '';
    if (elsewhere?.answered_at) {
      const markedAt = document.createElement('time');
      markedAt.dateTime = elsewhere.answered_at;
      markedAt.textContent = localTime(markedAt.dateTime);
      source.append(' ', markedAt);
    }

    reconcileReplies(section);
    updateFold(section, record);
  }
  send?.addEventListener('click', async () => {
    const pending = fields.filter(changed);
    undo.hidden = true;
    const controls = document.querySelectorAll('button,input,textarea');
    controls.forEach(c => c.disabled = true);
    let interrupted = '';
    let rejected = 0;
    try {
      for (const [i,f] of pending.entries()) {
        output.textContent = 'Sending ' + (i+1) + ' of ' + pending.length;
        const section = f.closest('section');
        const answer = answerText(f.value);
        const response = await fetch('answers', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({part:section.id, revision:section.dataset.revision, answer})});
        if (response.status === 400 || response.status === 409) {
          rejections.set(f, {message: await response.text(), draft: response.status === 400 ? answer : undefined});
          const box = f.closest('details.done');
          if (box) box.open = true;
          rejected += 1;
          continue;
        }
        if (!response.ok) throw new Error(await response.text());
        showAnswer(section, f, await response.json());
      }
    } catch (err) {
      interrupted = navigator.onLine ? 'Send interrupted. ' + err.message : 'Offline. Reconnect and send again.';
    }
    const summary = rejected ? rejected + (rejected === 1 ? ' answer rejected.' : ' answers rejected.') : '';
    output.textContent = [summary, interrupted].filter(Boolean).join(' ') || 'Answers saved.';
    controls.forEach(c => c.disabled = false);
    refresh();
  });
  refresh();
}
"""


def day_time(moment: object) -> str:
    return f'<time class="day" datetime="{esc(moment)}">{esc(str(moment)[:10])}</time>'


def library_row(item, *, served):
    item_id, kind = item["id"], item["kind"]
    title = f'<a class="lib-title" href="{item_page_name(item_id)}">{esc(item["title"])}</a>'
    project = f'<span class="project-name">{esc(item["project"])}</span>' if item.get("project") else ""
    tags = tag_labels(all_tags(item))
    attributes = filter_data(item)
    if kind == "questions" and not served:
        return f'<li class="lib-item"{attributes}>{title}{f" {project}" if project else ""}{tags}</li>'
    unread = served and thread_unread(item, None)

    if served:
        search = [item["title"]]
        if kind != "conversation":
            search.append(item["description"])
        if kind == "questions":
            parts = question_parts(item)
            search.extend(q["question"] for q in item["body"]["questions"])
            search.extend(part_record(item, n).get("answer", "") for n in parts)
            attributes += f' data-questions="{len(parts)}"'
        attributes += f' data-id="{item_id}" data-search="{esc(" ".join(search).casefold())}"'
    if kind == "questions":
        state, _, _ = question_summary(item, replies=served)
        label = '<span class="badge">Replied</span>' if state == "Replied" else state
        detail = f'<p class="meta">{label}</p>'
    else:
        replied = '<span class="badge">Replied</span>' if unread else ""
        description = '' if kind == "conversation" else f'<p class="lib-desc">{linked_text(item["description"])}</p>'
        detail = description + f'<p class="meta"><span>{KIND_LABELS[kind]}</span>{replied}{day_time(item["saved_at"])}</p>'
    return (f'<li class="lib-item"{attributes}>'
            f'<div class="lib-head"><span class="id">{item_id}</span>'
            f'{title}{project}</div>{detail}{tags}</li>')


def library_panel(name: str, items: list[dict[str, object]], empty: str, *, served: bool) -> str:
    list_id = f"{name}-list"
    filters = filter_bar(list_id, items, restorable=served)
    rows = "".join(library_row(item, served=served) for item in items)
    listing = f'<ul class="lib-list" id="{list_id}">{rows}</ul>' if rows else ""
    if not served:
        return (filters + listing + (no_match(list_id) if filters else "")) if rows else f'<p class="empty">{esc(empty)}</p>'
    return (f'<div class="lib-tools"><label for="search-{name}">Search</label>'
            f'<input type="search" id="search-{name}" placeholder="Words or an ID" autocomplete="off">{filters}</div>'
            f'<p class="lib-status" role="status" data-status></p>{listing}'
            f'<p class="empty" data-none{" hidden" if rows else ""}>{esc(empty)}</p>')


def library_lists(data: dict[str, object], *, served: bool) -> tuple[str, str]:
    references, answered = [], []
    for item in newest_first(data["items"]):  # type: ignore[arg-type]
        if item["kind"] in ("resource", "list"):
            references.append(item)
        elif item["kind"] == "questions" and not desk_item(item):
            answered.append(item)
    return (library_panel("references", references, "No references are saved yet.", served=served),
            library_panel("answered", answered, "Nothing is answered yet.", served=served))


def library_dialog(data: dict[str, object]) -> str:
    references, answered = library_lists(data, served=True)
    items = [item for item in newest_first(data["items"]) if item["kind"] == "conversation"]  # type: ignore[arg-type]
    conversations = library_panel("conversations", items,
                                  "No conversations yet. Talk to Firstmate from the Desk to start one.", served=True)
    names = (("references", "References"), ("answered", "Answered"), ("conversations", "Conversations"))
    tabs = "".join(f'<button type="button" role="tab" id="tab-{name}" aria-controls="panel-{name}" '
                   f'aria-selected="{"true" if number == 0 else "false"}" tabindex="{0 if number == 0 else -1}">{label}</button>'
                   for number, (name, label) in enumerate(names))
    panels = "".join(f'<section class="lib-panel" role="tabpanel" id="panel-{name}" aria-labelledby="tab-{name}"'
                     f'{"" if number == 0 else " hidden"}>{content}</section>'
                     for number, (name, content) in enumerate(zip((n for n, _ in names), (references, answered, conversations))))
    return (f'<dialog id="library" class="library" aria-label="Library"><div class="lib-top">'
            f'<div role="tablist" aria-label="Library">{tabs}</div><button type="button" data-close>Close library</button></div>'
            f'{panels}</dialog>\n')


def desk_item(item: dict[str, object]) -> bool:
    """A question item stays on the Desk while a question is open or a non-closed question has pending delivery."""
    _, open_parts, pending = question_summary(item)
    return bool(open_parts or pending)


def workers_section(ready: bool) -> str:
    if not ready:
        return ('<section id="workers"><h2>Workers</h2><p class="empty">Workers show here once firstmate_home '
                'names a Firstmate home.</p></section>')
    return ('<section id="workers"><div class="section-head"><h2>Workers</h2>'
            '<button type="button" id="refresh-workers">Refresh workers</button></div>'
            '<p class="meta" id="workers-status" role="status">Loading workers</p><div id="workers-list"></div></section>')


def conversation_card(item, *, show_project=True):
    href, title = item_page_name(item["id"]), esc(item["title"])
    project = f' <span class="project-name">{esc(item["project"])}</span>' if show_project and item.get("project") else ""
    return (f'<article class="work"><div class="eyebrow"><span class="id">{item["id"]}</span> Conversation{project}</div>'
            f'<h3>{title}</h3>{tag_labels(list(item.get("tags", [])))}'
            f'<div class="actions"><span class="badge">Replied</span>'
            f'<a class="card-link button-link" href="{href}" aria-label="Read the reply in {title}">Read reply</a></div></article>')


def talk_form(binding):
    return ('<form id="talk-form" class="talk" hidden novalidate><label for="talk-topic">Topic</label>'
            f'<input id="talk-topic" maxlength="{TITLE_LIMIT}" autocomplete="off">'
            f'<label for="talk-message">Message</label><textarea id="talk-message" maxlength="{TEXT_LIMIT}" rows="3"></textarea>'
            f'<p id="talk-error" hidden></p><div class="actions"><button type="submit" class="primary" disabled '
            f'data-home="{esc(binding)}">Start conversation</button><output role="status" tabindex="0"></output></div></form>')


def list_page(data: dict[str, object], site_title: str, *, served: bool, saved_name: str = "index.html",
              workers: bool = False, ask_binding: str | None = None) -> str:
    items = newest_first(data["items"])  # type: ignore[arg-type]
    empty = "Nothing is saved yet. Ask Firstmate to send something to Quarterdeck, such as a link, a list or open questions."
    attention = []
    for item in items:
        if item["kind"] == "questions" and desk_item(item):
            summary = question_summary(item, replies=served)
            attention.append((item, summary))
        elif served and item["kind"] == "conversation" and thread_unread(item, None):
            attention.append((item, ("Replied", [], False)))
    # With more than one project on the Desk, its cards group by project, the group holding the newest item first.
    projects = list(dict.fromkeys(item.get("project", "") for item, _ in attention))
    grouped = len(projects) > 1
    primary = next((item for item, summary in attention if summary[1]), None)
    body = ""

    def desk(group, *, show_project: bool = True) -> str:
        cards = "".join(conversation_card(item, show_project=show_project) if item["kind"] == "conversation" else
                        question_card(item, summary, served=served, primary=item is primary, show_project=show_project)
                        for item, summary in group)
        return f'<div class="desk">{cards}</div>'

    heading = "<h1>Desk</h1>"
    if served and ask_binding:
        heading = ('<div class="page-head"><h1>Desk</h1><button type="button" id="open-talk" aria-expanded="false" '
                   'aria-controls="talk-form">Talk to Firstmate</button></div>' + talk_form(ask_binding))
    if attention:
        body += heading
        if grouped:
            for project in projects:
                members = [pair for pair in attention if pair[0].get("project", "") == project]
                body += f'<h2 class="group-name">{esc(project or "(No project)")}</h2>' + desk(members, show_project=False)
        else:
            body += desk(attention)
    elif not items:
        body = f'{heading}<p class="empty">{esc(empty)}</p>'
    else:
        body = f'{heading}<p class="empty">Nothing is on your Desk. A question that needs an answer or a reply shows here.</p>'
    if served:
        body += workers_section(workers)
    library = ""
    if served:
        library = library_dialog(data)
    elif items:
        references, answered = library_lists(data, served=False)
        body += (f'<section class="page-section"><h2>References</h2>{references}</section>'
                 f'<section class="page-section"><h2>Answered</h2>{answered}</section>')
    return page_shell(site_title, site_title, body, served=served, saved_name=saved_name, workers=served and workers,
                      library=library, filters=bool(items) and not served,
                      starting=served and bool(ask_binding))


def item_body_html(item: dict[str, object]) -> str:
    item_id, body, kind = str(item["id"]), item["body"], item["kind"]
    if kind == "resource":
        url = str(body["url"])  # type: ignore[index]
        return f'<p><a class="open-link" href="{esc(url)}" rel="noopener noreferrer">{esc(body["link_text"])}</a></p>'  # type: ignore[index]
    if kind == "list":
        entries = body["entries"]  # type: ignore[index]
        return '<ol class="numbered">' + "".join(
            f'<li id="{esc(item_id)}.{number}"><span class="id">{esc(item_id)}.{number}</span><span>{esc(entry)}</span></li>'
            for number, entry in enumerate(entries, 1)) + "</ol>"


def item_page(item: dict[str, object], site_title: str, *, served: bool, saved_name: str = "index.html",
              ask_binding: str | None = None) -> str:
    item_id = str(item["id"])
    questions = item["kind"] == "questions"
    talking = item["kind"] == "conversation"
    content = (question_form(item, served, ask_binding=ask_binding) if questions
               else conversation_page(item, ask_binding) if talking else item_body_html(item))
    lead = "" if talking else f'<p class="lead">{linked_text(item["description"])}</p>'
    body = (
        f'<p class="id">{esc(item_id)}</p><h1>{esc(item["title"])}</h1>'
        f'<p class="meta">{esc(KIND_LABELS[str(item["kind"])])}{" · " + esc(item["project"]) if item.get("project") else ""}'
        f' · saved <time class="day" datetime="{esc(item["saved_at"])}">{esc(saved_day(item))}</time></p>'
        f'{tag_labels(list(item.get("tags", [])))}'
        f'{lead}{content}'
    )
    return page_shell(f"{item_id} {item['title']} · {site_title}", site_title, body,
                      served=served, saved_name=saved_name,
                      interactive=served and has_answer_controls(item),
                      conversations=served and (questions or talking), talking=served and talking)


def site_pages(data: dict[str, object], site_title: str, saved_name: str) -> dict[str, str]:
    items = [item for item in data["items"] if item["kind"] != "conversation"]  # type: ignore[union-attr]
    pages = {}
    for item in items:
        pages[item_page_name(str(item["id"]))] = item_page(item, site_title, served=False, saved_name=saved_name)
    pages[saved_name] = list_page({**data, "items": items}, site_title, served=False, saved_name=saved_name)
    return pages


# --- render -----------------------------------------------------------------------------


def default_output_dir(config: dict[str, object]) -> Path:
    configured = config.get("output_dir")
    return Path(configured).expanduser() if isinstance(configured, str) else state_dir()


def copy_images(items: list[dict[str, object]], store: Path, root: Path, *, live: bool) -> None:
    """Check images the exported pages use and copy them when rendering outside the state folder."""
    for item in items:
        files = item_image_files(item)
        if not files:
            continue
        item_id = str(item["id"])
        directory = None if live else image_directory(root, item_id)
        for file in sorted(files):
            with stored_image(store, item_id, file).open("rb") as stream:
                if directory is not None:
                    directory.mkdir(parents=True, exist_ok=True)
                    place_image(directory / file, stream.read())


def generated_page(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return GENERATED_MARKER in stream.read(4096)
    except OSError:
        return False


def retire_stale_pages(directory: Path, keep: set[str]) -> None:
    for path in directory.glob("q*.html"):
        if path.name not in keep and ITEM_PAGE_RE.fullmatch(path.name) and generated_page(path):
            path.unlink()


def render(args: argparse.Namespace) -> int:
    try:
        config = selected_config(args)
        output = Path(args.output).expanduser() if args.output else default_output_dir(config) / "index.html"
        output = output.parent.resolve() / output.name
        checkout = Path(__file__).resolve().parent
        if output.parent == checkout or checkout in output.parents:
            raise ValueError("the rendered page must be outside the Quarterdeck checkout")
        store = store_path()
        root = output.parent / "images"
        private_images = store.parent.resolve() / REPLY_IMAGES
        if private_images in output.parents:
            raise ValueError("the rendered page must be outside the private reply image tree")
        live = output.parent == store.parent.resolve()
        protected = {store.resolve(), store.with_name(store.name + ".lock").resolve()}
        if ITEM_PAGE_RE.fullmatch(output.name.lower()):
            raise ValueError("the Saved filename is reserved for item pages")
        if output.name.lower() == "images" or (live and output.name.lower() == REPLY_IMAGES):
            raise ValueError(f"the Saved filename is reserved for {output.name.lower()}")
        with locked_store(store, read_only=not live):
            data = read_store(store)
            pages = site_pages(data, page_title(args, config), output.name)
            for name in pages:
                destination = output.parent / name
                if destination in protected:
                    raise ValueError("the rendered page would overwrite the item store or its lock")
                if (destination.exists() or destination.is_symlink()) and not generated_page(destination):
                    raise ValueError(f"refusing to overwrite an unrelated file: {destination}")
            copy_images(data["items"], store, root, live=live)  # type: ignore[arg-type]
            for name, content in pages.items():
                write_private(output.parent / name, content)
            retire_stale_pages(output.parent, set(pages))
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(output)
    return 0


# --- serve ------------------------------------------------------------------------------

# A phone can pin the served page when the page names an icon and a manifest. Both are generated here.
APP_NAME = "Quarterdeck"
APP_NAVY, APP_GOLD, APP_SAND = "#17324d", "#c98635", "#f3eddf"
APP_ICON_PATHS = {"icon.svg": "image/svg+xml", "icon-180.png": "image/png", "icon-192.png": "image/png",
                  "icon-512.png": "image/png", "manifest.webmanifest": "application/manifest+json"}
APP_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" fill="' + APP_NAVY + '"/>'
    '<g fill="none" stroke="' + APP_GOLD + '" stroke-width="28" stroke-linecap="round">'
    '<circle cx="256" cy="133" r="29"/><path d="M256 162V380M184 205H328M102 282A154 154 0 0 0 410 282"/></g></svg>'
)
GZIP_MIN_BYTES = 1024
TLS_HANDSHAKE_SECONDS = 15


def anchor_icon_png(size: int) -> bytes:
    """A gold anchor on navy, drawn without any image library: one distance test per sub-pixel."""
    ring, shaft_top, shaft_bottom, bar_y, bar_half = (0.5, 0.26), 0.31, 0.74, 0.40, 0.14
    arc_center, arc_radius, arc_start, half_width = (0.5, 0.55), 0.20, 0.55, 0.0275

    def segment(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
        dx, dy = x1 - x0, y1 - y0
        t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
        return ((px - x0 - t * dx) ** 2 + (py - y0 - t * dy) ** 2) ** 0.5

    def gold(px: float, py: float) -> bool:
        if abs(((px - ring[0]) ** 2 + (py - ring[1]) ** 2) ** 0.5 - 0.058) <= half_width:
            return True
        if segment(px, py, 0.5, shaft_top, 0.5, shaft_bottom) <= half_width:
            return True
        if segment(px, py, 0.5 - bar_half, bar_y, 0.5 + bar_half, bar_y) <= half_width:
            return True
        return py >= arc_start and abs(((px - arc_center[0]) ** 2 + (py - arc_center[1]) ** 2) ** 0.5 - arc_radius) <= half_width

    samples = 2 if size <= 192 else 1
    back, front = (0x17, 0x32, 0x4D), (0xC9, 0x86, 0x35)
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            share = sum(gold((x + (i + .5) / samples) / size, (y + (j + .5) / samples) / size)
                        for i in range(samples) for j in range(samples)) / (samples * samples)
            row.extend(round(b + (f - b) * share) for b, f in zip(back, front))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return len(data).to_bytes(4, "big") + body + (zlib.crc32(body) & 0xFFFFFFFF).to_bytes(4, "big")

    header = size.to_bytes(4, "big") * 2 + bytes([8, 2, 0, 0, 0])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def app_manifest() -> str:
    icons = [{"src": f"icon-{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any maskable"}
             for size in (192, 512)]
    icons.append({"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"})
    return json.dumps({
        "name": APP_NAME, "short_name": APP_NAME, "description": "What you asked Firstmate to save.",
        "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": APP_SAND, "theme_color": APP_NAVY, "icons": icons,
    }, indent=2) + "\n"


@functools.lru_cache(maxsize=None)
def app_asset(name: str) -> bytes | None:
    """The bytes of one pinning asset, or None when the name is not one of them."""
    if name == "icon.svg":
        return APP_ICON_SVG.encode("utf-8")
    if name == "manifest.webmanifest":
        return app_manifest().encode("utf-8")
    sizes = {"icon-180.png": 180, "icon-192.png": 192, "icon-512.png": 512}
    return anchor_icon_png(sizes[name]) if name in sizes else None


def accepts_gzip(header: str | None) -> bool:
    """True when Accept-Encoding offers gzip with a quality above zero."""
    for part in (header or "").split(","):
        coding, _, params = part.partition(";")
        if coding.strip().lower() in {"gzip", "x-gzip"}:
            quality = re.search(r"q\s*=\s*([0-9.]+)", params)
            try:
                return float(quality[1]) > 0 if quality else True
            except ValueError:
                return False
    return False




def request_host_is_loopback(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse("//" + value.strip())
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return False
        host = parsed.hostname
        if not host:
            return False
        if host.rstrip(".").lower() == "localhost":
            return True
        address = ipaddress.ip_address(host)
        mapped_address = getattr(address, "ipv4_mapped", None)
        return address.is_loopback or bool(mapped_address and mapped_address.is_loopback)
    except ValueError:
        return False


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def base_path_option(value: str) -> str:
    try:
        return normalize_base_path(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def effective_base_path(args: argparse.Namespace) -> str:
    """--base-path when given (even as "" to switch a configured prefix off), else the config's base_path, else none."""
    given = getattr(args, "base_path", None)
    if given is not None:
        return normalize_base_path(given)
    return normalize_base_path(selected_config(args).get("base_path"))


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        mapped_address = getattr(address, "ipv4_mapped", None)
        return address.is_loopback or bool(mapped_address and mapped_address.is_loopback)
    except ValueError:
        return False


def bind_hosts(args: argparse.Namespace) -> list[str]:
    return list(dict.fromkeys(args.host or [DEFAULT_HOST]))


def tls_context(args: argparse.Namespace) -> ssl.SSLContext | None:
    """The server's TLS settings from --tls-cert and --tls-key, or None to serve plain HTTP."""
    cert, key = getattr(args, "tls_cert", None), getattr(args, "tls_key", None)
    if not cert and not key:
        return None
    if not (cert and key):
        raise ValueError("--tls-cert and --tls-key go together")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(str(Path(cert).expanduser()), str(Path(key).expanduser()))
    except ssl.SSLError as exc:
        raise ValueError(f"could not load the TLS certificate and key: {exc}") from exc
    return context


def make_server(host: str, port: int, handler: type[BaseHTTPRequestHandler],
                tls: ssl.SSLContext | None = None) -> ThreadingHTTPServer:
    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        daemon_threads = True

        def server_bind(self) -> None:
            if self.address_family == socket.AF_INET6:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            super().server_bind()

        def finish_request(self, request, client_address) -> None:  # noqa: ANN001 - socketserver signature
            if tls is None:
                super().finish_request(request, client_address)
                return
            # The handshake runs in the request's own thread, so a slow or plain-HTTP client never holds up the others.
            request.settimeout(TLS_HANDSHAKE_SECONDS)
            try:
                secure = tls.wrap_socket(request, server_side=True)
            except (ssl.SSLError, OSError):
                return
            secure.settimeout(None)
            try:
                super().finish_request(secure, client_address)
            finally:
                secure.close()

    return Server((host, port), handler)


def make_handler(site_title: str, base_path: str = "", store: Path | None = None, home: Path | None = None,
                 *, ask_home: Path | None = None, workers_home: Path | None = None) -> type[BaseHTTPRequestHandler]:
    """Every page is built from the item store when it is requested."""
    def ask_destination():
        if ask_home is not None:
            try:
                resolved = ask_home.resolve()
            except (OSError, RuntimeError):
                return None, None
            return resolved, hashlib.sha256(os.fsencode(resolved)).hexdigest()
        return None, None

    class Handler(BaseHTTPRequestHandler):
        server_version = "Quarterdeck"

        def send_text(self, status: int, body: str | bytes, content_type: str = "text/plain; charset=utf-8",
                      cache: str = "no-store", *, prevent_framing: bool = False) -> None:
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            packed = (len(data) >= GZIP_MIN_BYTES and accepts_gzip(self.headers.get("Accept-Encoding"))
                      and not content_type.startswith("image/"))
            if packed:
                data = gzip.compress(data, compresslevel=6, mtime=0)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if packed:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if prevent_framing:
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def send_image(self, data: dict[str, object], tree: str, item_id: str, file: str) -> None:
            """Require the item to list the file for the requested private image tree."""
            try:
                item = find_item(data, item_id)
                if file not in item_image_files(item, tree):
                    raise ValueError("not an image of this item")
                content = stored_image(store or store_path(), item_id, file, tree).read_bytes()
            except (OSError, ValueError):
                self.send_text(404, "Not found\n")
                return
            self.send_text(200, content, IMAGE_TYPES[file.rsplit(".", 1)[1]])

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            if is_loopback(str(self.server.server_address[0])) and not request_host_is_loopback(self.headers.get("Host")):
                self.send_text(403, "Host is not allowed\n")
                return
            try:
                request = urlparse(self.path)
            except ValueError:
                self.send_text(400, "Invalid request path\n")
                return
            requested = unquote(request.path)
            path = strip_base_path(requested, base_path)
            if path is None:
                query = request.query
                target = base_path + ("/" if requested == base_path else request.path)
                self.send_response(308)
                self.send_header("Location", target + ("?" + query if query else ""))
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            name = "index.html" if path == "/" else path.removeprefix("/")
            if name in APP_ICON_PATHS:
                self.send_text(200, app_asset(name) or b"", APP_ICON_PATHS[name], cache="public, max-age=86400")
                return
            if name == "workers.json" and workers_home is not None:
                try:
                    snapshot = workers_snapshot(workers_home)
                except SnapshotError:
                    self.send_text(502, json.dumps({"error": "The workers could not be read."}), "application/json")
                else:
                    self.send_text(200, json.dumps(snapshot), "application/json")
                return
            picture = IMAGE_URL_RE.fullmatch(name)
            if name != "index.html" and not picture and not ITEM_PAGE_RE.fullmatch(name):
                self.send_text(404, "Not found\n")
                return
            try:
                data = read_store(store or store_path())
            except ValueError:
                self.send_text(500, "Quarterdeck could not read its item store\n")
                return
            if picture:
                self.send_image(data, *picture.groups())
                return
            if name == "index.html":
                _, ask_binding = ask_destination()
                self.send_text(200, list_page(data, site_title, served=True, workers=workers_home is not None,
                                              ask_binding=ask_binding), "text/html; charset=utf-8",
                               prevent_framing=bool(ask_binding))
                return
            try:
                item = find_item(data, name.removesuffix(".html"))
            except ValueError:
                self.send_text(404, "Not found\n")
                return
            _, ask_binding = ask_destination() if item["kind"] in ("questions", "conversation") else (None, None)
            content = item_page(item, site_title, served=True, ask_binding=ask_binding)
            prevent_framing = has_answer_controls(item) or item["kind"] == "conversation" or (
                item["kind"] == "questions" and question_summary(item)[0] == "Replied")
            self.send_text(200, content, "text/html; charset=utf-8", prevent_framing=prevent_framing)

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            endpoint = self.path.removeprefix(base_path)
            subjects = {"/answers": "answer", "/asks": "ask", "/conversations": "conversation", "/replies/read": "reply acknowledgment"}
            if not self.path.startswith(base_path) or endpoint not in subjects:
                self.send_text(405, "Use the question page.")
                return
            subject = subjects[endpoint]
            host = self.headers.get("Host", "")
            https = isinstance(self.connection, ssl.SSLSocket) or self.headers.get_all("X-Forwarded-Proto") == ["https"]
            scheme = "https" if https else "http"
            if (not host or self.headers.get("Origin") != f"{scheme}://{host}"
                    or (is_loopback(str(self.server.server_address[0])) and not request_host_is_loopback(host))):
                self.send_text(403, "Open the question on Quarterdeck.")
                return
            if endpoint in ("/asks", "/conversations"):
                ask_path, ask_binding = ask_destination()
                if ask_path is None:
                    self.send_text(403, "Configure an available firstmate_home to ask Firstmate.")
                    return
            if self.headers.get_content_type() != "application/json":
                self.send_text(415, f"Expected {subject} data in JSON.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 256000 or "Transfer-Encoding" in self.headers:
                    raise ValueError("Invalid request size.")
                payload = json.loads(self.rfile.read(length))
                active_store = store or store_path()
                if endpoint == "/answers":
                    record = send_answer(active_store, home, payload)
                elif endpoint == "/asks":
                    record = ask_message(active_store, ask_path, ask_binding, payload)
                elif endpoint == "/conversations":
                    record = start_conversation(active_store, ask_path, ask_binding, payload)
                else:
                    record = mark_replies_read(active_store, payload)
            except AnswerConflict as exc:
                if endpoint in ("/asks", "/conversations"):
                    record = {"message": str(exc), "saved": isinstance(exc, SavedAskConflict)}
                    self.send_text(409, json.dumps(record), "application/json")
                else:
                    self.send_text(409, str(exc))
            except (OSError, StoreReadError):
                self.send_text(500, f"Could not save the {subject}.")
            except (ValueError, RecursionError):
                self.send_text(400, f"Check the question and {subject}.")
            else:
                self.send_text(200, json.dumps(record), "application/json")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server signature
            sys.stderr.write("quarterdeck: %s - %s\n" % (self.address_string(), format % args))

    return Handler


LEGACY_SERVE_FLAGS = ("allow_marks", "watch_waits", "no_snapshot", "home")


def answer_home(config: dict[str, object], config_path: Path | None) -> Path | None:
    explicit = config.get("firstmate_home")
    if isinstance(explicit, str):
        return Path(explicit)
    homes = config.get("homes")
    if config_path is not None and isinstance(homes, list) and len(homes) == 1 and isinstance(homes[0], dict):
        try:
            return (config_path.resolve().parent / inbox_path(homes[0].get("path"))).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
    return None


def serve(args: argparse.Namespace) -> int:
    servers: list[ThreadingHTTPServer] = []
    try:
        config_path = selected_config_path(args)
        config = load_config(config_path)
        tls = tls_context(args)
        base_path = effective_base_path(args)
        explicit_home = config.get("firstmate_home")
        firstmate = Path(explicit_home) if isinstance(explicit_home, str) else None
        handler = make_handler(page_title(args, config), base_path, home=answer_home(config, config_path),
                               ask_home=firstmate, workers_home=firstmate)
        port = args.port
        for host in bind_hosts(args):
            server = make_server(host, port, handler, tls)
            port = server.server_address[1]
            servers.append(server)
    except (OSError, ValueError) as exc:
        for server in servers:
            server.server_close()
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    ignored = [flag for flag in LEGACY_SERVE_FLAGS if getattr(args, flag, None)]
    if ignored:
        print("quarterdeck: ignoring " + ", ".join("--" + flag.replace("_", "-") for flag in ignored)
              + "; Quarterdeck shows items saved by hand and, when firstmate_home is set, the workers in that home", file=sys.stderr, flush=True)
    for server in servers:
        host = str(server.server_address[0])
        shown = f"[{host}]" if ":" in host else host
        print(f"Serving Quarterdeck at {'https' if tls else 'http'}://{shown}:{port}{base_path}/ (Ctrl+C to stop)",
              flush=True)
    exposed = [host for host in bind_hosts(args) if not is_loopback(host)]
    if exposed:
        print(f"quarterdeck: warning: {', '.join(exposed)} is not a loopback address; anyone who can reach this port can "
              "read saved items, answers, conversations and images, submit answers, and acknowledge replies. With "
              "firstmate_home set, they can send asks, start conversations and send messages.",
              file=sys.stderr, flush=True)
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            if server is not servers[0]:
                server.shutdown()
            server.server_close()
    return 0


# --- service and url --------------------------------------------------------------------


def systemd_quote(value: str, *, literal_dollars: bool = False) -> str:
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError("systemd values cannot contain control characters")
    if literal_dollars:
        value = value.replace("$", "$$")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_unit(args: argparse.Namespace) -> str:
    command = [sys.executable, str(Path(__file__).resolve()), "serve"]
    for host in bind_hosts(args):
        command += ["--host", host]
    command += ["--port", str(args.port)]
    if getattr(args, "base_path", None) is not None:
        command += ["--base-path", args.base_path]
    tls_cert, tls_key = getattr(args, "tls_cert", None), getattr(args, "tls_key", None)
    if bool(tls_cert) != bool(tls_key):
        raise ValueError("--tls-cert and --tls-key go together")
    if tls_cert and tls_key:
        command += ["--tls-cert", str(Path(tls_cert).expanduser().resolve()), "--tls-key", str(Path(tls_key).expanduser().resolve())]
    config_path = args.config.expanduser().resolve() if args.config else default_config_path()
    if args.config or config_path.is_file():
        load_config(config_path)
        command += ["--config", str(config_path)]
    exec_start = " ".join(systemd_quote(part, literal_dollars=True) for part in command)
    environment = ""
    if os.environ.get("XDG_STATE_HOME"):
        state_home = Path(os.environ["XDG_STATE_HOME"]).expanduser().resolve()
        environment = "Environment=" + systemd_quote("XDG_STATE_HOME=" + str(state_home)) + "\n"
    return (
        "[Unit]\nDescription=Quarterdeck page of saved items\nAfter=network.target\n\n"
        f"[Service]\n{environment}ExecStart={exec_start}\nRestart=on-failure\nRestartSec=5\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def service(args: argparse.Namespace) -> int:
    try:
        unit = service_unit(args)
        if not args.write:
            sys.stdout.write(unit)
            return 0
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
        unresolved_unit_path = config_home / "systemd" / "user" / "quarterdeck.service"
        if unresolved_unit_path.is_symlink():
            raise ValueError(f"refusing to replace symbolic-link unit: {unresolved_unit_path}")
        unit_path = unresolved_unit_path.parent.resolve() / unresolved_unit_path.name
        checkout = Path(__file__).resolve().parent
        if unit_path.parent == checkout or checkout in unit_path.parents:
            raise ValueError("the unit file must be outside the Quarterdeck checkout")
        write_private(unit_path, unit)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {unit_path}")
    print("Start it with: systemctl --user daemon-reload && systemctl --user enable --now quarterdeck.service")
    exposed = [host for host in bind_hosts(args) if not is_loopback(host)]
    if exposed:
        print(f"quarterdeck: warning: {', '.join(exposed)} is not a loopback address; anyone who can reach the port can "
              "read your saved items and send answers as the owner.", file=sys.stderr)
    return 0


def installed_unit_command() -> tuple[list[str], str] | None:
    """The installed service's serve arguments and the unit's path, or None when no unit is installed."""
    unit_path = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "systemd/user/quarterdeck.service"
    if not unit_path.is_file():
        return None
    match = re.search(r"(?m)^ExecStart=(.*)$", unit_path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"installed service unit has no ExecStart: {unit_path}")
    try:
        command = shlex.split(match.group(1).replace("%%", "%"))
    except ValueError as exc:
        raise ValueError(f"could not read installed service unit: {exc}") from exc
    try:
        return command[command.index("serve") + 1:], str(unit_path)
    except ValueError as exc:
        raise ValueError("installed service unit does not run quarterdeck serve") from exc


def url(args: argparse.Namespace) -> int:
    try:
        installed = installed_unit_command()
        options = (installed or ([], ""))[0]
        hosts = [options[i + 1] for i in range(len(options) - 1) if options[i] == "--host"] or [DEFAULT_HOST]
        values = {options[i]: options[i + 1] for i in range(len(options) - 1) if options[i] in {"--port", "--base-path", "--config"}}
        port = port_number(values.get("--port", str(DEFAULT_PORT)))
        base = effective_base_path(argparse.Namespace(
            base_path=values.get("--base-path"), config=Path(values["--config"]) if "--config" in values else None))
        scheme = "https" if "--tls-cert" in options else "http"
    except (OSError, ValueError, argparse.ArgumentTypeError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    hosts = list(dict.fromkeys(hosts))
    addresses = [f"{scheme}://[{host.replace('%', '%25', 1)}]:{port}{base}/" if ":" in host
                 else f"{scheme}://{host}:{port}{base}/" for host in hosts]
    source = f"installed service unit {installed[1]}" if installed else "default"
    if args.as_json:
        result: dict[str, object] = {"urls": addresses, "hosts": hosts, "source": source}
        if not installed:
            result["note"] = ("No installed Quarterdeck service was found. A plain `quarterdeck serve` listens on the "
                              "default address shown.")
        print(json.dumps(result, indent=2))
        return 0
    for address in addresses:
        print(address)
    if installed:
        print(f"Source: {source}")
    else:
        print("Source: default serve settings (no installed Quarterdeck service was found).")
        print("Run `quarterdeck serve` to start the page, or `quarterdeck service` to configure a systemd user service.")
    return 0


# --- install, update and help -------------------------------------------------------------

COMMAND_DOCS = {
    "mark": ["how-to/read-answers.md", "how-to/use-the-library.md", "reference/item-store.md"],
    "classify": ["how-to/classify-items.md", "reference/cli-and-config.md", "reference/item-store.md"],
    "stats": ["how-to/read-the-report.md", "reference/cli-and-config.md"],
    "add": ["how-to/save-items.md", "how-to/classify-items.md", "reference/cli-and-config.md", "reference/item-store.md"],
    "list": ["how-to/save-items.md", "reference/cli-and-config.md"],
    "show": ["how-to/read-answers.md", "how-to/save-items.md", "reference/cli-and-config.md"],
    "reply": ["how-to/reply-from-the-command-line.md", "how-to/talk-to-firstmate.md", "reference/cli-and-config.md", "reference/item-store.md"],
    "remove": ["how-to/save-items.md", "reference/cli-and-config.md"],
    "render": ["how-to/serve-the-page.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "serve": ["how-to/answer-questions.md", "how-to/use-the-library.md", "how-to/ask-firstmate.md", "how-to/talk-to-firstmate.md", "how-to/read-workers.md", "how-to/serve-the-page.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "service": ["how-to/serve-the-page.md", "reference/cli-and-config.md"],
    "url": ["how-to/serve-the-page.md", "reference/cli-and-config.md"],
    "install": ["how-to/install.md", "how-to/update.md", "how-to/uninstall.md"],
    "update": ["how-to/update.md", "reference/cli-and-config.md"],
    "help": ["README.md", "reference/cli-and-config.md"],
}


def run_installer(args: argparse.Namespace) -> int:
    installer = Path(__file__).resolve().with_name("install.sh")
    if not installer.is_file():
        print(f"quarterdeck: installer script not found: {installer}", file=sys.stderr)
        return 2
    command = ["sh", str(installer)] + (["--uninstall"] if args.uninstall else [])
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"quarterdeck: could not run installer: {exc}", file=sys.stderr)
        return 2


def show_help(args: argparse.Namespace) -> int:
    docs_root = (Path(__file__).resolve().parent / "docs").resolve()
    if args.as_json:
        commands = [{"name": name, "usage": command_parser.format_usage().strip(),
                     "summary": command_parser.description or command_parser.prog, "docs": COMMAND_DOCS.get(name, [])}
                    for name, command_parser in args.command_parsers.items()]
        print(json.dumps({"program": "quarterdeck", "docs_root": str(docs_root), "commands": commands}, indent=2))
        return 0
    if args.topic:
        command_parser = args.command_parsers.get(args.topic)
        if command_parser is None:
            print(f"quarterdeck: unknown help topic: {args.topic}", file=sys.stderr)
            return 2
        command_parser.print_help()
        doc_paths = COMMAND_DOCS.get(args.topic, [])
    else:
        args.root_parser.print_help()
        doc_paths = ["how-to/save-items.md", "how-to/install.md", "reference/cli-and-config.md"]
    print(f"\nDocumentation tree: {docs_root}")
    if doc_paths:
        print("Read: " + ", ".join(str(docs_root / item) for item in doc_paths))
    print("Machine-readable command index: quarterdeck help --json")
    return 0


# --- command line -------------------------------------------------------------------------

KIND_HELP = ("resource (a link: --url and --link-text), list (one entry per non-blank line), "
             "questions (a JSON array of {question, context, options, suggestion, links, images, kind, tags}; "
             "links is a list of {text, url} shown above the options; an option may be "
             "{label, image}, and an image is {path, alt}, copied into the private store when saved)")


def add_serve_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", action="append", metavar="ADDRESS", help=f"address to bind; repeat to listen on several, all on the same port (default {DEFAULT_HOST} only; a non-loopback address allows readers to send answers as the owner)")
    parser.add_argument("--port", type=port_number, default=DEFAULT_PORT, help=f"port to listen on (default {DEFAULT_PORT})")
    parser.add_argument("--base-path", type=base_path_option, metavar="PATH", help="URL path prefix when a reverse proxy serves Quarterdeck under one, such as /qd; overrides base_path in config")
    parser.add_argument("--tls-cert", metavar="FILE", help="serve https with this PEM certificate chain (with --tls-key)")
    parser.add_argument("--tls-key", metavar="FILE", help="the PEM private key for --tls-cert")
    parser.add_argument("--config", type=Path, help="optional JSON config file")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quarterdeck",
        description="Keep a page of things saved by hand: links, lists and open questions. Each item gets an ID "
                    "(Q1, Q2, ...) that is never reused; its numbered parts are cited as Q7.3.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command_parsers: dict[str, argparse.ArgumentParser] = {}

    def command(name: str, help_text: str, description: str, handler) -> argparse.ArgumentParser:  # noqa: ANN001
        sub = subparsers.add_parser(name, help=help_text, description=description)
        sub.set_defaults(handler=handler)
        command_parsers[name] = sub
        return sub

    add = command("add", "save a new item and print its ID",
                  "Save a new item and print its ID. The body is copied in now; Quarterdeck never reads it again "
                  f"from anywhere else. Kinds: {KIND_HELP}.", add_item)
    add.add_argument("--kind", required=True, choices=ADD_KINDS, help="what the item is")
    add.add_argument("--title", required=True, help="a short title that says what it is about")
    add.add_argument("--description", required=True, help="one or two sentences: what it is and why it was saved")
    add.add_argument("--url", help="a resource's http(s) address")
    add.add_argument("--link-text", help="a resource's link text")
    add.add_argument("--project", help="the item's project name or owner/name repository")
    add.add_argument("--tag", dest="tags", action="append", metavar="TAG",
                     help=f"a lowercase topic tag, repeatable, up to {TAG_LIMIT}; allowed: {', '.join(CLASSIFICATION['tag'])}")
    add.add_argument("--body-file", metavar="PATH", help="the body of a list or questions; - reads standard input. Questions may carry PNG, JPEG or still WebP images "
                                                          "(up to 5 MB each) that are copied in when saved")

    command("list", "list saved items, newest first", "List saved items, newest first.", list_items)

    show = command("show", "show one item in full", "Show one item in full, with its parts numbered (Q7.1, Q7.2, ...).", show_item)
    show.add_argument("id", help="item ID, such as Q7")

    reply = command("reply", "add Firstmate's reply to an ask or a conversation message",
                    "Add Firstmate's reply under an ask on a question item (Q7) or one question (Q7.1), or under a message in a "
                    "conversation started with Talk to Firstmate (an inbox note that begins CONVERSATION; reply to its item ID, "
                    "such as Q12, with its message ID). The reply shows as Replied until the owner opens it. A reply leaves answers "
                    "and states intact. Pair each --link-text with a --url and each --image with an --alt, in the order given.", reply_item)
    reply.add_argument("id", help="item or question ID, such as Q7 or Q7.1; a conversation's item ID, such as Q12")
    reply.add_argument("--ask", required=True, metavar="ID", help="the ask or message ID from the inbox note")
    reply.add_argument("--text", required=True, help="the reply")
    reply.add_argument("--link-text", action="append", metavar="TEXT", help="the words of a link; repeat for more links")
    reply.add_argument("--url", action="append", metavar="URL", help="the http(s) address for the matching --link-text")
    reply.add_argument("--image", action="append", metavar="PATH", help="a PNG, JPEG or still WebP file (up to 5 MB) copied into the private store; repeat for more")
    reply.add_argument("--alt", action="append", metavar="TEXT", help="alt text that describes the matching --image")

    mark = command("mark", "mark questions answered, closed or open",
                   "Set the state of a question (Q7.3) or every question in an item (Q7). Marking answered records an "
                   "answer given somewhere other than the page, requires --via, and skips questions that are already "
                   "answered or closed.", mark_item)
    mark.add_argument("id", help="question or item ID")
    mark.add_argument("state", choices=("answered", "closed", "open"))
    mark.add_argument("--via", metavar="WHERE", help=f"with answered: lowercase answer source ({', '.join(CLASSIFICATION['via'])})")

    classify = command("classify", "set an item's or question's classification",
                       "Set or replace the project and tags of an item (Q7), or the kind and tags of one question (Q7.3), "
                       "without touching its content or answers. Options you leave out stay as they are.", classify_item)
    classify.add_argument("id", help="item ID such as Q7, or question ID such as Q7.3")
    classify.add_argument("--project", help="the item's project name or owner/name repository (item IDs only)")
    classify.add_argument("--tag", dest="tags", action="append", metavar="TAG",
                          help=f"a lowercase tag, repeatable, up to {TAG_LIMIT}; replaces the tags already there")
    classify.add_argument("--kind", help=f"a question's lowercase kind (question IDs only; allowed: {', '.join(CLASSIFICATION['kind'])})")

    stats = command("stats", "count questions by kind, tag and project",
                    "Print, by kind, by tag and by project: questions saved, answered on the Desk, answered elsewhere "
                    "(by source), still open, closed, and the median wait for answers given on the Desk.", show_stats)
    stats.add_argument("--since", metavar="DATE", help="include saves and dated answers on or after this UTC date, such as 2026-11-01")
    stats.add_argument("--json", dest="as_json", action="store_true",
                       help="print structured counts, including the off-Desk total, and median waits in seconds")

    remove = command("remove", "delete an item", "Delete an item. Its ID is never given to another item.", remove_item)
    remove.add_argument("id", help="item ID, such as Q7")

    render_parser = command("render", "write the page to HTML files",
                            "Write HTML pages for the Desk, resources, lists and question items.", render)
    render_parser.add_argument("--output", help="HTML file path for the Desk page; the other pages go beside it")
    render_parser.add_argument("--config", type=Path, help="optional JSON config file")
    render_parser.add_argument("--title", help="override the page title")

    serve_parser = command("serve", "serve the page over HTTP",
                           "Anyone who can reach the service can read saved items, answers, conversations and reply images, "
                           "and can submit answers or acknowledge replies as the owner. With firstmate_home set, they can "
                           "also send asks, start conversations, send messages and read that home's running workers "
                           "on entry or on Refresh workers. Browser "
                           "writes require the same origin as the served page.", serve)
    add_serve_options(serve_parser)
    serve_parser.add_argument("--title", help="override the page title")
    for flag in LEGACY_SERVE_FLAGS:  # accepted so an existing service unit keeps starting; they do nothing now
        if flag == "home":
            serve_parser.add_argument("--home", help=argparse.SUPPRESS)
        else:
            serve_parser.add_argument("--" + flag.replace("_", "-"), action="store_true", help=argparse.SUPPRESS)

    service_parser = command("service", "print or write a systemd user unit",
                             "Print a systemd user unit that runs quarterdeck serve, or write it with --write.", service)
    add_serve_options(service_parser)
    service_parser.add_argument("--write", action="store_true", help="write the unit file instead of printing it")

    url_parser = command("url", "print the page's address", "Print the address the installed service serves.", url)
    url_parser.add_argument("--json", dest="as_json", action="store_true", help="print URLs and their source as JSON")

    install = command("install", "install or uninstall Quarterdeck", "Run the user-local shell installer.", run_installer)
    install.add_argument("--uninstall", action="store_true", help="remove installer-created files and keep config and data")
    update = command("update", "update an installed Quarterdeck",
                     "Run the user-local shell installer to update the installed checkout.", run_installer)
    update.set_defaults(uninstall=False)

    help_parser = command("help", "show command and documentation help", "Discover commands and documentation.", show_help)
    help_parser.add_argument("topic", nargs="?", help="show help for one command")
    help_parser.add_argument("--json", dest="as_json", action="store_true", help="print a machine-readable command index")
    help_parser.set_defaults(root_parser=parser, command_parsers=command_parsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
