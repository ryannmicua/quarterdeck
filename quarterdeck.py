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
import time
import unicodedata
import uuid
import zlib
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse


DEFAULT_TITLE = "Quarterdeck"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
ALLOWED_CONFIG_KEYS = {"page_title", "output_dir", "base_path", "firstmate_home", "catch_up_days"}
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
    if "catch_up_days" in config:
        days = config["catch_up_days"]
        if type(days) is not int or not 1 <= days <= 3650:
            raise ValueError("config catch_up_days must be a whole number of days from 1 to 3650")
    return config


def catch_up_days(config: dict[str, object]) -> int:
    return config.get("catch_up_days", CATCH_UP_DAYS)  # type: ignore[return-value]


def selected_config_path(args: argparse.Namespace) -> Path | None:
    """The --config file, else the default config when it exists, else no config."""
    path = getattr(args, "config", None)
    if path is None:
        default_path = default_config_path()
        path = default_path if default_path.is_file() else None
    return path.expanduser() if path else None


def selected_config(args: argparse.Namespace) -> dict[str, object]:
    return load_config(selected_config_path(args))


def configured_firstmate_home(config: dict[str, object]) -> Path | None:
    home = config.get("firstmate_home")
    return Path(home) if isinstance(home, str) else None


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


# Store item bodies by value so saved content survives changes to the original inputs.
# An ID is never reused, even after its item is removed.

STORE_VERSION = 1
KIND_LABELS = {"resource": "Resource", "list": "List", "questions": "Questions", "conversation": "Conversation"}
KINDS = tuple(KIND_LABELS)
# The Desk saves a conversation together with its first message.
ADD_KINDS = tuple(kind for kind in KINDS if kind != "conversation")
ID_RE = re.compile(r"[Qq][1-9][0-9]*")
ITEM_PAGE_RE = re.compile(r"q[1-9][0-9]*[.]html")
TITLE_LIMIT = 200
DESCRIPTION_LIMIT = 600
TEXT_LIMIT = 20_000


CATCH_UP_DAYS = 7
CATCH_UP_ID_RE = re.compile(r"C[1-9][0-9]*")
CATCH_UP_SUMMARY_LIMIT = 300


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


def resource_links(body: dict[str, object]) -> list[dict[str, str]]:
    """A resource's links in order. The older single-link body reads as a list of one."""
    if "links" in body:
        if set(body) != {"links"}:
            raise ValueError("a resource has either url and link_text or links, not both")
        links = question_links(body["links"], "resource")
        if not links:
            raise ValueError("a resource needs at least one link")
        return links
    if set(body) != {"url", "link_text"}:
        raise ValueError("a resource needs a url and link_text, or a list of links")
    return [{"text": clean_text(body.get("link_text"), "link text", TITLE_LIMIT), "url": clean_url(body.get("url"))}]


def resource_body(body: dict[str, object]) -> dict[str, object]:
    """One link keeps the original body shape so saved items need no rewrite; more links use a links list."""
    links = resource_links(body)
    if len(links) == 1:
        return {"url": links[0]["url"], "link_text": links[0]["text"]}
    return {"links": links}


def paired_links(texts: list[str] | None, urls: list[str] | None) -> list[dict[str, str]]:
    texts, urls = texts or [], urls or []
    if len(texts) != len(urls):
        raise ValueError("each --link-text needs its own --url")
    return [{"text": text, "url": url} for text, url in zip(texts, urls)]


def validated_body(kind: str, body: object, pending: dict[str, bytes] | None = None, *,
                   stored: bool = False) -> dict[str, object]:
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    if kind == "resource":
        return resource_body(body)
    if kind == "list":
        return {"entries": list_entries(body.get("entries"))}
    if kind == "conversation":
        if body:
            raise ValueError("a conversation has no body")
        return {}
    return {"questions": question_entries(body.get("questions"), pending, stored=stored)}


def make_body(kind: str, *, url: list[str] | None, link_text: list[str] | None, body: str | None,
              images: dict[str, bytes]) -> dict[str, object]:
    if kind == "resource":
        if not url or not link_text:
            raise ValueError("a resource needs --url and --link-text")
        if body is not None:
            raise ValueError("a resource takes --url and --link-text, not a body")
        return validated_body(kind, {"links": paired_links(link_text, url)})
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


# --- Projects and topics --------------------------------------------------------------
# The owner files a conversation under one optional project and, within it, one optional topic. A project or topic
# keeps its ID when renamed, so a placement and every link to a conversation stay valid. Nothing is ever deleted.

GROUP_NAME_LIMIT = 60
PROJECT_ID_RE = re.compile(r"P[1-9][0-9]*")
TOPIC_ID_RE = re.compile(r"T[1-9][0-9]*")


def clean_group_name(value: object, what: str) -> str:
    return clean_text(value, what, GROUP_NAME_LIMIT)


def validate_projects(data: dict[str, object]) -> None:
    if "projects" not in data:
        return
    projects, topic_ids, project_ids, names = data["projects"], set(), set(), set()
    damaged = "the item store is damaged: its projects are not in the saved format"
    if not isinstance(projects, list):
        raise ValueError(damaged)
    for project in projects:
        if not isinstance(project, dict) or set(project) != {"id", "name", "topics"}:
            raise ValueError(damaged)
        topics = project["topics"]
        if (not isinstance(project["id"], str) or not PROJECT_ID_RE.fullmatch(project["id"]) or project["id"] in project_ids
                or not isinstance(topics, list)):
            raise ValueError(damaged)
        project_ids.add(project["id"])
        try:
            name = clean_group_name(project["name"], "project name")
        except ValueError as exc:
            raise ValueError(damaged) from exc
        if name != project["name"] or name.casefold() in names:
            raise ValueError(damaged)
        names.add(name.casefold())
        topic_names = set()
        for topic in topics:
            if not isinstance(topic, dict) or set(topic) != {"id", "name"} or not isinstance(topic["id"], str) \
                    or not TOPIC_ID_RE.fullmatch(topic["id"]) or topic["id"] in topic_ids:
                raise ValueError(damaged)
            topic_ids.add(topic["id"])
            try:
                name = clean_group_name(topic["name"], "topic name")
            except ValueError as exc:
                raise ValueError(damaged) from exc
            if name != topic["name"] or name.casefold() in topic_names:
                raise ValueError(damaged)
            topic_names.add(name.casefold())


def check_placement(projects: list[dict[str, object]], placement: object) -> dict[str, str]:
    """Raise ValueError unless the placement names a project and, optionally, a topic inside that project."""
    if not isinstance(placement, dict) or "project" not in placement or set(placement) - {"project", "topic"}:
        raise ValueError("a placement is a project and an optional topic")
    project = next((entry for entry in projects if entry["id"] == placement["project"]), None)
    if project is None:
        raise ValueError("the placement names a project that does not exist")
    if "topic" in placement and not any(topic["id"] == placement["topic"] for topic in project["topics"]):  # type: ignore[attr-defined]
        raise ValueError("the placement names a topic that is not in its project")
    return placement  # type: ignore[return-value]


def new_placement(projects: list[dict[str, object]], project: object, topic: object) -> dict[str, str] | None:
    """The placement a request names by ID. None means Ungrouped."""
    if project is None:
        if topic is not None:
            raise ValueError("a topic needs its project")
        return None
    return check_placement(projects, {"project": project, **({} if topic is None else {"topic": topic})})


def project_tree(data: dict[str, object]) -> list[dict[str, object]]:
    """Projects and their topics by name, in the shape the Desk script and the CLI read."""
    by_name = lambda entry: (str(entry["name"]).casefold(), str(entry["id"]))  # noqa: E731
    return [{"id": project["id"], "name": project["name"],
             "topics": [{"id": topic["id"], "name": topic["name"]} for topic in sorted(project["topics"], key=by_name)]}
            for project in sorted(data.get("projects", []), key=by_name)]  # type: ignore[attr-defined,arg-type]


def placement_label(item: dict[str, object], projects: list[dict[str, object]]) -> str:
    """The project and topic of a conversation as the Desk shows them, or empty when it is ungrouped."""
    placement = item.get("placement")
    if not placement:
        return ""
    project = next(entry for entry in projects if entry["id"] == placement["project"])  # type: ignore[index]
    names = [str(project["name"])]
    if "topic" in placement:  # type: ignore[operator]
        names.append(next(str(topic["name"]) for topic in project["topics"] if topic["id"] == placement["topic"]))  # type: ignore[attr-defined,index]
    return " / ".join(names)


def find_project(projects: list[dict[str, object]], project_id: object) -> dict[str, object] | None:
    return next((entry for entry in projects if entry["id"] == project_id), None)


def named_placement(data: dict[str, object], project: str | None, topic: str | None) -> dict[str, str] | None:
    """The placement that --project and --topic name by ID, in any case. None means Ungrouped."""
    if project is None:
        if topic is not None:
            raise ValueError("--topic needs --project")
        return None
    projects = data.get("projects", [])
    found = find_project(projects, project.upper())  # type: ignore[arg-type]
    if found is None:
        known = ", ".join(f"{entry['id']} {entry['name']}" for entry in projects) or "none yet"  # type: ignore[attr-defined]
        raise ValueError(f"no project with ID {project!r}; the projects are: {known}")
    placement = {"project": str(found["id"])}
    if topic is not None:
        if not any(entry["id"] == topic.upper() for entry in found["topics"]):  # type: ignore[attr-defined]
            known = ", ".join(f"{entry['id']} {entry['name']}" for entry in found["topics"]) or "none yet"  # type: ignore[attr-defined]
            raise ValueError(f"no topic with ID {topic!r} in {found['id']} {found['name']}; its topics are: {known}")
        placement["topic"] = topic.upper()
    return placement


def change_projects(store: Path, payload: object) -> dict[str, object]:
    """Create a project or a topic, or rename one. The Desk sends one of three shapes."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid project fields.")
    fields = set(payload)
    with changing_store(store) as data:
        projects = data.setdefault("projects", [])
        if fields == {"name"}:
            name = clean_group_name(payload["name"], "Project name")
            if any(name.casefold() == str(project["name"]).casefold() for project in projects):  # type: ignore[attr-defined]
                raise AnswerConflict(f"A project named {name} already exists. Choose another name.")
            new_id = f"P{max((int(project['id'][1:]) for project in projects), default=0) + 1}"  # type: ignore[attr-defined]
            projects.append({"id": new_id, "name": name, "topics": []})  # type: ignore[attr-defined]
        elif fields == {"project", "name"}:
            project = find_project(projects, payload["project"])  # type: ignore[arg-type]
            if project is None:
                raise ValueError("Unknown project.")
            name = clean_group_name(payload["name"], "Topic name")
            if any(name.casefold() == topic["name"].casefold() for topic in project["topics"]):  # type: ignore[attr-defined]
                raise AnswerConflict(f"{project['name']} already has a topic named {name}. Choose another name.")
            new_id = "T" + str(max((int(topic["id"][1:]) for entry in projects for topic in entry["topics"]),  # type: ignore[attr-defined,index]
                                   default=0) + 1)
            project["topics"].append({"id": new_id, "name": name})  # type: ignore[attr-defined]
        elif fields == {"id", "name"}:
            target = payload["id"]
            if not isinstance(target, str):
                raise ValueError("Unknown project or topic.")
            if PROJECT_ID_RE.fullmatch(target):
                project = find_project(projects, target)  # type: ignore[arg-type]
                if project is None:
                    raise ValueError("Unknown project or topic.")
                name = clean_group_name(payload["name"], "Project name")
                if any(name.casefold() == str(other["name"]).casefold() for other in projects if other is not project):  # type: ignore[attr-defined]
                    raise AnswerConflict(f"A project named {name} already exists. Choose another name.")
                project["name"] = name
            elif TOPIC_ID_RE.fullmatch(target):
                found = next(((project, topic) for project in projects for topic in project["topics"]  # type: ignore[attr-defined,index]
                              if topic["id"] == target), None)
                if found is None:
                    raise ValueError("Unknown project or topic.")
                project, topic = found
                name = clean_group_name(payload["name"], "Topic name")
                if any(name.casefold() == other["name"].casefold() for other in project["topics"] if other is not topic):  # type: ignore[attr-defined,index]
                    raise AnswerConflict(f"{project['name']} already has a topic named {name}. Choose another name.")
                topic["name"] = name
            else:
                raise ValueError("Unknown project or topic.")
            new_id = target
        else:
            raise ValueError("Invalid project fields.")
        return {"id": new_id, "projects": project_tree(data)}


def utc_stamp(value: object, name: str) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        if moment.isoformat() + "Z" != value:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{name} must be a UTC timestamp") from exc
    return moment


def check_store(data: object) -> dict[str, object]:
    """Validate a store read from disk; raise ValueError when it is not one Quarterdeck wrote."""
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != STORE_VERSION:
        raise ValueError(f"the item store is not a version {STORE_VERSION} Quarterdeck store")
    items, next_id = data.get("items"), data.get("next_id")
    if not isinstance(items, list) or type(next_id) is not int or next_id < 1:
        raise ValueError("the item store is damaged: it lacks items or next_id")
    validate_projects(data)
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
            utc_stamp(item.get("saved_at"), "saved_at")
            body = validated_body(item["kind"], item.get("body"), stored=True)
            if (title, description, body) != (item["title"], item["description"], item["body"]):
                raise ValueError("item text or body is not in the saved format")
            if item_classification(item.get("project"), item.get("tags"), stored=True) != {
                    k: item[k] for k in ("project", "tags") if k in item}:
                raise ValueError("item project or tags are not in the saved format")
            for flag in CONVERSATION_FLAGS:
                if flag in item and (item[flag] is not True or item["kind"] != "conversation"):
                    raise ValueError(f"only a conversation can be {flag}")
            if "placement" in item:
                if item["kind"] != "conversation":
                    raise ValueError("only a conversation can be placed in a project")
                check_placement(data.get("projects", []), item["placement"])
            if "answers" in item:
                validate_records(item)
            if "conversation" in item:
                validate_conversation(item)
            elif item["kind"] == "conversation":
                raise ValueError("a conversation needs its messages")
        except ValueError as exc:
            raise ValueError(f"the item store is damaged: {item_id}: {exc}") from exc
    validate_catch_ups(data)
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


def now_text(*, fractional=False) -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds" if fractional else "seconds").replace("+00:00", "Z")


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
EARLIER_ANSWER_FIELDS = ("answer", "answered_at", "request_id")


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
        if "earlier_answers" in record:
            earlier = record["earlier_answers"]
            if "answer" not in record or not isinstance(earlier, list) or not earlier:
                raise ValueError("invalid earlier answers")
            for entry in earlier:
                if (not isinstance(entry, dict) or entry.keys() != set(EARLIER_ANSWER_FIELDS)
                        or clean_text(entry["answer"], "answer", TEXT_LIMIT, multiline=True) != entry["answer"]
                        or not re.fullmatch(r"quarterdeck-[0-9a-f]{32}", str(entry["request_id"]))):
                    raise ValueError("invalid earlier answer")
                parse_utc(entry["answered_at"])
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
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise ValueError("invalid message")
            author = message.get("author")
            if not isinstance(author, str) or author not in AUTHOR_LABELS:
                raise ValueError("invalid message")
            you = author == "you"
            required = {"id", "author", "text", "at"} | ({"delivery", "request_id", "home"} if you else {"read"})
            optional = set() if you else {"ask", "links", "images", "clarify"}
            if not you and index == 0 and item["kind"] == "conversation":
                optional.add("home")
            if not required <= set(message) <= required | optional:
                raise ValueError("invalid message")
            if (not isinstance(message["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", message["id"])
                    or message["id"] in seen
                    or clean_text(message["text"], "message", TEXT_LIMIT, multiline=True) != message["text"]):
                raise ValueError("invalid message")
            seen.add(message["id"])
            parse_utc(message["at"])
            if "home" in message and (not isinstance(message["home"], str)
                                      or not re.fullmatch(r"[0-9a-f]{64}", message["home"])):
                raise ValueError("invalid message home")
            if you:
                if (message["delivery"] not in ("pending", "sent")
                        or not isinstance(message["request_id"], str)
                        or not re.fullmatch(r"quarterdeck-[0-9a-f]{32}", message["request_id"])):
                    raise ValueError("invalid ask")
                asks.add(message["id"])
                continue
            links, images = message.get("links", []), message.get("images", [])
            if "clarify" in message and (item["kind"] != "questions" or part != "item"
                                         or not valid_clarifications(message["clarify"], parts)):
                raise ValueError("invalid reply")
            # A Firstmate message without an ask is a standalone update, on conversations only.
            if (("ask" in message and (not isinstance(message["ask"], str) or message["ask"] not in asks))
                    or ("ask" not in message and item["kind"] != "conversation")
                    or type(message["read"]) is not bool or question_links(links, "reply") != links
                    or not isinstance(images, list)
                    or [image_entry(image, "reply image", None) for image in images] != images):
                raise ValueError("invalid reply")


def valid_clarifications(clarify, parts):
    """A reply to the whole item can clarify its questions: question number -> {text, option_changed}."""
    return (isinstance(clarify, dict) and bool(clarify)
            and all(re.fullmatch(r"[1-9][0-9]*", number) and int(number) in parts and isinstance(entry, dict)
                    and set(entry) == {"text", "option_changed"} and type(entry["option_changed"]) is bool
                    and clean_text(entry["text"], "clarification", TEXT_LIMIT, multiline=True) == entry["text"]
                    for number, entry in clarify.items()))


def clarifications(item, number):
    """What Firstmate's replies in the set discussion clarified about one question, oldest first."""
    return [{**reply["clarify"][str(number)], "reply_id": reply["id"], "at": reply["at"]} for reply in part_thread(item, None)
            if reply["author"] == "firstmate" and str(number) in reply.get("clarify", {})]


def latest_option_change(notes):
    return next((note for note in reversed(notes) if note["option_changed"]), {})


def option_review_required(change, record):
    return bool(change) and ("answer" not in record or parse_utc(change["at"]) > parse_utc(record["answered_at"]))


def thread_key(part):
    return "item" if part is None else str(part)


def part_thread(item, part):
    return item.get("conversation", {}).get(thread_key(part), [])


def unread_reply(message):
    return message["author"] == "firstmate" and not message["read"]


def thread_unread(item, part):
    return any(map(unread_reply, part_thread(item, part)))


UNREAD_REPLY = "Unread reply"


def first_unread_reply(item, part):
    return next((m for m in part_thread(item, part) if unread_reply(m)), None)


def unread_target(item):
    parts = question_parts(item) if item["kind"] == "questions" else ()
    for part in (*parts, None):
        reply = first_unread_reply(item, part)
        if reply:
            return f'reply-{reply["id"]}'
    return None


def has_pending_ask(item):
    return any(
        message["author"] == "you" and message["delivery"] == "pending"
        for key, thread in item.get("conversation", {}).items()
        if conversation_editable(item, None if key == "item" else int(key))
        for message in thread
    )


def conversation_activity(item):
    """The moment of a conversation's latest message, or of its saving. Pinning and reading never change it."""
    return max([parse_utc(item["saved_at"])] + [parse_utc(m["at"]) for m in part_thread(item, None)])


def by_activity(items):
    """Newest activity first, the newest saved item first on a tie."""
    return sorted(items, key=lambda item: (conversation_activity(item), int(str(item["id"])[1:])), reverse=True)


def is_pinned(item):
    return item.get("pinned") is True


def is_resolved(item):
    return item.get("resolved") is True


def is_archived(item):
    return item.get("archived") is True


def part_state(item, part, *, replies=True):
    state = part_record(item, part).get("state", "open")
    return "replied" if replies and thread_unread(item, part) else state


def put_record(item, part, state, *, mark=None, **desk_fields):
    previous = part_record(item, part)
    marks = answer_marks(previous)
    earlier = previous.get("earlier_answers", [])
    if "answer" in previous:
        if desk_fields:
            archived = {k: previous[k] for k in EARLIER_ANSWER_FIELDS}
            archived["answered_at"] = next((m["answered_at"] for m in reversed(marks) if m["via"] == DESK),
                                           archived["answered_at"])
            earlier = [*earlier, archived]
        else:
            desk_fields = {k: previous[k] for k in DESK_FIELDS}
    if mark is not None:
        marks = [*marks, mark]
    record = {**desk_fields, "state": state, "revision": uuid.uuid4().hex}
    if marks:
        record["answer_marks"] = marks
    if earlier:
        record["earlier_answers"] = earlier
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


class OptionChanged(AnswerConflict):
    def __init__(self, notes):
        self.notes = notes


class SavedAskConflict(AnswerConflict):
    pass


class AskHomeUnavailable(Exception):
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
    required = {"part", "answer", "revision"}
    if not isinstance(payload, dict) or not required <= payload.keys() <= required | {"option_revision"}:
        raise ValueError("Invalid answer fields.")
    reviewed = payload.get("option_revision", "")
    if any(not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-f]{32})?", value)
           for value in (payload["revision"], reviewed)):
        raise ValueError("Invalid question revision.")
    item_id, part = parse_part(payload["part"])
    answer = clean_text(payload["answer"], "Answer", TEXT_LIMIT, multiline=True)

    with locked_store(store):
        data = read_store(store)
        item = find_answer_item(data, item_id)
        if part not in question_parts(item):
            raise ValueError("Unknown question.")
        old = part_record(item, part)
        state = old.get("state")
        if state == "closed":
            raise AnswerConflict("Question closed. Reopen it before sending.")
        notes = clarifications(item, part)
        change = latest_option_change(notes)
        if reviewed != change.get("reply_id", ""):
            raise OptionChanged(notes)
        same = old.get("answer") == answer
        repeat = same and state == "answered" and old.get("delivery") != "cancelled"
        if not repeat and payload["revision"] != old.get("revision", ""):
            raise AnswerConflict("Question changed. Reload before sending.")
        if same:
            record = old
            if option_review_required(change, record):
                record.setdefault("answer_marks", answer_marks(record))
                record["answered_at"] = now_text(fractional=True)
                write_store(store, data)
        else:
            at = now_text(fractional=bool(change))
            record = put_record(item, part, "answered", mark={"via": DESK, "answered_at": at}, answer=answer, answered_at=at,
                                delivery="pending", request_id="quarterdeck-" + uuid.uuid4().hex)
            write_store(store, data)
        if home and delivery_pending(record) and deliver_note(home, record["request_id"], f"{item_id}.{part} {record['answer']}"):
            record["delivery"] = "sent"
            write_store(store, data)
    return {**{k: record[k] for k in ("state", "revision", "answer", "answered_at", "delivery")},
            "earlier_answers_html": earlier_answers_html(record),
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
    delivery = message["delivery"]
    result = {"message": f"{noun} sent." if delivery == "sent" else f"{noun} saved. Not delivered."}
    if include_thread:
        if part is None and item["kind"] == "questions":
            result["delivery"] = delivery
            result.update(set_discussion(item, editable=conversation_editable(item, part)))
        else:
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
    return item["kind"] != "conversation" or thread_home(part_thread(item, None)) in (None, binding)


def thread_home(thread):
    return next((m["home"] for m in thread if "home" in m), None)


def ask_message(store, home, binding, payload, *, home_available=True, include_thread=True):
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
            if item["kind"] == "conversation" and not home_available:
                raise AskHomeUnavailable
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
        if item["kind"] == "conversation" and not home_available:
            raise AskHomeUnavailable
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


def append_conversation(data, title, message, placement=None):
    item_id = f"Q{data['next_id']}"
    data["next_id"] = int(data["next_id"]) + 1
    data["items"].append({"id": item_id, "kind": "conversation", "title": title,
                          "description": "Conversation with Firstmate.", "saved_at": now_text(), "body": {},
                          **({"placement": placement} if placement else {}),
                          "conversation": {"item": [message]}})
    return item_id


def start_conversation(store, home, binding, payload):
    """Save a new conversation item holding its first message, then deliver that message like any ask."""
    required = {"id", "title", "text", "home"}
    if not isinstance(payload, dict) or not required <= set(payload) <= required | {"project", "topic"}:
        raise ValueError("Invalid conversation fields.")
    message_id = payload["id"]
    if not isinstance(message_id, str) or not re.fullmatch(r"[0-9a-f]{32}", message_id):
        raise ValueError("Invalid message ID.")
    title = clean_text(payload["title"], "Conversation title", TITLE_LIMIT)
    text = clean_text(payload["text"], "Message", TEXT_LIMIT, multiline=True)
    with locked_store(store):
        data = read_store(store)
        for item in data["items"]:
            first = item.get("conversation", {}).get("item", [None])[0] if item["kind"] == "conversation" else None
            if first and first["author"] == "you" and first["id"] == message_id:
                if (item["title"], first["text"]) != (title, text):
                    raise AnswerConflict("Message ID already in use. Reload the Desk.")
                if payload["home"] != binding or first["home"] != binding:
                    raise SavedAskConflict("Firstmate home changed. This message belongs to the original home.")
                item_id = item["id"]
                break
        else:
            if payload["home"] != binding:
                raise AnswerConflict("Firstmate home changed. This message belongs to the original home.")
            placement = new_placement(data.get("projects", []), payload.get("project"), payload.get("topic"))
            item_id = append_conversation(data, title, new_ask(message_id, text, binding), placement)
            write_store(store, data)
    result = ask_message(store, home, binding, {"part": item_id, "id": message_id, "action": "retry"},
                         include_thread=False)
    with locked_store(store, read_only=True):
        data = read_store(store)
    item = find_item(data, item_id)
    summary = attention_summary(item, served=True)
    return {**result, "href": item_page_name(item_id), "row": conversation_row(item, data.get("projects", [])),
            "hold": library_row(item, served=True),
            "attention": desk_row(item, summary, primary=False, context="needs-you") if summary else None}


CONVERSATION_FLAGS = ("pinned", "resolved", "archived")
FLAG_SUBJECTS = {"pinned": "pin", "resolved": "resolution", "archived": "archive"}


def set_conversation_flag(store, payload, flag):
    """Set or clear a conversation's pinned, resolved or archived flag. Nothing else about it changes, and no flag locks it."""
    subject = FLAG_SUBJECTS[flag]
    if not isinstance(payload, dict) or set(payload) != {"part", flag} or type(payload[flag]) is not bool:
        raise ValueError(f"Invalid {subject} fields.")
    item_id = payload["part"]
    if not isinstance(item_id, str) or item_id != item_id.upper() or not ID_RE.fullmatch(item_id):
        raise ValueError("Unknown conversation.")
    with changing_store(store) as data:
        item = find_item(data, item_id)
        if item["kind"] != "conversation":
            raise ValueError(f"Only a conversation can be {flag}.")
        if payload[flag]:
            item[flag] = True
        else:
            item.pop(flag, None)
        result = {flag: payload[flag]}
        if flag != "pinned":  # the Hold list keeps its own row for the conversation
            result["hold"] = "" if is_archived(item) else library_row(item, served=True)
        return result


def pin_conversation(store, payload):
    return set_conversation_flag(store, payload, "pinned")


def move_conversation(store, payload):
    """Change which project and topic a conversation is in, or return it to Ungrouped. Nothing else about it changes."""
    if not isinstance(payload, dict) or set(payload) != {"part", "project", "topic"}:
        raise ValueError("Invalid move fields.")
    item_id = payload["part"]
    if not isinstance(item_id, str) or item_id != item_id.upper() or not ID_RE.fullmatch(item_id):
        raise ValueError("Unknown conversation.")
    with changing_store(store) as data:
        item = find_item(data, item_id)
        if item["kind"] != "conversation":
            raise ValueError("Only a conversation can be moved.")
        placement = new_placement(data.get("projects", []), payload["project"], payload["topic"])
        if placement:
            item["placement"] = placement
        else:
            item.pop("placement", None)
        return {"project": payload["project"], "topic": payload["topic"]}


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


def conversation_message_numbers(messages):
    return {message["id"]: number
            for number, message in enumerate((m for m in messages if m["author"] == "you"), 1)}


def item_text(item: dict[str, object], projects: list[dict[str, object]] | None = None) -> str:
    """One item as plain text, numbered the way the page numbers it."""
    item_id, body = str(item["id"]), item["body"]
    lines = [f"{item_id} · {item['title']}", f"{KIND_LABELS[str(item['kind'])]} · saved {saved_day(item)}",
             "", str(item["description"]), ""]
    if item.get("placement"):
        lines.insert(2, f"Placement: {placement_label(item, projects or [])}")
    if item.get("project"):
        lines.insert(2, f"Project: {item['project']}")
    if item.get("tags"):
        lines.insert(3 if item.get("project") else 2, "Tags: " + ", ".join(item["tags"]))
    if item["kind"] == "resource":
        lines.extend(f"{link['text']}: {link['url']}" for link in resource_links(body))  # type: ignore[arg-type]
    elif item["kind"] == "list":
        lines.extend(f"{item_id}.{number}  {entry}" for number, entry in enumerate(body["entries"], 1))  # type: ignore[index]
    elif item["kind"] == "conversation":
        lines = lines[:-2]
        states = [name for name, on in (("Resolved", is_resolved(item)), ("Archived", is_archived(item))) if on]
        if states:
            lines.insert(len(lines) - 1, "Status: " + ", ".join(states))
        messages = part_thread(item, None)
        numbers = conversation_message_numbers(messages)
        for message in messages:
            who = AUTHOR_LABELS[message["author"]]
            if message["author"] == "you":
                label = f"{who} ({delivery_label(message).lower()})"
                reference = f"Message {numbers[message['id']]}"
            else:
                label = who
                reference = f"In reply to message {numbers[message['ask']]}" if "ask" in message else "Update"
            lines.append(f"{message['at']}  {label}  {message['id']}  {reference}")
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


def edit_item(args: argparse.Namespace) -> int:
    def action() -> int:
        if args.title is None and args.description is None and not args.url and not args.link_text:
            raise ValueError("nothing to change: give --title, --description, or --link-text with --url")
        links = paired_links(args.link_text, args.url) if args.url or args.link_text else None
        store = store_path()
        with changing_store(store) as data:
            item = find_item(data, args.id)
            if item["kind"] not in ("resource", "questions"):
                raise ValueError(f"{item['id']} is a {item['kind']}; edit changes only resources and question items")
            if links is not None and item["kind"] != "resource":
                raise ValueError(f"{item['id']} is not a resource, so it has no links to edit")
            if args.title is not None:
                item["title"] = clean_text(args.title, "title", TITLE_LIMIT)
            if args.description is not None:
                item["description"] = clean_text(args.description, "description", DESCRIPTION_LIMIT, multiline=True)
            if links is not None:
                item["body"] = validated_body("resource", {"links": links})
        print(f"Edited {item['id']}")
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
        data = read_store(store_path())
        print(item_text(find_item(data, args.id), data.get("projects", [])), end="")
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


# --- Catch-up ---------------------------------------------------------------------------


def parse_catch_up_id(value: str) -> str:
    if not CATCH_UP_ID_RE.fullmatch(value.upper()):
        raise ValueError(f"not a catch-up ID: {value!r} (IDs look like C4)")
    return value.upper()


def catch_up_link(url: object, link_text: object) -> dict[str, str]:
    if (url is None) != (link_text is None):
        raise ValueError("a link needs both --url and --link-text")
    if url is None:
        return {}
    return {"url": clean_url(url), "link_text": clean_text(link_text, "link text", TITLE_LIMIT)}


def validate_catch_ups(data: dict[str, object]) -> None:
    if "catch_ups" not in data and "next_catch_up" not in data:
        return
    notices, next_id = data.get("catch_ups"), data.get("next_catch_up")
    if not isinstance(notices, list) or type(next_id) is not int or next_id < 1:
        raise ValueError("the item store is damaged: it lacks catch_ups or next_catch_up")
    seen = set()
    for notice in notices:
        if not isinstance(notice, dict) or not isinstance(notice.get("id"), str):
            raise ValueError("the item store is damaged: a catch-up notice has no id")
        notice_id = notice["id"]
        try:
            if parse_catch_up_id(notice_id) != notice_id or notice_id in seen or int(notice_id[1:]) >= next_id:
                raise ValueError("it is not canonical, is repeated or is above next_catch_up")
            seen.add(notice_id)
            allowed = {"id", "title", "summary", "saved_at", "read_at", "url", "link_text", "item", "project"}
            if set(notice) - allowed:
                raise ValueError("it has an unknown field")
            expected = {
                "title": clean_text(notice.get("title"), "title", TITLE_LIMIT),
                "summary": clean_text(notice.get("summary"), "summary", CATCH_UP_SUMMARY_LIMIT),
                **catch_up_link(notice.get("url"), notice.get("link_text")),
            }
            if "item" in notice:
                expected["item"] = parse_id(str(notice["item"]))
            if "project" in notice:
                expected["project"] = clean_project(notice["project"])
            utc_stamp(notice.get("saved_at"), "saved_at")
            if "read_at" in notice:
                utc_stamp(notice["read_at"], "read_at")
            if {k: v for k, v in notice.items() if k not in {"id", "saved_at", "read_at"}} != expected:
                raise ValueError("its text is not in the saved format")
        except ValueError as exc:
            raise ValueError(f"the item store is damaged: {notice_id}: {exc}") from exc


def catch_ups(data: dict[str, object]) -> list[dict[str, object]]:
    return data.get("catch_ups", [])  # type: ignore[return-value]


def unread_catch_ups(data: dict[str, object]) -> list[dict[str, object]]:
    """Unread notices, newest first."""
    return newest_first([notice for notice in catch_ups(data) if "read_at" not in notice])


def prune_catch_ups(data: dict[str, object], days: int) -> None:
    """Drop read notices at or before the retention cutoff."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    data["catch_ups"] = [notice for notice in catch_ups(data)
                         if "read_at" not in notice or utc_stamp(notice["read_at"], "read_at") > cutoff]


class CatchUpConflict(ValueError):
    pass


def catch_ups_by_ids(data, ids):
    notices = {notice["id"]: notice for notice in catch_ups(data)}
    if not notices.keys() >= set(ids):
        raise CatchUpConflict("Unknown catch-up notice.")
    return [notices[notice_id] for notice_id in ids]


def mark_catch_ups_read(store: Path, ids: list[str], days: int) -> list[str]:
    """Mark the named notices and return their IDs."""
    with locked_store(store):
        data = read_store(store)
        notices = catch_ups_by_ids(data, ids)
        stamp = now_text()
        for notice in notices:
            notice.setdefault("read_at", stamp)
        prune_catch_ups(data, days)
        write_store(store, data)
        return ids


def add_catch_up(args: argparse.Namespace) -> int:
    def action() -> int:
        days = catch_up_days(selected_config(args))
        notice: dict[str, object] = {
            "title": clean_text(args.title, "title", TITLE_LIMIT),
            "summary": clean_text(args.summary, "summary", CATCH_UP_SUMMARY_LIMIT),
            **catch_up_link(args.url, args.link_text),
        }
        if args.project is not None:
            notice["project"] = clean_project(args.project)
        store = store_path()
        with changing_store(store) as data:
            if args.item is not None:
                notice["item"] = find_item(data, args.item)["id"]
            notice_id = f"C{data.get('next_catch_up', 1)}"
            data["next_catch_up"] = int(data.get("next_catch_up", 1)) + 1  # type: ignore[call-overload]
            data.setdefault("catch_ups", [])
            data["catch_ups"].append({"id": notice_id, **notice, "saved_at": now_text()})  # type: ignore[union-attr]
            prune_catch_ups(data, days)
        print(notice_id)
        return 0
    return run_item_command(action)


def list_catch_ups(args: argparse.Namespace) -> int:
    def action() -> int:
        notices = unread_catch_ups(read_store(store_path()))
        if not notices:
            print("No unread catch-up notices.")
        for notice in notices:
            print(f"{notice['id']:<6} {str(notice['saved_at'])[:10]}  {notice['title']}")
        return 0
    return run_item_command(action)


def print_catch_up(notice):
    print(f"{notice['id']}  {notice['saved_at']}  {notice['title']}")
    print(f"  {notice['summary']}")
    if "project" in notice:
        print(f"  Project: {notice['project']}")
    if "item" in notice:
        print(f"  Item: {notice['item']}")
    if "url" in notice:
        print(f"  {notice['link_text']}: {notice['url']}")


def read_catch_up(args: argparse.Namespace) -> int:
    def action() -> int:
        store = store_path()
        if args.all:
            if args.ids:
                raise ValueError("give catch-up IDs or --all, not both")
            ids = None
        elif args.ids:
            ids = list(dict.fromkeys(parse_catch_up_id(value) for value in args.ids))
        else:
            raise ValueError("give a catch-up ID such as C4, or --all")
        days = catch_up_days(selected_config(args))
        data = read_store(store)
        notices = unread_catch_ups(data) if ids is None else catch_ups_by_ids(data, ids)
        if not notices:
            print("No unread catch-up notices.")
            return 0
        for notice in notices:
            print_catch_up(notice)
        sys.stdout.flush()
        marked = mark_catch_ups_read(store, [str(notice["id"]) for notice in notices], days)
        for notice_id in marked:
            print(f"{notice_id} Read")
        return 0
    return run_item_command(action)


# --- Report -------------------------------------------------------------------------------
# Counts questions by kind, tag and project. A question's tags are its own plus its item's.
# It reads only the store and never decides whether anything was answered elsewhere.

UNCLASSIFIED = "(unclassified)"


def parse_utc(text: object) -> datetime:
    value = str(text)
    seconds = "%S.%f" if "." in value else "%S"
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:" + seconds + "Z").replace(tzinfo=timezone.utc)


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


def clarify_arguments(clarified, changed, item_id, part):
    """--clarify Q7.1=TEXT and --option-changed Q7.1 become the clarify field of a reply to the whole item."""
    if not clarified and not changed:
        return {}
    if part is not None:
        raise ValueError(f"--clarify and --option-changed belong on a reply to the whole item, such as {item_id}")
    result = {}
    for entry in clarified:
        name, equals, text = entry.partition("=")
        question = parse_part(name.upper())
        if not equals or question[0] != item_id or question[1] in map(int, result):
            raise ValueError(f"--clarify takes each question of {item_id} once, as {item_id}.1=TEXT: {entry!r}")
        result[str(question[1])] = {"text": clean_text(text, "clarification", TEXT_LIMIT, multiline=True),
                                    "option_changed": False}
    for name in changed:
        question = parse_part(name.upper())
        if question[0] != item_id or str(question[1]) not in result:
            raise ValueError(f"--option-changed {name} needs a --clarify for the same question")
        result[str(question[1])]["option_changed"] = True
    return result


def reply_text_and_links(args: argparse.Namespace):
    text = clean_text(args.text, "reply text", TEXT_LIMIT, multiline=True)
    links = question_links(paired_links(args.link_text, args.url), "reply")
    return text, links


def read_reply_images(args: argparse.Namespace):
    paths, alts = args.image or [], args.alt or []
    if len(paths) != len(alts):
        raise ValueError("each --image needs its own --alt text that describes it")
    named: list[tuple[dict[str, str], str]] = []
    images = [image_entry({"path": path, "alt": alt}, f"reply image {number}", named)
              for number, (path, alt) in enumerate(zip(paths, alts), 1)]
    pending: dict[str, bytes] = {}
    read_images(named, pending)
    return images, pending


def reply_parts(args: argparse.Namespace):
    """The text, links and images shared by post and converse."""
    text, links = reply_text_and_links(args)
    images, pending = read_reply_images(args)
    return text, links, images, pending


def firstmate_message(text, links, images, ask=None, *, fractional=False):
    message: dict[str, object] = {"id": uuid.uuid4().hex, "author": "firstmate", "text": text,
                                  "at": now_text(fractional=fractional), "read": False}
    if ask is not None:
        message["ask"] = ask
    if links:
        message["links"] = links
    if images:
        message["images"] = images
    return message


def reply_item(args: argparse.Namespace) -> int:
    def action() -> int:
        target = args.id.upper()
        try:
            item_id, part = parse_thread_target(target)
        except ValueError as exc:
            raise ValueError(f"reply to a conversation item, a question item or one of its questions, such as Q7 or Q7.1: {args.id!r}") from exc
        text, links = reply_text_and_links(args)
        clarify = clarify_arguments(args.clarify or [], args.option_changed or [], item_id, part)
        images, pending = read_reply_images(args)
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
            option_changed = any(note["option_changed"] for note in clarify.values())
            message = firstmate_message(text, links, images, ask, fractional=option_changed)
            if clarify:
                if item["kind"] != "questions":
                    raise ValueError("--clarify needs a question item")
                if not valid_clarifications(clarify, question_parts(item)):
                    raise ValueError("--clarify names a question this item does not have")
                message["clarify"] = clarify
            thread.append(message)
            save_images(store, item_id, pending, tree=REPLY_IMAGES)
            write_store(store, data)
        print(f"{target} Replied")
        return 0
    return run_item_command(action)


def post_update(args: argparse.Namespace) -> int:
    """Firstmate's own update on a conversation: no ask to answer, unread like a reply, no inbox note."""
    def action() -> int:
        target = args.id.upper()
        text, links, images, pending = reply_parts(args)
        store = store_path()
        with locked_store(store):
            data = read_store(store)
            item = find_item(data, target)
            if item["kind"] != "conversation":
                raise ValueError(f"post to a conversation item, such as Q12: {args.id!r}")
            part_thread(item, None).append(firstmate_message(text, links, images))
            save_images(store, item["id"], pending, tree=REPLY_IMAGES)
            write_store(store, data)
        print(f"{target} Updated")
        return 0
    return run_item_command(action)


def set_flag_command(flag: str, value: bool, done: str):
    """A CLI command that sets or clears a conversation's resolved or archived flag, and reports the new state."""
    def command(args: argparse.Namespace) -> int:
        def action() -> int:
            target = args.id.upper()
            store = store_path()
            with locked_store(store):
                data = read_store(store)
                item = find_item(data, target)
                if item["kind"] != "conversation":
                    raise ValueError(f"use a conversation item, such as Q12: {args.id!r}")
                if value:
                    item[flag] = True
                else:
                    item.pop(flag, None)
                write_store(store, data)
            print(f"{target} {done}")
            return 0
        return run_item_command(action)
    return command


resolve_conversation = set_flag_command("resolved", True, "Resolved")
reopen_conversation = set_flag_command("resolved", False, "Reopened")
archive_conversation = set_flag_command("archived", True, "Archived")
unarchive_conversation = set_flag_command("archived", False, "Unarchived")


def converse(args: argparse.Namespace) -> int:
    """Open a conversation in Firstmate's own name; its first message is an update, and no inbox note is sent."""
    def action() -> int:
        title = clean_text(args.title, "title", TITLE_LIMIT)
        text, links, images, pending = reply_parts(args)
        home = configured_firstmate_home(selected_config(args))
        message = firstmate_message(text, links, images)
        if home is not None:
            _, binding = ask_destination(home, require_directory=True)
            if binding is None:
                raise ValueError("firstmate_home must resolve to an existing directory")
            message["home"] = binding
        store = store_path()
        with locked_store(store):
            data = read_store(store)
            placement = named_placement(data, args.project, args.topic)
            item_id = append_conversation(data, title, message, placement)
            save_images(store, item_id, pending, tree=REPLY_IMAGES)
            write_store(store, data)
        print(item_id)
        return 0
    return run_item_command(action)


def list_projects(args: argparse.Namespace) -> int:
    """Print the owner's projects and the topics in each, so Firstmate can name one when it opens a conversation."""
    def action() -> int:
        tree = project_tree(read_store(store_path()))
        if not tree:
            print("No projects.")
        for project in tree:
            print(f"{project['id']:<6} {project['name']}")
            for topic in project["topics"]:  # type: ignore[attr-defined]
                print(f"  {topic['id']:<6} {topic['name']}")
        return 0
    return run_item_command(action)


def move_item(args: argparse.Namespace) -> int:
    """Move a conversation into a project and topic, or back to Ungrouped. Nothing else about it changes."""
    def action() -> int:
        if args.ungrouped == (args.project is not None):
            raise ValueError("give --project, with --topic if you want one, or --ungrouped")
        with changing_store(store_path()) as data:
            item = find_item(data, args.id)
            if item["kind"] != "conversation":
                raise ValueError(f"move a conversation, such as Q12: {args.id!r}")
            placement = named_placement(data, args.project, args.topic)
            if placement:
                item["placement"] = placement
            else:
                item.pop("placement", None)
        print(f"{item['id']} Moved")
        return 0
    return run_item_command(action)


# --- Workers ------------------------------------------------------------------------------
# Roster refresh runs only the home's read-only fleet snapshot, without a model.
# Quarterdeck shows only what the snapshot states outright. `serve` rereads the home on a fixed interval and
# keeps the last good reading on disk next to the item store, so a page never waits on the fleet snapshot.

SNAPSHOT_COMMAND = ("bin/fm-fleet-snapshot.sh", "--local")
SNAPSHOT_SECONDS = 60
WORKERS_FRESH_SECONDS = 3600  # a saved reading older than this is reported as stale
WORKERS_REFRESH_SECONDS = 1800  # how often `serve` rereads the home in the background (30 minutes)
WORKERS_CACHE_NAME = "workers-saved.json"
UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
WORKER_STATES = {"working": "Working", "done": "Finished", "blocked": "Stuck", "failed": "Stuck"}
ACTIVITY_UNAVAILABLE = "Activity unavailable"


class SnapshotError(Exception):
    pass


def workers_clock() -> float:
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    return time.monotonic() if clock is None else time.clock_gettime(clock)


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
    data = run_snapshot(home)
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
    work_tasks = {key: task for key, task in workers.items() if key not in second_mates}
    return {
        "observed_at": generated,
        "workers": [roster_row(key, task, observed) for key, task in work_tasks.items()],
        "second_mates": [roster_row(key, task, observed) for key, task in second_mates.items()],
        "work": underway_rows(data, work_tasks, observed),
    }


def workers_cache_path(store: Path | None = None) -> Path:
    """The last good reading is kept next to the item store."""
    return (store or store_path()).with_name(WORKERS_CACHE_NAME)


class SavedWorkers:
    """The last successful reading, shared by every visitor and kept on disk so a restart shows it at once."""

    def __init__(self, home: Path, path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._active: Future[dict[str, object]] | None = None
        self._active_background = False
        self._home = home
        self._home_key = os.path.realpath(home)
        self._reading: tuple[float, dict[str, object]] | None = None
        self._failed = False
        self._path = path
        self._load()

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            record = json.loads(self._path.read_text(encoding="utf-8"))
            checked, snapshot = record["checked_at"], record["snapshot"]
            if (record.get("home") != self._home_key or not UTC_RE.fullmatch(checked) or not isinstance(snapshot, dict)
                    or not isinstance(snapshot.get("observed_at"), str)
                    or not isinstance(snapshot.get("workers"), list)
                    or not isinstance(snapshot.get("second_mates"), list)
                    or not isinstance(snapshot.get("work"), list)):
                return
            age = datetime.now(timezone.utc) - datetime.strptime(checked, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return
        self._reading = (workers_clock() - max(0.0, age.total_seconds()), snapshot)

    def _save(self, snapshot: dict[str, object]) -> None:
        if self._path is None:
            return
        record = {"home": self._home_key, "checked_at": utc_text(datetime.now(timezone.utc)), "snapshot": snapshot}
        try:
            write_private(self._path, json.dumps(record))
        except OSError as exc:  # the reading still serves from memory
            print(f"quarterdeck: could not save the workers reading: {exc}", file=sys.stderr, flush=True)

    @staticmethod
    def _stale(checked_at: float) -> bool:
        return workers_clock() - checked_at > WORKERS_FRESH_SECONDS

    def refresh(self, *, reuse: bool, background: bool = False) -> dict[str, object]:
        """A manual check (`reuse` false) joins a running background check but not another manual one."""
        with self._lock:
            active = self._active
            if active is not None:
                if not reuse and not self._active_background:
                    raise SnapshotError("the fleet snapshot is already running")
            else:
                if reuse and self._reading is not None:
                    checked_at, snapshot = self._reading
                    if not self._stale(checked_at):
                        return snapshot
                self._active = pending = Future()
                self._active_background = background
        if active is not None:
            return active.result()
        try:
            snapshot = workers_snapshot(self._home)
            self._save(snapshot)
            self._reading = (workers_clock(), snapshot)
            self._failed = False
        except BaseException as exc:
            self._failed = True  # the last good reading stays
            pending.set_exception(exc)
            raise
        else:
            pending.set_result(snapshot)
            return snapshot
        finally:
            with self._lock:
                self._active = None

    def view(self) -> dict[str, object]:
        """The saved reading, whether it is missing or older than WORKERS_FRESH_SECONDS, and whether the last check failed."""
        reading = self._reading
        if reading is None:
            return {"snapshot": None, "stale": True, "failed": self._failed}
        checked_at, snapshot = reading
        return {"snapshot": snapshot, "stale": self._stale(checked_at), "failed": self._failed}


def keep_workers_fresh(saved: SavedWorkers, interval: float, stop: threading.Event) -> None:
    """Reread the home now and then every `interval` seconds until `stop` is set; a failure keeps the last reading."""
    while not stop.is_set():
        try:
            saved.refresh(reuse=False, background=True)
        except SnapshotError:
            pass  # refresh has already kept the last good reading and marked the failure
        except Exception as exc:  # never end the loop
            print(f"quarterdeck: the workers check failed: {exc}", file=sys.stderr, flush=True)
        stop.wait(interval)


# --- Under way ----------------------------------------------------------------------------
# The Desk's Under way tab reads only the saved workers reading, so opening it never waits on the fleet snapshot.
# The reading carries one row per ship or scout with a live terminal, plus the ones merged today.

UNDERWAY_KINDS = {"ship", "scout"}
UNDERWAY_ORDER = ("Ready to merge", "In review", "Building", "Working", "Question asked", "Stuck", "Unknown",
                  "Finished", "Merged today")
TASK_LINKS_DEFAULT = Path("data") / "qd-threads" / "links.tsv"
TASK_LINKS_MAX_BYTES = 1 << 20


def local_today() -> str:
    """The date on the machine that serves Quarterdeck, which is also the home's date for merge records."""
    return datetime.now().astimezone().date().isoformat()


def underway_status(task: dict, kind: str, has_pull_request: bool) -> str:
    """The Workers page's word, sharpened only where the reading states a pull request outright."""
    status = worker_status(task)
    if status == "Working":
        return "In review" if has_pull_request else "Building" if kind == "ship" else "Working"
    if status == "Finished" and has_pull_request:
        return "Ready to merge"
    return status


def merge_record(record: object) -> tuple[str, str] | None:
    """The task id and local date of a backlog record that states it was merged, else None."""
    if not isinstance(record, dict) or record.get("state") != "done":
        return None
    completion, key = record.get("completion"), record.get("id")
    if not isinstance(completion, dict) or completion.get("verb") != "merged":
        return None
    date = completion.get("date")
    if not isinstance(key, str) or not isinstance(date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
        return None
    return key, date


def pull_request_href(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return clean_url(value)
    except ValueError:
        return None


def underway_rows(data: dict, tasks: dict[str, dict], observed: datetime) -> list[dict[str, object]]:
    """Ships and scouts with a live terminal, then the work merged today, as the Under way tab shows them."""
    backlog = data.get("backlog")
    records = backlog.get("records") if isinstance(backlog, dict) else None
    records = [record for record in records if isinstance(record, dict)] if isinstance(records, list) else []
    merged = dict(found for record in records if (found := merge_record(record)))
    today = observed.astimezone().date().isoformat()
    rows: list[dict[str, object]] = []
    for key, task in tasks.items():
        kind = task.get("kind")
        if kind not in UNDERWAY_KINDS:
            continue
        row = roster_row(key, task, observed)
        links = row["links"]
        merged_on = merged.get(key)
        if merged_on is not None and merged_on != today:
            continue
        status = "Merged today" if merged_on else underway_status(task, kind, bool(links))
        title = snapshot_text(snapshot_field(snapshot_field(task, "backlog", dict) or {}, "title", str))
        rows.append({"id": key, "title": title or key, "project": row["project"], "status": status,
                     "pr": links[0]["href"] if links else None, "merged_on": merged_on})
    for record in records:
        found = merge_record(record)
        if found is None or found[1] != today or found[0] in tasks or record.get("kind") not in UNDERWAY_KINDS:
            continue
        title, repo = record.get("title"), record.get("repo")
        rows.append({"id": found[0], "title": snapshot_text(title if isinstance(title, str) else None) or found[0],
                     "project": snapshot_text(repo if isinstance(repo, str) else None), "status": "Merged today",
                     "pr": pull_request_href(record.get("pr_url")), "merged_on": found[1]})
    return sorted(rows, key=lambda row: UNDERWAY_ORDER.index(row["status"]) if row["status"] in UNDERWAY_ORDER else 0)


def read_task_links(path: Path) -> dict[str, str]:
    """Task id to Quarterdeck item id from Firstmate's map. A missing or unreadable file means no links. The last line wins."""
    try:
        with path.open("rb") as handle:
            text = handle.read(TASK_LINKS_MAX_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return {}
    links = {}
    for line in text.splitlines():
        columns = line.split("\t")
        if len(columns) >= 2 and columns[0].strip() and ID_RE.fullmatch(columns[1].strip()):
            links[columns[0].strip()] = columns[1].strip().upper()
    return links


def underway_view(saved: SavedWorkers, links: dict[str, str], items: list[dict[str, object]]) -> dict[str, object]:
    """The saved reading shaped for the tab. Reading it never runs the snapshot."""
    view = saved.view()
    snapshot = view["snapshot"]
    work = snapshot.get("work") if isinstance(snapshot, dict) else None
    if not isinstance(work, list):
        return {"observed_at": None, "failed": view["failed"], "rows": []}
    conversations = {item["id"] for item in items if item["kind"] == "conversation"}
    today = local_today()
    rows = []
    for row in work:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("status"), str):
            continue
        if row["status"] == "Merged today" and row.get("merged_on") != today:
            continue
        target = links.get(row["id"])
        rows.append({**row, "conversation": item_page_name(target) if target in conversations else None})
    return {"observed_at": snapshot["observed_at"], "failed": view["failed"], "rows": rows}


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
html { -webkit-text-size-adjust: 100%; -webkit-tap-highlight-color: transparent; }
a, button, [role="tab"] { touch-action: manipulation; }
body { margin: 0; background: var(--ground); color: var(--ink); font: var(--text-body)/1.55 var(--serif); overflow-wrap: anywhere; }
a { color: inherit; text-decoration-thickness: 1px; text-underline-offset: .18em; }
a:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.bar { display: flex; gap: 1rem; align-items: baseline; justify-content: space-between; padding: .9rem var(--gutter);
  border-bottom: 1px solid var(--rule); font-family: var(--sans); }
.bar { flex-wrap: wrap; }
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
.list-nav { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin: 0 0 1.2rem; font-family: var(--sans); }
.list-nav.end { margin: 2rem 0 .75rem; }
.list-nav a { display: inline-flex; align-items: center; min-height: var(--tap); padding: .5rem .8rem; border: 1px solid var(--soft);
  border-radius: 6px; background: var(--raised); font: 600 var(--text-md)/1.3 var(--sans); text-decoration: none; }
.list-nav .next-link { border: 2px solid var(--ink); }
@media (hover: hover) { .list-nav a:hover { background: var(--faint); } }
.list-nav a:active { background: var(--faint); }
tr[id^="watch-"] { scroll-margin-top: 2rem; }
tr[id^="watch-"]:target { background: var(--faint); }
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
.msg { scroll-margin-top:1rem; }
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
.decision img,.thread img,.msg img { display:block; width:100%; height:auto; max-height:30rem; object-fit:contain; background:var(--faint);
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
.desk { margin:0 0 1rem; overflow-x:auto; font-family:var(--sans); }
.desk table { width:100%; border-collapse:collapse; text-align:left; }
.desk thead th { color:var(--soft); font-size:var(--text-sm); font-weight:500; }
.desk th, .desk td { padding:.7rem .5rem; border-bottom:1px solid var(--rule); vertical-align:middle; }
.desk tbody th { font-weight:600; }
.desk td { overflow-wrap:normal; }
.desk td.project { overflow-wrap:anywhere; }
.desk .work { position:relative; cursor:pointer; border-left:5px solid var(--rule); }
.desk .work.next { background:var(--signal-ground); }
@media (hover: hover) and (pointer: fine) {
  .desk .work:hover { background:var(--faint); }
  .desk .work.next:hover { background:var(--signal-ground); box-shadow:inset 0 0 0 2px var(--signal); }
}
.desk .work:active { background:var(--faint); }
.desk .work:has(.card-link:focus-visible) { outline:3px solid var(--focus); outline-offset:-3px; }
.desk .card-link { display:inline-block; padding:.35rem 0; }
.desk .card-link::after { content:""; position:absolute; inset:0; }
.desk .notice-project { display:none; }
.desk.underway td.project { min-width:8rem; }
.desk.underway th { overflow-wrap:anywhere; }
.underway-links { white-space:nowrap; }
.underway-links a { display:inline-block; margin-right:.9rem; padding:.35rem 0; }
@media (max-width: 40rem) {
  .desk.underway thead { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .desk.underway tbody tr { display:grid; grid-template-columns:auto 1fr; gap:.2rem .9rem; align-items:center;
    padding:.7rem .3rem; border-bottom:1px solid var(--rule); }
  .desk.underway tbody th { grid-column:1 / -1; padding:0; border:0; }
  .desk.underway tbody td { padding:0; border:0; }
  .underway-links { white-space:normal; }
}
.underway-status { margin:0 0 .5rem; }
.desk .notice p { margin:.25rem 0; font-weight:400; }
.desk .notice a { display:inline-block; margin-right:.65rem; }
.desk .notice .desk-feedback { max-width:16ch; overflow-wrap:anywhere; }
[data-read-all][hidden] + .desk-feedback { display:none; }
.sr, .desk-feedback:empty { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); white-space:nowrap; }
.button-link, .work-action { display:inline-flex; align-items:center; min-height:44px; padding:.5rem .85rem; border-radius:5px; font:600 var(--text-md)/1.3 var(--sans);
  text-decoration:none; border:1px solid var(--soft); background:var(--raised); color:var(--ink); }
.work-action { background:var(--ink); color:var(--raised); border-color:var(--ink); }
.section-head { display:flex; flex-wrap:wrap; gap:.5rem 1rem; align-items:center; justify-content:space-between; margin-top:2rem; }
.section-head h1 { margin:0; }
.workers-link { margin:1.5rem 0 0; font-family:var(--sans); }
#workers > .section-head:first-child { margin-top:0; }
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
#workers h2 { font:700 var(--text-md)/1.3 var(--sans); margin:1.4rem 0 0; }
.tags { display:flex; flex-wrap:wrap; gap:.3rem .5rem; list-style:none; margin:.35rem 0 0; padding:0; font-family:var(--sans); }
.tags li { font-size:var(--text-sm); color:var(--soft); border:1px solid var(--rule); border-radius:5px; padding:.05rem .5rem; }
.project-name { color:var(--soft); font-size:var(--text-sm); font-weight:400; }
.filters { display:flex; flex-wrap:wrap; gap:.6rem 1.2rem; align-items:end; margin:.6rem 0; font-family:var(--sans); }
.filters label { display:flex; flex-direction:column; gap:.2rem; min-width:0; max-width:100%; font-size:var(--text-sm); color:var(--soft); }
.filters select { min-height:var(--tap); min-width:min(100%,12rem); max-width:100%; font:var(--text-md)/1.3 var(--sans); padding:.4rem .6rem;
  background:var(--raised); color:var(--ink); border:1px solid var(--soft); border-radius:5px; }
.saved-answer .record:empty { display:none; }
.earlier-answers { margin:.5rem 0; overflow-wrap:anywhere; }
.earlier-answers summary { cursor:pointer; }
@media (max-width: 40rem) {
  .desk th, .desk td { padding:.6rem .3rem; }
  .desk-panel .desk .project, [aria-labelledby="waiting-title"] .desk .project { display:none; }
  .desk .notice-project { display:block; }
}
[hidden] { display:none !important; }
.bar nav { display:flex; flex-wrap:wrap; gap:.4rem 1rem; align-items:center; }
.bar nav a { margin-left:0; }
.bar button { min-height:40px; font-size:var(--text-sm); }
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
.lib-item.ref-row { display:flex; flex-wrap:wrap; align-items:baseline; gap:0 .9rem; padding:.4rem 0; }
.ref-title { font:500 var(--text-md)/1.4 var(--sans); }
.ref-more { display:inline-flex; flex-wrap:wrap; gap:0 .8rem; font-size:var(--text-sm); }
.ref-meta { display:inline-flex; flex-wrap:wrap; gap:0 .8rem; margin-left:auto; font-size:var(--text-sm); color:var(--soft); }
.ref-meta a, .ref-id { color:var(--soft); font:400 var(--text-sm)/1.4 var(--sans); }
.lib-item .meta { display:flex; flex-wrap:wrap; gap:0 1rem; font-size:var(--text-md); }
.page-section { margin:2rem 0 0; }
.page-head { display:flex; flex-wrap:wrap; gap:.6rem 1.5rem; align-items:center; justify-content:space-between; max-width:52rem; }
.page-head h1 { margin:0; }
.talk, .composer { max-width:52rem; margin:1rem 0; padding:clamp(1rem,3vw,1.5rem); background:var(--raised); border:1px solid var(--rule);
  border-radius:8px; font-family:var(--sans); }
.talk label, .composer label { margin:.8rem 0 .2rem; }
.composer label:first-child, .talk-field label { margin-top:0; }
.talk input { display:block; width:100%; min-height:var(--tap); background:var(--ground); color:var(--ink); border:1px solid var(--soft);
  border-radius:4px; padding:.55rem; font:inherit; }
.talk textarea { min-height:var(--tap); }
.talk { display:grid; gap:.8rem; grid-template-columns:minmax(0, 1fr); }
.talk .actions, .composer .actions { margin-top:.8rem; }
.talk .actions { margin:0; }
.talk p[id], .composer p[id] { margin:.4rem 0 0; color:var(--signal); font-weight:600; }
.talk p[id] { margin:0; }
.talk output { margin:0; }
.talk output:empty { display:none; }
.talk[hidden] { display:none; }
.talk-open { margin:1rem 0 0; }
.talk-field { display:flex; flex-direction:column; }
.talk-field input { flex:1; }
@media (width > 48rem) {
  .talk { grid-template-columns:minmax(10rem, 1fr) minmax(14rem, 2fr) auto; align-items:stretch; }
  .talk .actions { align-self:end; }
  .talk p[id], .talk output { grid-column:1 / -1; }
}
.desk-tabs { margin:1rem 0 1.2rem; border-bottom:1px solid var(--rule); }
.desk-tabs [role="tab"] { font-size:var(--text-md); margin-bottom:-1px; }
@media (max-width: 30rem) { .desk-tabs { flex-wrap:wrap; } .desk-tabs [role="tab"] { padding:.5rem .3rem; font-size:var(--text-sm); }
  .desk-tabs .tab-count { min-width:0; margin-left:.2rem; padding:0 .35rem; } }
.tab-count { display:inline-block; min-width:1.6em; margin-left:.3rem; padding:0 .45rem; border-radius:1em; text-align:center;
  font:600 var(--text-sm)/1.5 var(--sans); color:var(--signal); background:var(--signal-ground); }
.desk-panel > .empty { margin:1rem 0; }
.topics { max-width:52rem; }
.topics h2 { margin:1.6rem 0 .5rem; }
.topic-list { list-style:none; margin:0; padding:0; border-top:1px solid var(--rule); font-family:var(--sans); }
.topic { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:.4rem 1rem; align-items:center; padding:.8rem 0; border-bottom:1px solid var(--rule); }
.topic > .desk-feedback { grid-column:1 / -1; margin:0; }
.topic-main { min-width:0; flex:1; }
.topic-title { display:inline-block; padding:.2rem 0; font:700 var(--text-lg)/1.3 var(--serif); }
.topic-latest { margin:.1rem 0 .3rem; color:var(--soft); display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.topic-meta { display:flex; flex-wrap:wrap; gap:0 1rem; margin:0; }
.topic-meta .badge { font-size:var(--text-sm); }
.topic-actions { display:flex; flex-wrap:wrap; gap:.4rem; justify-content:flex-end; }
.topic-actions button { flex:none; min-width:5rem; }
.topic-actions button:active { background:var(--faint); }
@media (max-width: 30rem) { .topic { grid-template-columns:minmax(0,1fr); } .topic-actions { justify-content:flex-start; } }
.resolved-mark, .archived-mark { display:inline-flex; align-items:center; gap:.3rem; font:600 var(--text-sm)/1.5 var(--sans); color:var(--ink); }
.conversation-state { display:flex; flex-wrap:wrap; align-items:center; gap:.5rem 1rem; margin:.8rem 0 1rem; font-family:var(--sans); }
.conversation-state button { min-width:5rem; }
.conversation-state .state-status { flex-basis:100%; margin:0; }
.conversation-state .state-status:empty { display:none; }
.conversation-state .state-status.failed { color:var(--signal); font-weight:600; }
.topic > .move-form { grid-column:1 / -1; padding:.4rem 0 0; }
#scope[data-projects="no"] ~ .topics .move { display:none; }
.topic-meta .placement { color:var(--ink); font-weight:600; }
.scope { max-width:52rem; margin:1rem 0; display:flex; flex-wrap:wrap; gap:.6rem 1rem; align-items:end; font-family:var(--sans); }
.scope-field { display:flex; flex-direction:column; gap:.2rem; min-width:0; flex:1 1 9rem; max-width:18rem; }
.scope-field label { margin:0; font-size:var(--text-sm); color:var(--soft); }
.scope select, .move-form select, .group-form input { box-sizing:border-box; min-height:var(--tap); width:100%; padding:.4rem .6rem; font:var(--text-md)/1.3 var(--sans);
  background:var(--raised); color:var(--ink); border:1px solid var(--soft); border-radius:5px; }
.scope-actions, .group-form, .move-form { display:flex; flex-wrap:wrap; gap:.5rem 1rem; align-items:end; }
.group-form { flex:1 1 100%; }
.group-form .scope-field { flex:1 1 14rem; max-width:24rem; }
.move-form .actions { margin:0; }
.scope > .desk-feedback { flex:1 1 100%; margin:0; }
.talk-destination { margin:0; }
@media (width > 48rem) { .talk-destination { grid-column:1 / -1; } }
#desk-status { margin:.6rem 0 0; }
.tab-count[hidden] { display:none; }
.bar nav a[aria-current="page"] { font-weight:700; text-decoration-thickness:2px; }
.conversation .composer { margin:1rem 0 0; padding:1rem 0 0; background:none; border:0; border-top:1px solid var(--rule); border-radius:0; max-width:none; }
.conversation .exchange { margin:0; }
.queue { margin:1rem 0; }
.queue h3 { font:700 var(--text-lg)/1.3 var(--sans); margin:0 0 .5rem; }
.queue ul { list-style:none; margin:0 0 .75rem; padding:0; font-family:var(--sans); }
.queue li { padding:.6rem 0; border-top:1px solid var(--rule); overflow-wrap:anywhere; }
.queue li p { margin:.2rem 0; }
#item-ask-editor { margin:0 0 1rem; }
.clarified { border-left:3px solid var(--signal); padding:.1rem 0 .1rem .8rem; margin:.5rem 0 1rem; }
.clarified p { margin:.3rem 0; }
.clarified .who { font-family:var(--sans); }
.review { font-family:var(--sans); }
"""


TIME_SCRIPT = """
const localTime = dateTime => new Date(dateTime).toLocaleString(undefined, {dateStyle:'medium', timeStyle:'short'});
const localizeTimes = root => {
  for (const time of root.querySelectorAll('.saved-answer time[datetime]:not([datetime=""]), .earlier-answers time, .answer-source time, .thread time[datetime], .conversation time[datetime], .underway-status time[datetime]')) {
    time.textContent = localTime(time.dateTime);
  }
};
const localizeDays = root => {
  for (const time of root.querySelectorAll('time.day[datetime]:not([datetime=""])')) {
    time.textContent = new Date(time.dateTime).toLocaleDateString(undefined, {dateStyle:'medium'});
  }
};
const localizeStamps = root => {
  for (const time of root.querySelectorAll('time.stamp[datetime]:not([datetime=""])')) {
    time.textContent = localTime(time.dateTime);
  }
};
localizeTimes(document);
localizeDays(document);
localizeStamps(document);
"""


WORKER_SUMMARY_SCRIPT = r"""
const exceptions = new Set(['Question asked', 'Stuck', 'Unreachable']);
const make = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const workerSummary = worker => {
  const item = make('li');
  const head = make('div', 'head');
  head.append(make('strong', 'who', worker.name),
    make('span', exceptions.has(worker.status) ? 'status exception' : 'status', worker.status));
  item.append(head);
  if (worker.project) item.append(make('p', 'project', worker.project));
  item.append(make('p', 'doing', worker.activity));
  return item;
};
"""


WORKERS_SCRIPT = r"""
(() => {
  const section = document.querySelector('#workers');
  const button = section && section.querySelector('#refresh-workers');
  if (!button) return;
  const heading = section.querySelector('h1');
  const status = section.querySelector('#workers-status');
  const list = section.querySelector('#workers-list');
  let lastObservedAt = null;
  const stamped = (label, iso) => {
    const line = make('p', 'when', label + ' ');
    const time = make('time', '', localTime(iso));
    time.dateTime = iso;
    line.append(time);
    return line;
  };
  const row = (worker, mate) => {
    const item = workerSummary(worker);
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
    if (title) box.append(make('h2', '', rows.length > 1 ? title + ' (' + rows.length + ')' : title));
    const roster = make('ul', 'roster');
    for (const worker of rows) roster.append(row(worker, mate));
    box.append(roster);
    return box;
  };
  const lastChecked = prefix => {
    status.textContent = prefix + 'Last checked ';
    const time = make('time', '', localTime(lastObservedAt));
    time.dateTime = lastObservedAt;
    status.append(time);
  };
  const show = (data, prefix = '') => {
    heading.textContent = data.workers.length > 1 ? 'Workers (' + data.workers.length + ')' : 'Workers';
    list.replaceChildren();
    if (data.workers.length) list.append(group('', data.workers, false));
    else list.append(make('p', 'empty', 'No workers are running. A worker with a terminal shows here.'));
    if (data.second_mates.length) list.append(group('Second mates', data.second_mates, true));
    status.className = 'meta';
    lastObservedAt = data.observed_at;
    lastChecked(prefix);
  };
  const failed = () => {
    status.className = 'meta failed';
    status.textContent = lastObservedAt
      ? 'Check failed. Showing the list from ' + localTime(lastObservedAt) + '.'
      : 'Workers could not be loaded. Try again.';
  };
  const read = async path => {
    const response = await fetch(path, {cache: 'no-store', headers: {Accept: 'application/json'}});
    if (!response.ok) throw new Error('worker read failed');
    return response.json();
  };
  const load = async manual => {
    if (button.getAttribute('aria-disabled') === 'true') return;
    button.setAttribute('aria-disabled', 'true');
    list.setAttribute('aria-busy', 'true');
    status.className = 'meta';
    status.textContent = lastObservedAt ? 'Refreshing workers' : 'Loading workers';
    try {
      if (manual) {
        show(await read('workers.json'));
      } else {
        const saved = await read('workers-saved.json');
        if (!saved.snapshot) show(await read('workers.json?reuse=1'));
        else {
          show(saved.snapshot);
          if (saved.failed) failed();
        }
      }
    } catch {
      failed();
    } finally {
      button.removeAttribute('aria-disabled');
      list.removeAttribute('aria-busy');
    }
  };
  button.addEventListener('click', () => load(true));
  load(false);
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


# A list's Back link opens the Desk with the list named in the address: ?tab= for a Desk tab, ?hold= for a Hold tab,
# and ?row= for the item whose row comes into view. The address is cleaned once the scripts have read it.
ARRIVAL_SCRIPT = r"""
const arrival = new URLSearchParams(location.search);
const arrivalRow = (rows, id) => rows.find(row => row.dataset.id === id) ?? rows.find(row => row.dataset.item === id);
const showArrivalRow = (row, link) => {
  if (!row) return;
  row.scrollIntoView({block: 'center'});
  link?.focus({preventScroll: true});
};
"""

ARRIVAL_END_SCRIPT = r"""
if (['tab', 'hold', 'row'].some(name => arrival.has(name))) {
  history.replaceState(history.state, '', location.pathname + location.hash);
}
"""

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
  const arrivingTab = tabs.find(tab => tab.id === 'tab-' + arrival.get('hold'));
  const libraryLookup = arrivingTab ? {tab: arrivingTab.id, query: arrival.get('search') ?? '',
    filters: {project: arrival.get('project') ?? '', tag: arrival.get('tag') ?? ''}} : history.state?.libraryLookup;
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
               && e.target.closest('a.lib-title, a.ref-title, a.ref-extra')) {
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
    const linkContext = () => {
      const params = {project: '', tag: '', search: (topics ? normalizeTopic(search.value) : search.value.trim()).slice(0, 200)};
      for (const select of filters) params[select.name] = select.value;
      for (const link of panel.querySelectorAll('a[data-detail]')) {
        const [address, hash] = link.getAttribute('href').split('#');
        const [path, query] = address.split('?');
        const next = new URLSearchParams(query);
        for (const [name, value] of Object.entries(params)) {
          if (value) next.set(name, value); else next.delete(name);
        }
        link.setAttribute('href', path + '?' + next + (hash === undefined ? '' : '#' + hash));
      }
    };
    const run = () => {
      const rows = panel.querySelectorAll('.lib-item');
      const query = topics ? normalizeTopic(search.value) : search.value.trim();
      const id = idQuery.exec(query);
      const terms = caseFold(query).split(topics ? ' ' : /\s+/);
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
      linkContext();
      if (!panel.hidden) updateScrollMode();
    };
    if (topics) panel.addEventListener('conversation-saved', event => {
      const list = panel.querySelector('.lib-list');
      const {id, html} = event.detail;
      list.querySelector('[data-id="' + id + '"]')?.remove();
      if (!html) {
        run();
        return;
      }
      const next = [...list.children].find(row => BigInt(row.dataset.id.slice(1)) < BigInt(id.slice(1)));
      if (next) next.insertAdjacentHTML('beforebegin', html);
      else list.insertAdjacentHTML('beforeend', html);
      localizeDays(list.querySelector('[data-id="' + id + '"]'));
      run();
    });
    search.addEventListener('input', run);
    for (const select of filters) select.addEventListener('change', run);
    run();
  }
  if (restoredTab) {
    select(restoredTab, false);
    openLibrary();
    if (arrivingTab) {
      const row = arrivalRow([...panelOf(arrivingTab).querySelectorAll('.lib-item:not([hidden])')], arrival.get('row'));
      showArrivalRow(row, row?.querySelector('a[data-detail]'));
    }
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


# --- List context ------------------------------------------------------------------------
# A detail page opened from a list carries that list in its address, so Back and Next unread need no session.


class ListKind(NamedTuple):
    label: str  # the list's name, as the Back link says it
    page: str   # the page that shows the list: "desk", "hold" or "watch"
    tab: str    # its tab on that page
    filters: tuple[str, ...] = ()  # the list's filters, which its links carry


HOLD_FILTERS = ("project", "tag", "search")
DESK_SCOPE = ("project", "topic")
LIST_KINDS = {
    "needs-you": ListKind("Needs you", "desk", "needs-you"),
    "conversations": ListKind("Conversations", "desk", "conversations", DESK_SCOPE),
    "archived": ListKind("Archived", "desk", "archived"),
    "watch": ListKind("Watch", "watch", ""),
    "references": ListKind("References", "hold", "references", HOLD_FILTERS),
    "answered": ListKind("Answered", "hold", "answered", HOLD_FILTERS),
    "hold-conversations": ListKind("Hold conversations", "hold", "conversations", HOLD_FILTERS),
}
FILTER_VALUE_LIMIT = 200


def parse_list_context(query: str) -> dict[str, str] | None:
    """The list a page was opened from, read from its address. None when the address names no list that exists."""
    try:
        fields = parse_qs(query, max_num_fields=12)
    except ValueError:
        return None
    name = fields.get("from", [""])[0]
    kind = LIST_KINDS.get(name)
    if kind is None:
        return None
    context = {"from": name}
    for key in kind.filters:
        value = fields.get(key, [""])[0][:FILTER_VALUE_LIMIT]
        if value:
            context[key] = value
    return context


def item_link(item_id: str, context: dict[str, str] | str | None = None, target: str | None = None) -> str:
    """The address of an item's page. With a list it names, the page can send the owner back to that list."""
    href = item_page_name(item_id)
    if context:
        href += "?" + urlencode({"from": context} if isinstance(context, str) else context)
    return href + (f"#{target}" if target else "")


def back_link_href(context: dict[str, str], item_id: str) -> str:
    """The address of the list a page was opened from, with the item's row in view."""
    kind = LIST_KINDS[context["from"]]
    if kind.page == "watch":
        return f"watch.html#watch-{item_id}"
    filters = {key: context[key] for key in kind.filters if key in context}
    return "index.html?" + urlencode({"tab" if kind.page == "desk" else "hold": kind.tab, **filters, "row": item_id})


ID_QUERY_RE = re.compile(r"(q[0-9]+)(?:[.]([0-9]+))?", re.IGNORECASE)


def library_search_text(item) -> str:
    """The folded words that a search in a Hold list matches an item on."""
    search = [item["title"]]
    if item["kind"] != "conversation":
        search.append(item["description"])
    if item["kind"] == "questions":
        search.extend(q["question"] for q in item["body"]["questions"])
        search.extend(part_record(item, n).get("answer", "") for n in question_parts(item))
    return " ".join(search).casefold()


def library_matches(item, filters: dict[str, str], *, topics: bool) -> bool:
    """Whether an item shows in a Hold list under these filters, as the list's own script decides it."""
    if filters.get("project") and item.get("project", "") != filters["project"]:
        return False
    if filters.get("tag") and filters["tag"] not in all_tags(item):
        return False
    query = " ".join(filters.get("search", "").split()) if topics else filters.get("search", "").strip()
    if not query:
        return True
    id_match = ID_QUERY_RE.fullmatch(query)
    if id_match and item["id"].lower() == id_match[1].lower():
        number = id_match[2]
        if number is None or (item["kind"] == "questions" and number.isdigit() and 1 <= int(number) <= len(question_parts(item))):
            return True
    return (not id_match or topics) and all(term in library_search_text(item) for term in query.casefold().split())


def list_members(data: dict[str, object], context: dict[str, str]) -> list[dict[str, object]]:
    """The items a list shows, in the order it shows them."""
    items = newest_first(data["items"])  # type: ignore[arg-type]
    name = context["from"]
    if name == "needs-you":
        return [item for item in items if attention_summary(item, served=True)]
    if name == "watch":
        return [row[0] for row in watch_requests(data)]
    if name in ("conversations", "archived"):
        wanted = name == "archived"
        members = [item for item in items if item["kind"] == "conversation" and is_archived(item) == wanted]
        return list_sorter(name)([item for item in members if in_desk_scope(data, context, item)])
    kinds = {"references": ("resource", "list"), "answered": ("questions",), "hold-conversations": ("conversation",)}[name]
    members = [item for item in items if item["kind"] in kinds and (
        name != "answered" or not desk_item(item)) and not (name == "hold-conversations" and is_archived(item))]
    return [item for item in members if library_matches(item, context, topics=name == "hold-conversations")]


def in_desk_scope(data: dict[str, object], context: dict[str, str], item) -> bool:
    """Whether a conversation shows under the Desk's project and topic scope, as the Desk's script decides it."""
    project = context.get("project", "")
    if project == "-":
        return not item.get("placement")
    found = find_project(data.get("projects", []), project)  # type: ignore[arg-type]
    if found is None:
        return True
    placement = item.get("placement") or {}
    topic = context.get("topic", "")
    if not any(entry["id"] == topic for entry in found["topics"]):  # type: ignore[attr-defined]
        topic = ""
    return placement.get("project") == project and (not topic or placement.get("topic") == topic)


def list_sorter(name: str):
    """How a list orders items, or None when it orders them by something an item does not carry on its own."""
    if name in ("conversations", "archived"):
        def sort_conversations(items):
            ordered = by_activity(items)
            return [i for i in ordered if is_pinned(i)] + [i for i in ordered if not is_pinned(i)] if name == "conversations" else ordered
        return sort_conversations
    return None if name == "watch" else newest_first


def list_unread(name: str, item) -> bool:
    """Needs you holds only what still needs the owner; every other list counts an unread reply."""
    return name == "needs-you" or unread_target(item) is not None


def attention_target(item, summary) -> str | None:
    """Where an item's page opens: its first unread reply, else its first open question."""
    state, open_parts, _ = summary
    if state == "Replied":
        return unread_target(item)
    return f'{item["id"]}.{open_parts[0]}' if open_parts else None


def next_unread(data: dict[str, object], context: dict[str, str], current) -> tuple[dict[str, object], str | None] | None:
    """The next unread item after the current one in its list, wrapping to the start, and where its page opens."""
    name = context["from"]
    line = list_members(data, context)
    if all(item["id"] != current["id"] for item in line):
        sorter = list_sorter(name)
        line = sorter([*line, current]) if sorter else [current, *line]
    at = next(number for number, item in enumerate(line) if item["id"] == current["id"])
    for item in (*line[at + 1:], *line[:at]):
        if list_unread(name, item):
            summary = attention_summary(item, served=True) if name == "needs-you" else None
            return item, attention_target(item, summary) if summary else unread_target(item)
    return None


CONVERSATION_TEXT_SCRIPT = r"""
const trimmed = value => value.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, '');
const normalizeTopic = value => trimmed(value.replace(/[\p{White_Space}\u001c-\u001f]+/gu, ' '));
"""


def nav_links(saved_href, current, served):
    links = [("desk", "Desk", saved_href)]
    if served:
        links += [("watch", "Watch", "watch.html"), ("workers", "Workers", "workers.html")]
    return "".join(f'<a href="{href}"{" aria-current=\"page\"" if key == current else ""}>{label}</a>'
                   for key, label, href in links)


def page_shell(title: str, site_title: str, body: str, *, served: bool, saved_name: str, interactive: bool = False,
               workers: bool = False, library: str = "", conversations: bool = False, filters: bool = False,
               talking: bool = False, starting: bool = False, current: str = "", watching: bool = False) -> str:
    saved_href = quote(saved_name, safe="")
    opener = ('<button type="button" id="open-library" aria-haspopup="dialog" aria-controls="library">Open Hold</button>'
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
        f'<nav>{nav_links(saved_href, current, served)}{opener}</nav></header>\n'
        f"<main>\n{body}\n</main>\n{library}<script>{ARRIVAL_SCRIPT if library or starting else ''}{TIME_SCRIPT}{VIEWPORT_SCRIPT if interactive or library else ''}{QUESTION_SCRIPT if conversations else ''}"
        f"{CONVERSATION_TEXT_SCRIPT if library or talking or starting else ''}"
        f"{ANSWER_SCRIPT if interactive else ''}{CONVERSATION_SCRIPT if talking else ''}{DESK_SCRIPT if starting else ''}"
        f"{WORKER_SUMMARY_SCRIPT if workers or watching else ''}"
        f"{WORKERS_SCRIPT if workers else ''}{WATCH_SCRIPT if watching else ''}{FILTER_SCRIPT if filters or library else ''}"
        f"{f'const libraryCaseFolds = {library_case_folds()};' + LIBRARY_SCRIPT if library else ''}"
        f"{ARRIVAL_END_SCRIPT if library or starting else ''}</script>\n</body>\n</html>\n"
    )


def delivery_label(record):
    return {"sent": "Sent", "pending": "Not delivered", "cancelled": "Sending stopped"}[record["delivery"]]


def question_summary(item, *, replies=True) -> tuple[str, list[int], bool]:
    states = [part_record(item, n).get("state", "open") for n in question_parts(item)]
    open_parts = [n for n, state in enumerate(states, 1) if state == "open"]
    state = "Open" if open_parts else ("Closed" if all(s == "closed" for s in states) else "Answered")
    if replies and unread_target(item):
        state = "Replied"  # the internal name; the page shows UNREAD_REPLY
    pending = any(delivery_pending(r) for r in item.get("answers", {}).values())
    return state, open_parts, pending


def progress_text(item, summary):
    """The one line of progress: replied first, then how many questions are still open."""
    state, open_parts, _ = summary
    remaining = f"{len(open_parts)} of {len(question_parts(item))} open" if open_parts else ""
    if state == "Replied":
        return f"{UNREAD_REPLY}, {remaining}" if remaining else UNREAD_REPLY
    return remaining or state


def desk_row(item, summary, *, primary, context=None):
    state, open_parts, pending = summary
    href = esc(item_link(item["id"], context, attention_target(item, summary)))
    title = esc(item["title"])
    project = esc(item["project"]) if item.get("project") else ""
    remaining = len(open_parts)
    count = f'{remaining} of {len(question_parts(item))} open' if remaining else "None open"
    latest = (f'<span class="badge">{UNREAD_REPLY}</span>' if state == "Replied" else
              "Asked" if remaining else esc(state))
    if pending:
        failure = '<span>Not delivered</span>'
        latest = failure if state == "Messaged" else f'{latest} {failure}'
    return (f'<tr class="work{' next' if primary else ''}"{f' data-id="{item["id"]}"' if context else ""}><th scope="row">'
            f'<a class="card-link" href="{href}"><span class="id">{item["id"]}</span> {title}</a></th>'
            f'<td class="project">{project}</td><td>{count}</td><td>{latest}</td></tr>')


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


def message_html(item, message, *, editable, message_number=None):
    you = message["author"] == "you"
    when = esc(message["at"])
    delivery = f' <span class="delivery">{delivery_label(message)}</span>' if you else ''
    controls = ''
    if editable and you and message["delivery"] == "pending":
        controls = ('<div class="actions">'
                    f'<button type="button" data-retry-ask="{message["id"]}">'
                    f'{"Send message again" if item["kind"] == "conversation" else "Send ask again"}</button></div>')
    identity = (f' data-ask-id="{message["id"]}"' if you
                else f' data-reply-id="{message["id"]}" id="reply-{message["id"]}" tabindex="-1"')
    reference = ""
    if message_number is not None:
        if you:
            identity += f' id="message-{message_number}" tabindex="-1"'
            reference = f' <span>Message {message_number}</span>'
        else:
            reference = f' <a href="#message-{message_number}">In reply to message {message_number}</a>'
    elif not you and "ask" not in message:
        reference = ' <span>Firstmate update</span>'
    unread = " data-unread" if unread_reply(message) else ""
    return (f'<li class="msg {message["author"]}"{identity}{unread}><p class="who"><strong>{AUTHOR_LABELS[message["author"]]}</strong> '
            f'<time datetime="{when}">{when}</time>{delivery}{reference}</p><p class="record">{linked_text(message["text"])}</p>'
            f'{links_html(message)}{shots_html(item, message, tree=REPLY_IMAGES)}{controls}</li>')


def thread_html(item, number, *, editable):
    if item["kind"] == "conversation":
        thread = part_thread(item, number)
        numbers = conversation_message_numbers(thread)
        home = thread_home(thread) if editable else None
        messages = ''.join(message_html(item, m, editable=editable and m.get("home") == home,
                                        message_number=numbers.get(m.get("ask", m["id"])))
                           for m in thread)
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
                     'aria-expanded="false">Discuss this set</button>')
    if any(q.get("suggestion") and conversation_editable(item, n)
           for n, q in enumerate(item["body"]["questions"], 1)):
        controls += '<button id="suggest">Use all suggestions</button>'
    controls += '<button id="undo" hidden>Undo changes</button>'
    return f'<div class="sendbar"><div class="actions">{controls}</div><output role="status" tabindex="0"></output></div>'


def queue_panel(target, ask_binding):
    """The mixed send queue: answers drafted on the cards and messages about the set, sent together."""
    return (f'<div id="item-ask-editor" data-home="{esc(ask_binding)}" hidden>'
            '<section class="queue" aria-labelledby="queue-title"><h3 id="queue-title">Queued to send (<span id="queue-count">0</span>)</h3>'
            '<ul id="queue-list"></ul><p id="queue-empty">Your queue is empty.</p>'
            '<div class="actions"><button type="button" id="send-queue" class="primary" disabled>Send queued items</button></div></section>'
            f'<div class="queue-form"><label for="ask-{target}">Message about this set</label>'
            f'<textarea id="ask-{target}" maxlength="{TEXT_LIMIT}" rows="2" aria-describedby="error-{target}"></textarea>'
            f'<p id="error-{target}" hidden></p><div class="actions">'
            '<button type="button" id="queue-add" disabled>Queue item</button></div></div></div>')


def item_conversation(item, ask_binding):
    asking = ask_binding is not None
    target = item["id"]
    thread = conversation_thread(item, None, target, editable=asking)
    if not thread:
        return ""
    unread = thread_unread(item, None)
    badge = f'<span class="badge">{UNREAD_REPLY}</span>' if unread else '<span class="badge" hidden></span>'
    editor = queue_panel(target, ask_binding) if asking else ''
    return (f'<section id="{target}" class="work item-conversation" data-conversation aria-labelledby="item-conversation-title">'
            f'<div class="eyebrow">{badge}</div><h2 id="item-conversation-title">Discuss this set</h2>'
            f'{editor}{thread}</section>')


def conversation_state(item):
    """The resolved mark and the controls that resolve, reopen, archive and unarchive one conversation."""
    resolved, archived = is_resolved(item), is_archived(item)
    title = esc(item["title"])
    return (f'<div class="conversation-state" data-id="{item["id"]}" data-title="{title}" '
            f'data-resolved="{"true" if resolved else "false"}" data-archived="{"true" if archived else "false"}">'
            f'{resolved_mark(item)}<span class="archived-mark"{"" if archived else " hidden"}>Archived</span>'
            f'<button type="button" class="resolve">{"Reopen" if resolved else "Resolve"}</button>'
            f'<button type="button" class="archive">{"Unarchive" if archived else "Archive"}</button>'
            '<p class="meta state-status" role="status"></p></div>')


def conversation_page(item, ask_binding):
    """A conversation: every message and reply in time order, and a box for the next message."""
    target = item["id"]
    editable = bool(ask_binding) and conversation_home_matches(item, ask_binding)
    thread = thread_html(item, None, editable=editable)
    unread = thread_unread(item, None)
    badge = f'<span class="badge">{UNREAD_REPLY}</span>' if unread else '<span class="badge" hidden></span>'
    if editable:
        composer = (f'<div class="composer"><label for="message-{target}">Your message</label>'
                    f'<textarea id="message-{target}" maxlength="{TEXT_LIMIT}" rows="3" aria-describedby="error-{target}"></textarea>'
                    f'<p id="error-{target}" hidden></p><div class="actions"><button type="button" class="primary" id="send-message" '
                    f'data-home="{esc(ask_binding)}" disabled>Send message</button><output role="status" tabindex="0"></output></div></div>')
    elif ask_binding:
        composer = '<p class="empty">Restore the original firstmate_home to send or retry messages.</p>'
    else:
        composer = '<p class="empty">Set firstmate_home to send Firstmate more messages.</p>'
    return (f'<section id="{target}" class="work item-conversation conversation" data-conversation>'
            f'<div class="eyebrow">{badge}</div><div class="thread-messages">{thread}</div>{composer}</section>')


def clarified_html(notes, editable):
    """What the set discussion clarified, shown at the question it affects; a changed option holds the draft for review."""
    revision = latest_option_change(notes).get("reply_id", "")
    html = ''.join(f'<p class="record">{linked_text(note["text"])}</p>' for note in notes)
    review = ""
    if editable and revision:
        review = ('<p class="review" role="status"><strong>An option changed.</strong> Review your draft before sending. '
                  '<button type="button" class="keep-draft">Keep this draft</button></p>')
    return (f'<div class="clarified" data-notes="{len(notes)}" data-option-revision="{revision}"'
            f'{"" if notes else " hidden"}><p class="who"><strong>Clarified after discussion</strong></p>{html}{review}</div>')


def set_discussion(item, *, editable):
    return {"thread": thread_html(item, None, editable=editable),
            "clarifications": {f'{item["id"]}.{number}': clarified_html(notes, conversation_editable(item, number))
                               for number in question_parts(item) if (notes := clarifications(item, number))}}


def earlier_answers_html(record):
    earlier = record.get("earlier_answers", [])
    if not earlier:
        return ""
    entries = []
    for entry in reversed(earlier):
        at = esc(entry["answered_at"])
        entries.append(f'<li><p class="record">{esc(entry["answer"])}</p>'
                       f'<p class="meta"><time datetime="{at}">{at}</time></p></li>')
    return ('<details class="earlier-answers meta"><summary>Earlier answers</summary><ol>'
            + ''.join(entries) + '</ol></details>')


def question_form(item, served, *, ask_binding=None):
    cards, folded = [], []
    asking = ask_binding is not None
    for number, question in enumerate(item["body"]["questions"], 1):
        part = f'{item["id"]}.{number}'
        record = part_record(item, number)
        status = record.get("state", "open")
        label = part_state(item, number, replies=served).capitalize()
        if label == "Replied":
            label = UNREAD_REPLY
        answer = record.get("answer", "")
        editable = served and status != "closed"
        notes = clarifications(item, number) if served else []
        saved = ""
        if answer or editable:
            at = esc(record.get("answered_at", ""))
            delivery = delivery_label(record) if answer else ""
            hidden = "" if answer else " hidden"
            saved = (f'<div class="saved-answer"{hidden}><p class="record">{esc(answer)}</p>'
                     f'<p class="meta"><span class="delivery">{delivery}</span> '
                     f'<time datetime="{at}">{at}</time></p></div>')
            if served:
                saved += f'<div class="answer-history">{earlier_answers_html(record)}</div>'
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
                       f' data-pending="{int(delivery_pending(record))}"'
                       f'{" data-review" if option_review_required(latest_option_change(notes), record) else ""}')
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
        clarified = clarified_html(notes, editable) if served else ""
        card = (f'<section class="decision" id="{part}" aria-labelledby="title-{part}"{editor}>'
                f'<div class="eyebrow"><span class="id">{part}</span><span class="badge">{label}</span>'
                f'{f"<span>{esc(question['kind'].capitalize())}</span>" if question.get("kind") else ""}</div>'
                f'{tag_labels(question.get("tags", []))}'
                f'<h2 id="title-{part}">{linked_text(question["question"])}</h2><p>{linked_text(question["context"])}</p>'
                f'{clarified}{shots_html(item, question)}{saved}{controls}{thread}</section>')
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


QUESTION_SCRIPT = r"""
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
  const replied = [...replyStates.keys()].some(s => s.querySelector('.badge').textContent === 'Unread reply');
  const remaining = sections.filter(s => s.dataset.state === 'open').length;
  const open = remaining ? remaining + ' of ' + sections.length + ' open' : '';
  const state = sections.every(s => s.dataset.state === 'closed') ? 'Closed' : 'Answered';
  progress.textContent = replied ? ['Unread reply', open].filter(Boolean).join(', ') : (open || state);
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
  badge.textContent = unread ? 'Unread reply' : state ? state[0].toUpperCase() + state.slice(1) : '';
  badge.hidden = !badge.textContent;
  if (unread) expandAnswer(section);
  updateProgress();
}
async function acknowledgeReplies(section, shown) {
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
const unreadReplyIds = root => [...root.querySelectorAll('[data-reply-id][data-unread]')].map(reply => reply.dataset.replyId);
const whole = document.querySelector('.item-conversation:not(.conversation)');
let sending = false;
let discussionRequest;
let refreshAnswerControls;
const answerText = value => value.replace(/^[\p{White_Space}\x1c-\x1f]+|[\p{White_Space}\x1c-\x1f]+$/gu, '');
const answerDraft = f => {
  const answer = answerText(f.value);
  return answer && (answer !== f.defaultValue || f.closest('section').dataset.pending === '1');
};
const needsReview = section => section.hasAttribute('data-review');
function updateClarification(section, html) {
  const current = section.querySelector('.clarified');
  const field = section.querySelector('textarea');
  const template = document.createElement('template');
  template.innerHTML = html;
  const next = template.content.firstElementChild;
  if (Number(next.dataset.notes) <= Number(current.dataset.notes)) return;
  if (field && next.dataset.optionRevision !== current.dataset.optionRevision) {
    section.setAttribute('data-review', '');
    const folded = section.closest('details.done');
    if (folded && answerDraft(field)) folded.open = true;
  }
  const restoreFocus = current.contains(document.activeElement);
  const review = next.querySelector('.review');
  if (review) review.hidden = !needsReview(section);
  current.replaceWith(next);
  if (restoreFocus) (next.querySelector('.review:not([hidden]) .keep-draft') || section).focus();
  return true;
}
function updateClarifications(clarifications) {
  let changed = false;
  for (const [id, html] of Object.entries(clarifications)) {
    if (updateClarification(document.getElementById(id), html)) changed = true;
  }
  if (changed) refreshAnswerControls?.();
}
function updateThread(section, html) {
  const thread = section.querySelector('.thread');
  const messages = thread.querySelector('.thread-messages');
  const next = messages.cloneNode(false);
  const preview = thread.cloneNode(false);
  preview.append(next);
  next.innerHTML = html;
  if (section === whole && !thread.querySelector('.pending-asks')) {
    for (const button of next.querySelectorAll('[data-retry-ask]')) button.closest('.actions').remove();
  }
  localizeTimes(preview);
  if (section !== whole || !messages.isEqualNode(next)) {
    const restoreFocus = messages.contains(document.activeElement);
    messages.replaceChildren(...next.childNodes);
    if (restoreFocus) thread.querySelector('summary').focus();
  }
  const saved = new Set([...messages.querySelectorAll('[data-ask-id]')].map(ask => ask.dataset.askId));
  for (const pending of thread.querySelectorAll('[data-local-ask]')) {
    if (saved.has(pending.dataset.localAsk)) {
      if (pending.contains(document.activeElement)) thread.querySelector('summary').focus();
      pending.remove();
    }
  }
  thread.hidden = !thread.querySelector('.thread-messages > *, .pending-asks > *');
  reconcileReplies(section);
}
if (whole) {
  setInterval(async () => {
    if (document.hidden || sending || discussionRequest) return;
    const request = discussionRequest = new AbortController();
    try {
      const response = await fetch(whole.id.toLowerCase() + '.discussion.json', {signal: request.signal});
      if (!response.ok) return;
      const result = await response.json();
      if (request.signal.aborted || sending) return;
      updateThread(whole, result.thread);
      updateClarifications(result.clarifications);
    } catch {
      return;
    } finally {
      discussionRequest = null;
    }
  }, 10000);
}
(() => {
  const target = document.getElementById(location.hash.slice(1));
  if (!target?.hasAttribute('data-reply-id')) return;
  const section = target.closest('[data-conversation]');
  expandAnswer(section);
  const thread = target.closest('details.thread');
  if (thread) thread.open = true;
  const focusReply = () => setTimeout(() => target.focus(), 0);
  if (document.readyState === 'complete') focusReply();
  else addEventListener('load', focusReply, {once:true});
  if (target.hasAttribute('data-unread')) acknowledgeReplies(section, [target.dataset.replyId]);
})();
(() => {
  // Without a #fragment that names something on the page, open on the first unread message, else the latest, among those shown.
  if (document.getElementById(location.hash.slice(1))) return;
  const navigation = performance.getEntriesByType?.('navigation')[0]?.type;
  if (navigation === 'reload' || navigation === 'back_forward') return;
  const hidden = document.querySelector('[data-reply-id][data-unread]');
  if (hidden) {
    const section = hidden.closest('[data-conversation]');
    expandAnswer(section);
    const thread = hidden.closest('details.thread');
    if (thread && !thread.open) {
      thread.open = true;
      acknowledgeReplies(section, unreadReplyIds(thread));
    }
  }
  const shown = [...document.querySelectorAll('.msg')].filter(message => message.getClientRects().length);
  const unread = shown.find(message => message.hasAttribute('data-unread'));
  const stamp = message => Date.parse(message.querySelector('time')?.dateTime) || 0;
  const latest = shown.reduce((best, message) => !best || stamp(message) >= stamp(best) ? message : best, null);
  const target = unread || latest;
  if (!target) return;
  let moved = false;
  const land = () => {
    if (moved) return;
    const box = target.getBoundingClientRect();
    if (box.top >= 0 && box.bottom <= innerHeight) return;
    target.scrollIntoView({block:'start', behavior:'instant'});
  };
  for (const name of ['wheel', 'touchstart', 'keydown', 'pointerdown']) {
    addEventListener(name, () => { moved = true; }, {once:true, passive:true});
  }
  land();
  if (document.readyState !== 'complete') addEventListener('load', land, {once:true});
})();
document.addEventListener('click', event => {
  const summary = event.target.closest('.thread > summary');
  if (!summary || summary.parentElement.open || event.defaultPrevented) return;
  event.preventDefault();
  summary.parentElement.open = true;
  acknowledgeReplies(summary.closest('[data-conversation]'), unreadReplyIds(summary.parentElement));
});
"""


def talk_script(body):
    """Shared by the Desk form and a conversation's message box: one POST of a message with its own ID."""
    return r"""
const messageId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
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
  reconcileReplies(section);
  const field = section.querySelector('textarea');
  const button = section.querySelector('#send-message');
  if (!field || !button) return;
  const output = section.querySelector('output');
  const error = document.getElementById(field.getAttribute('aria-describedby'));
  const messages = section.querySelector('.thread-messages');
  const drafts = new Map();
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
      if (outcome.ok || outcome.saved === false) {
        for (const [text, id] of drafts) {
          if (id === body.id) drafts.delete(text);
        }
      }
      if (outcome.ok) {
        messages.innerHTML = outcome.result.thread;
        localizeTimes(messages);
        reconcileReplies(section);
        output.textContent = outcome.result.message;
        if (body.text !== undefined) field.value = '';
      } else {
        output.textContent = '';
        show(outcome.message);
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
    const retryId = drafts.get(text);
    const id = retryId ?? messageId();
    drafts.set(text, id);
    const body = {part:section.id, id, text, home:button.dataset.home};
    send(retryId ? {...body, action:'retry'} : body);
  });
  section.addEventListener('click', event => {
    const retry = event.target.closest('[data-retry-ask]');
    if (retry) send({part:section.id, id:retry.dataset.retryAsk, action:'retry'});
  });
  refresh();
})();
(() => {
  const bar = document.querySelector('.conversation-state');
  if (!bar) return;
  const status = bar.querySelector('.state-status');
  const mark = bar.querySelector('.resolved-mark');
  const archivedMark = bar.querySelector('.archived-mark');
  const words = {
    resolved: {labels: ['Resolve', 'Reopen'], done: ['is reopened.', 'is resolved.']},
    archived: {labels: ['Archive', 'Unarchive'], done: ['is unarchived.', 'is archived.']}
  };
  bar.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    const key = button.classList.contains('archive') ? 'archived' : 'resolved';
    const value = bar.dataset[key] !== 'true';
    const done = words[key].done[value ? 1 : 0];
    const hadFocus = document.activeElement === button;
    button.disabled = true;
    let saved = false;
    try {
      saved = (await postJson(key === 'archived' ? 'archives' : 'resolutions', {part:bar.dataset.id, [key]:value})).ok;
    } catch {
      saved = false;
    }
    button.disabled = false;
    status.classList.toggle('failed', !saved);
    if (saved) {
      bar.dataset[key] = String(value);
      button.textContent = words[key].labels[value ? 1 : 0];
      mark.hidden = bar.dataset.resolved !== 'true';
      archivedMark.hidden = bar.dataset.archived !== 'true';
    }
    status.textContent = saved ? bar.dataset.title + ' ' + done
      : 'Quarterdeck could not save the change to ' + bar.dataset.title + '. Try again.';
    if (hadFocus) button.focus();
  });
})();
""")


DESK_SCRIPT = talk_script(r"""
(() => {
  const tabs = [...document.querySelectorAll('.desk-tabs [role="tab"]')];
  const panelOf = tab => document.getElementById(tab.getAttribute('aria-controls'));
  const select = (tab, focus) => {
    for (const other of tabs) {
      const on = other === tab;
      other.setAttribute('aria-selected', String(on));
      other.tabIndex = on ? 0 : -1;
      panelOf(other).hidden = !on;
    }
    if (focus) tab.focus();
  };
  for (const tab of tabs) {
    tab.addEventListener('click', () => select(tab, false));
    tab.addEventListener('keydown', event => {
      const at = tabs.indexOf(tab);
      const next = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[tabs.length - 1]
        : event.key === 'ArrowRight' ? tabs[(at + 1) % tabs.length]
        : event.key === 'ArrowLeft' ? tabs[(at + tabs.length - 1) % tabs.length] : null;
      if (next) { event.preventDefault(); select(next, true); }
    });
  }
  const startStatus = document.getElementById('desk-status');
  const report = (target, panel, text, failed) => {
    for (const node of panel.hidden ? [target, startStatus] : [target]) {
      node.classList.toggle('failed', failed);
      node.textContent = text;
    }
  };
  const conversationPanel = document.getElementById('desk-panel-conversations');
  const attentionPanel = document.getElementById('desk-panel-needs-you');
  const attentionRows = attentionPanel.querySelector('tbody');
  const attentionTable = attentionPanel.querySelector('.desk');
  const attentionEmpty = attentionPanel.querySelector('.empty');
  const attentionTab = document.getElementById('desk-tab-needs-you');
  const count = attentionTab.querySelector('.tab-count');
  const noticeButtons = () => [...attentionRows.querySelectorAll('button[data-read]')];
  const readAll = attentionPanel.querySelector('[data-read-all]');
  const syncAttention = () => {
    const total = attentionRows.rows.length;
    count.hidden = attentionTable.hidden = !total;
    count.textContent = total || '';
    attentionEmpty.hidden = Boolean(total);
    readAll.hidden = noticeButtons().length < 2;
  };
  const updateAttention = (id, html) => {
    const items = [...attentionRows.querySelectorAll('tr.work')];
    const rowId = row => row.querySelector('.id').textContent;
    const previous = items.find(row => rowId(row) === id);
    const focused = previous?.contains(document.activeElement);
    previous?.remove();
    if (html) {
      const next = items.find(row => row !== previous && BigInt(rowId(row).slice(1)) < BigInt(id.slice(1)));
      if (next) next.insertAdjacentHTML('beforebegin', html);
      else attentionRows.insertAdjacentHTML('beforeend', html);
    }
    syncAttention();
    if (focused) {
      const replacement = [...attentionRows.querySelectorAll('tr.work')].find(row => rowId(row) === id);
      (replacement?.querySelector('.card-link') || attentionTab).focus();
    }
  };
  attentionPanel.addEventListener('click', async event => {
    const button = event.target.closest('button[data-read], button[data-read-all]');
    if (!button || button.disabled) return;
    const noticeStatus = button.nextElementSibling;
    const noticeAnnouncement = document.getElementById('notice-announcement');
    noticeStatus.textContent = '';
    noticeAnnouncement.textContent = '';
    const notices = noticeButtons();
    const selected = button === readAll ? notices : [button];
    const ids = selected.map(control => control.dataset.read);
    const controls = [...notices, readAll];
    const previousFocus = document.activeElement;
    controls.forEach(control => { control.disabled = true; });
    try {
      const response = await fetch('catch-ups/read', {method:'POST', redirect:'error',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({ids})});
      if (response.status === 409) {
        report(noticeStatus, attentionPanel, 'A notice may already be read. Reload the page when ready.', false);
        return;
      }
      if (!response.ok) throw new Error('refused');
      const {read} = await response.json();
      if (!Array.isArray(read) || read.length !== ids.length || !ids.every((id, index) => read[index] === id)) {
        throw new Error('Unconfirmed acknowledgment');
      }
      const focused = document.activeElement === document.body ? previousFocus : document.activeElement;
      const hadFocus = attentionPanel.contains(focused);
      selected.forEach(control => control.closest('tr').remove());
      syncAttention();
      report(noticeAnnouncement, attentionPanel, ids.length === 1 ? 'Notice marked read.' : 'Notices marked read.', false);
      if (hadFocus && !attentionPanel.hidden && (!focused.isConnected || (focused === readAll && readAll.hidden))) {
        attentionTab.focus();
      }
    } catch {
      report(noticeStatus, attentionPanel, 'The notice could not be marked read. Try again.', true);
    } finally {
      controls.forEach(control => { control.disabled = false; });
    }
  });
  const groups = {pinned: document.querySelector('#topics-pinned'), recent: document.querySelector('#topics-recent'),
    archived: document.querySelector('#topics-archived')};
  const none = document.getElementById('no-conversations');
  const noneArchived = document.getElementById('no-archived');
  const noneInScope = document.getElementById('no-scoped-conversations');
  const rowsOf = group => [...group.querySelectorAll('.topic')];
  const scope = document.getElementById('scope');
  const scopeProject = document.getElementById('scope-project');
  const scopeTopic = document.getElementById('scope-topic');
  const scopeTopicField = document.getElementById('scope-topic-field');
  const newProject = document.getElementById('new-project');
  const newTopic = document.getElementById('new-topic');
  const renameProject = document.getElementById('rename-project');
  const renameTopic = document.getElementById('rename-topic');
  const destination = document.querySelector('.talk-destination');
  const groupStatus = document.getElementById('group-status');
  let tree = JSON.parse(document.getElementById('groups-data').textContent);
  const projectOf = id => tree.find(project => project.id === id);
  const topicOf = (project, id) => project?.topics.find(topic => topic.id === id);
  const labelOf = (projectId, topicId) => {
    const project = projectOf(projectId);
    const topic = topicOf(project, topicId);
    return !project ? '' : topic ? project.name + ' / ' + topic.name : project.name;
  };
  const labelRow = row => {
    const label = labelOf(row.dataset.project, row.dataset.topic);
    const span = row.querySelector('.placement');
    span.textContent = label;
    span.hidden = !label;
  };
  const inScope = row => {
    const project = scopeProject.value, topic = scopeTopic.value;
    if (!project) return true;
    if (project === '-') return !row.dataset.project;
    return row.dataset.project === project && (!topic || row.dataset.topic === topic);
  };
  const scopeEmptyText = () => {
    if (scopeProject.value === '-') return 'Every conversation is in a project. A conversation you move out of a project shows here.';
    const project = projectOf(scopeProject.value);
    return 'No conversations in ' + (topicOf(project, scopeTopic.value) ?? project).name + ' yet. A conversation you start or move into it shows here.';
  };
  const syncGroups = () => {
    for (const group of Object.values(groups)) group.hidden = !rowsOf(group).some(row => !row.hidden);
    // Archived rows live in their own tab and never count toward the filtered Conversations list.
    const any = Boolean(document.querySelector('#topics-pinned .topic, #topics-recent .topic'));
    none.hidden = any;
    noneInScope.hidden = !any || Boolean(document.querySelector('#topics-pinned .topic:not([hidden]), #topics-recent .topic:not([hidden])'));
    if (!noneInScope.hidden) noneInScope.textContent = scopeEmptyText();
    noneArchived.hidden = !groups.archived.hidden;
  };
  const linkRow = row => {
    const link = row.querySelector('.topic-title');
    const archived = row.dataset.archived === 'true';
    const [address, hash] = link.getAttribute('href').split('#');
    const [path, query] = address.split('?');
    const params = new URLSearchParams(query);
    params.set('from', archived ? 'archived' : 'conversations');
    for (const [name, value] of [['project', scopeProject.value], ['topic', scopeTopic.value]]) {
      if (value && !archived) params.set(name, value); else params.delete(name);
    }
    link.setAttribute('href', path + '?' + params + (hash === undefined ? '' : '#' + hash));
  };
  const place = row => {
    const name = row.dataset.archived === 'true' ? 'archived' : row.dataset.pinned === 'true' ? 'pinned' : 'recent';
    linkRow(row);
    const list = groups[name].querySelector('.topic-list');
    // The filter applies only outside Archived, and before the row takes its place by pin and activity.
    row.hidden = name !== 'archived' && !inScope(row);
    const {activity, id} = row.dataset;
    const next = rowsOf(list.parentElement).find(other => other !== row && (
      other.dataset.activity < activity ||
      (other.dataset.activity === activity && BigInt(other.dataset.id.slice(1)) < BigInt(id.slice(1)))
    ));
    list.insertBefore(row, next || null);
    syncGroups();
  };
  const fillSelect = (select, first, entries, value) => {
    select.replaceChildren(...first.map(([id, name]) => new Option(name, id)), ...entries.map(entry => new Option(entry.name, entry.id)));
    select.value = [...select.options].some(option => option.value === value) ? value : '';
  };
  const SCOPE_KEY = 'quarterdeck-scope';
  const syncScope = (next = {project: scopeProject.value, topic: scopeTopic.value}) => {
    fillSelect(scopeProject, [['', 'All'], ['-', 'Ungrouped']], tree, next.project);
    const project = projectOf(scopeProject.value);
    fillSelect(scopeTopic, [['', 'All topics']], project ? project.topics : [], project ? next.topic : '');
    document.getElementById('scope-project-field').hidden = !tree.length;
    scope.dataset.projects = tree.length ? 'yes' : 'no';
    scopeTopicField.hidden = !project;
    newTopic.hidden = renameProject.hidden = !project;
    renameTopic.hidden = !scopeTopic.value;
    for (const row of conversationPanel.querySelectorAll('.topic')) {
      row.hidden = !inScope(row);
      labelRow(row);
      linkRow(row);
    }
    syncGroups();
    groupStatus.textContent = '';
    if (destination) {
      const label = project ? labelOf(project.id, scopeTopic.value) : '';
      destination.hidden = !tree.length;
      destination.textContent = label ? 'Starts in ' + label + '.' : 'Starts ungrouped.';
    }
    try { sessionStorage.setItem(SCOPE_KEY, JSON.stringify({project: scopeProject.value, topic: scopeTopic.value})); } catch {}
  };
  const startPlacement = () => {
    const project = projectOf(scopeProject.value);
    return !project ? {} : scopeTopic.value ? {project: project.id, topic: scopeTopic.value} : {project: project.id};
  };
  scopeProject.addEventListener('change', () => syncScope({project: scopeProject.value, topic: ''}));
  scopeTopic.addEventListener('change', () => syncScope());

  const groupForm = document.getElementById('group-form');
  const groupName = document.getElementById('group-name');
  const groupSave = groupForm.querySelector('button[type="submit"]');
  const moveForm = document.getElementById('move-form');
  const moveProject = document.getElementById('move-project');
  const moveTopic = document.getElementById('move-topic');
  let naming = null;
  let moving = null;
  const openGroup = (trigger, mode) => {
    closeMove(false);
    naming = {...mode, trigger};
    document.getElementById('group-label').textContent = (mode.kind === 'project' ? 'Project' : 'Topic') + ' name';
    groupName.value = mode.name || '';
    groupStatus.classList.remove('failed');
    groupStatus.textContent = '';
    groupForm.hidden = false;
    groupSave.disabled = !normalizeTopic(groupName.value);
    groupName.focus();
    groupName.select();
  };
  const closeGroup = refocus => {
    if (!naming) return;
    const {trigger} = naming;
    naming = null;
    groupForm.hidden = true;
    if (refocus) (trigger.hidden ? scopeProject : trigger).focus();
  };
  newProject.addEventListener('click', () => openGroup(newProject, {kind: 'project'}));
  newTopic.addEventListener('click', () => openGroup(newTopic, {kind: 'topic', parent: scopeProject.value}));
  renameProject.addEventListener('click', () => openGroup(renameProject, {kind: 'project', id: scopeProject.value, name: projectOf(scopeProject.value).name}));
  renameTopic.addEventListener('click', () => openGroup(renameTopic, {kind: 'topic', id: scopeTopic.value,
    name: topicOf(projectOf(scopeProject.value), scopeTopic.value).name}));
  groupName.addEventListener('input', () => { groupSave.disabled = !normalizeTopic(groupName.value); });
  document.getElementById('group-cancel').addEventListener('click', () => closeGroup(true));
  groupForm.addEventListener('keydown', event => { if (event.key === 'Escape') closeGroup(true); });
  groupForm.addEventListener('submit', async event => {
    event.preventDefault();
    const name = normalizeTopic(groupName.value);
    if (!name || !naming) return;
    const body = naming.id ? {id: naming.id, name} : naming.parent ? {project: naming.parent, name} : {name};
    groupSave.disabled = true;
    groupStatus.classList.remove('failed');
    groupStatus.textContent = 'Saving name';
    try {
      const outcome = await postJson('groups', body);
      if (outcome.ok) {
        tree = outcome.result.projects;
        const {kind, id} = naming;
        const next = {project: scopeProject.value, topic: scopeTopic.value};
        if (!id && kind === 'project') Object.assign(next, {project: outcome.result.id, topic: ''});
        if (!id && kind === 'topic') next.topic = outcome.result.id;
        closeGroup(false);
        syncScope(next);
        groupStatus.textContent = name + ' saved.';
        (kind === 'project' ? scopeProject : scopeTopic).focus();
      } else {
        groupStatus.classList.add('failed');
        groupStatus.textContent = outcome.message;
        groupSave.disabled = false;
      }
    } catch {
      groupStatus.classList.add('failed');
      groupStatus.textContent = 'Quarterdeck could not save the name. Try again.';
      groupSave.disabled = false;
    }
  });

  const syncMoveTopics = value => {
    const project = projectOf(moveProject.value);
    fillSelect(moveTopic, [['', 'No topic']], project ? project.topics : [], value);
    document.getElementById('move-topic-field').hidden = !project;
  };
  const closeMove = refocus => {
    if (!moving) return;
    const row = moving;
    moving = null;
    moveForm.hidden = true;
    scope.after(moveForm);
    if (refocus) row.querySelector('button.move').focus();
  };
  const note = (row, text, failed) => report(row.hidden ? startStatus : row.querySelector('.desk-feedback'), conversationPanel, text, failed);
  moveProject.addEventListener('change', () => syncMoveTopics(''));
  document.getElementById('move-cancel').addEventListener('click', () => closeMove(true));
  moveForm.addEventListener('keydown', event => { if (event.key === 'Escape') closeMove(true); });
  moveForm.addEventListener('submit', async event => {
    event.preventDefault();
    const row = moving;
    const title = row.querySelector('.pin').dataset.title;
    const project = moveProject.value || null;
    const topic = project && moveTopic.value || null;
    const controls = moveForm.querySelectorAll('select,button');
    controls.forEach(control => { control.disabled = true; });
    let saved = false;
    try {
      saved = (await postJson('moves', {part: row.dataset.id, project, topic})).ok;
    } catch {
      saved = false;
    }
    controls.forEach(control => { control.disabled = false; });
    if (!saved) {
      note(row, 'Quarterdeck could not move ' + title + '. Try again.', true);
      return;
    }
    for (const [name, value] of [['project', project], ['topic', topic]]) {
      if (value) row.dataset[name] = value; else delete row.dataset[name];
    }
    closeMove(false);
    labelRow(row);
    row.hidden = !inScope(row);
    syncGroups();
    note(row, title + ' moved to ' + (labelOf(project, topic) || 'Ungrouped') + '.', false);
    if (!row.hidden) row.querySelector('button.move').focus();
    else (document.querySelector('.topic:not([hidden]) .topic-title') || scopeProject).focus();
  });
  conversationPanel.addEventListener('click', event => {
    const button = event.target.closest('button.move');
    if (!button || button.disabled) return;
    const row = button.closest('.topic');
    if (moving === row) { closeMove(true); return; }
    closeMove(false);
    closeGroup(false);
    moving = row;
    fillSelect(moveProject, [['', 'Ungrouped']], tree, row.dataset.project || '');
    syncMoveTopics(row.dataset.topic || '');
    row.append(moveForm);
    moveForm.hidden = false;
    moveProject.focus();
  });
  try {
    const saved = arrival.get('tab') === 'conversations'
      ? {project: arrival.get('project'), topic: arrival.get('topic')} : JSON.parse(sessionStorage.getItem(SCOPE_KEY)) || {};
    syncScope({project: saved.project || '', topic: saved.topic || ''});
  } catch {
    syncScope({project: '', topic: ''});
  }

  const flags = {
    pin: {path:'pins', key:'pinned', labels:['Pin', 'Unpin'], done:['is unpinned.', 'is pinned.'], failed:'the pin for '},
    resolve: {path:'resolutions', key:'resolved', labels:['Resolve', 'Reopen'], done:['is reopened.', 'is resolved.'], failed:'the resolution for '},
    archive: {path:'archives', key:'archived', labels:['Archive', 'Unarchive'], done:['is unarchived.', 'is archived.'], failed:'the archive for '}
  };
  const holdList = document.getElementById('panel-conversations');
  const onRowClick = async event => {
    const button = event.target.closest('button.pin, button.resolve, button.archive');
    if (!button || button.disabled) return;
    const flag = flags[button.className];
    const row = button.closest('.topic');
    const panel = row.closest('.desk-panel');
    const rowStatus = row.querySelector('.desk-feedback');
    const value = row.dataset[flag.key] !== 'true';
    const hadFocus = document.activeElement === button;
    button.disabled = true;
    let outcome = null;
    try {
      outcome = await postJson(flag.path, {part: row.dataset.id, [flag.key]: value});
    } catch {
      outcome = null;
    }
    const focused = document.activeElement;
    button.disabled = false;
    if (outcome?.ok) {
      const title = button.dataset.title;
      row.dataset[flag.key] = String(value);
      button.textContent = flag.labels[value ? 1 : 0];
      button.setAttribute('aria-label', flag.labels[value ? 1 : 0] + ' ' + title);
      row.querySelector('.resolved-mark').hidden = row.dataset.resolved !== 'true';
      row.querySelector('button.pin').hidden = row.dataset.archived === 'true';
      row.querySelector('button.move').hidden = row.dataset.archived === 'true';
      if (flag.key === 'archived' && row.contains(moveForm)) closeMove(false);
      const neighbour = row.nextElementSibling || row.previousElementSibling
        || [...panel.querySelectorAll('.topic')].find(other => other !== row && !other.closest('[hidden]'));
      place(row);
      if (flag.key === 'archived') {
        // The row left this tab, so the shared status line carries the news.
        startStatus.classList.remove('failed');
        startStatus.textContent = title + ' ' + flag.done[value ? 1 : 0];
        if (hadFocus) (neighbour?.querySelector('.topic-title') || document.getElementById(panel.getAttribute('aria-labelledby'))).focus();
      } else {
        report(rowStatus, panel, title + ' ' + flag.done[value ? 1 : 0], false);
        if (!panel.hidden && (focused === button || (hadFocus && focused === document.body))) button.focus();
      }
      if (flag.key !== 'pinned' && holdList && outcome.result.hold !== undefined) {
        holdList.dispatchEvent(new CustomEvent('conversation-saved', {detail: {id: row.dataset.id, html: outcome.result.hold}}));
      }
    } else {
      report(rowStatus, panel, 'Quarterdeck could not save ' + flag.failed + button.dataset.title + '. Try again.', true);
      if (!panel.hidden && (focused === button || (hadFocus && focused === document.body))) button.focus();
    }
  };
  conversationPanel.addEventListener('click', onRowClick);
  document.getElementById('desk-panel-archived').addEventListener('click', onRowClick);

  const arrivingDeskTab = tabs.find(tab => tab.id === 'desk-tab-' + arrival.get('tab'));
  if (arrivingDeskTab) {
    select(arrivingDeskTab, false);
    const wanted = arrival.get('row') ?? '';
    const rows = [...panelOf(arrivingDeskTab).querySelectorAll('tr.work, tr.notice, li.topic')];
    // A question answered meanwhile has left Needs you; its place is taken by the next one down.
    const below = row => row.classList.contains('work') && BigInt(row.dataset.id.slice(1)) < BigInt(wanted.slice(1) || '0');
    const row = arrivalRow(rows, wanted) ?? (/^Q[1-9][0-9]*$/.test(wanted) ? rows.find(below) : undefined);
    showArrivalRow(row, row?.querySelector('.card-link, .topic-title, a'));
  }

  const form = document.querySelector('#talk-form');
  if (!form) return;
  const topic = form.querySelector('#talk-topic');
  const message = form.querySelector('#talk-message');
  const button = form.querySelector('button[type="submit"]');
  const opener = document.querySelector('#talk-open');
  const drafts = new Map();
  const hasDraft = () => Boolean(topic.value.trim() || message.value.trim());
  const setOpen = open => {
    form.hidden = !open;
    opener.setAttribute('aria-expanded', String(open));
  };
  const refresh = () => {
    button.disabled = !(normalizeTopic(topic.value) && trimmed(message.value));
    if (hasDraft()) setOpen(true);
  };
  opener.addEventListener('click', () => {
    const open = form.hidden;
    setOpen(open);
    if (open) topic.focus();
  });
  const show = html => {
    const holder = document.createElement('template');
    holder.innerHTML = html;
    const row = holder.content.firstElementChild;
    const replaced = document.querySelector('.topic[data-id="' + row.dataset.id + '"]');
    if (replaced?.contains(moveForm)) closeMove(false);
    replaced?.remove();
    localizeDays(row);
    localizeStamps(row);
    labelRow(row);
    row.hidden = !inScope(row);
    place(row);
    return row;
  };
  form.addEventListener('input', refresh);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const title = normalizeTopic(topic.value), text = trimmed(message.value);
    if (!title || !text) return;
    const key = JSON.stringify([title, text]);
    const id = drafts.get(key) ?? messageId();
    drafts.set(key, id);
    const controls = form.querySelectorAll('button,input,textarea');
    controls.forEach(c => c.disabled = true);
    startStatus.classList.remove('failed');
    startStatus.textContent = 'Starting conversation';
    let started = null;
    try {
      const outcome = await postJson('conversations', {id, title, text, home:button.dataset.home, ...startPlacement()});
      if (outcome.ok || outcome.saved === false) drafts.delete(key);
      if (outcome.ok) {
        startStatus.textContent = outcome.result.message;
        started = show(outcome.result.row);
        updateAttention(started.dataset.id, outcome.result.attention);
        document.getElementById('panel-conversations').dispatchEvent(new CustomEvent('conversation-saved', {
          detail: {id: started.dataset.id, html: outcome.result.hold}
        }));
        topic.value = '';
        message.value = '';
        setOpen(false);
      } else {
        startStatus.classList.add('failed');
        startStatus.textContent = outcome.message;
      }
    } catch {
      startStatus.classList.add('failed');
      startStatus.textContent = SENDING_FAILED();
    }
    controls.forEach(c => c.disabled = false);
    refresh();
    if (!conversationPanel.hidden) started?.querySelector('.topic-title').focus();
  });
  refresh();
})();
""")


WATCH_SCRIPT = r"""
(() => {
  const status = document.getElementById('watch-work-status');
  const list = document.getElementById('watch-work-list');
  if (!status || !list) return;
  const failed = () => {
    status.className = 'meta failed';
    status.textContent = 'Current work could not be loaded. Open Workers to check it.';
  };
  fetch('workers-saved.json', {cache: 'no-store', headers: {Accept: 'application/json'}}).then(response => {
    if (!response.ok) throw new Error('worker read failed');
    return response.json();
  }).then(({snapshot}) => {
    if (!snapshot) {
      status.textContent = 'Workers have not been checked yet. Open Workers to check them.';
      return;
    }
    const rows = [...snapshot.workers, ...snapshot.second_mates];
    for (const worker of rows) list.append(workerSummary(worker));
    list.hidden = !rows.length;
    status.textContent = rows.length ? 'Worker reading taken ' : 'No workers are running. Reading taken ';
    const time = make('time', '', localTime(snapshot.observed_at));
    time.dateTime = snapshot.observed_at;
    status.append(time);
  }).catch(failed);
})();
"""


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
  const exclusions = new Map();
  let messageQueued = false;
  const queueList = document.querySelector('#queue-list');
  const queueField = document.querySelector('.queue-form textarea');
  const queueAdd = document.querySelector('#queue-add');
  const sendQueue = document.querySelector('#send-queue');
  const freshId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  const draftOrigins = new Map(fields.filter(f => f.closest('section').querySelector('.ask-button'))
    .map(f => [f, 'filled']));
  let history = [];
  const newDraft = f => answerText(f.value) && answerText(f.value) !== answerText(f.defaultValue);
  const rejectedAsk = origin => origin?.rejected && !origin.edited;
  const handEdited = origin => origin === 'manual' || origin?.rejected || origin?.edited;
  const askDraft = f => handEdited(draftOrigins.get(f)) && newDraft(f);
  const requestRejected = status => [400, 403, 405, 409, 415].includes(status);
  const optionRevision = section => section.querySelector('.clarified').dataset.optionRevision;
  const isExcluded = field => exclusions.get(field)?.active;
  const changed = f => {
    return !rejectedAsk(draftOrigins.get(f)) && !rejections.has(f) && !isExcluded(f)
      && !needsReview(f.closest('section')) && answerDraft(f);
  };
  const suggestionFor = f => {
    const section = f.closest('section');
    if (!isExcluded(f) && !rejections.has(f) && section.dataset.state === 'open' && !f.defaultValue && !f.value) {
      return section.querySelector('[data-suggested]');
    }
  };
  function renderQueue() {
    if (!queueList) return;
    const answers = fields.filter(changed);
    const count = answers.length + Number(messageQueued);
    const item = (key, title, text, remove) => {
      const li = document.createElement('li');
      li.dataset.queueKey = key;
      const head = document.createElement('p');
      head.append(Object.assign(document.createElement('strong'), {textContent: title}));
      li.append(head);
      li.append(Object.assign(document.createElement('p'), {className: 'record', textContent: text}));
      if (remove) {
        const button = Object.assign(document.createElement('button'), {type: 'button', textContent: 'Remove from send'});
        button.addEventListener('click', remove);
        li.append(button);
      }
      return li;
    };
    const rows = [
      ...answers.map(f => {
        const section = f.closest('section');
        return item(section.id, 'Answer \u00b7 ' + section.id, answerText(f.value),
          () => {
            const exclusion = {text: answerText(f.value), active: true, edited: false};
            exclusions.set(f, exclusion);
            for (const step of history) {
              const draft = step.get(f);
              if (draft) draft.exclusion = exclusion;
            }
            refresh();
          });
      }),
      ...(messageQueued ? [item(whole.id, 'Set message', answerText(queueField.value))] : []),
    ];
    const previous = [...queueList.children];
    const focused = document.activeElement.closest('[data-queue-key]');
    for (const [index, row] of rows.entries()) {
      const existing = previous.find(li => li.dataset.queueKey === row.dataset.queueKey);
      if (existing?.isEqualNode(row)) rows[index] = existing;
    }
    previous.filter(row => !rows.includes(row)).forEach(row => row.remove());
    rows.forEach((row, index) => {
      if (queueList.children[index] !== row) queueList.insertBefore(row, queueList.children[index] ?? null);
    });
    if (previous.includes(focused) && !focused.contains(document.activeElement)) {
      const replacement = rows.find(row => row.dataset.queueKey === focused.dataset.queueKey)
        || rows[Math.min(previous.indexOf(focused), answers.length - 1)];
      (replacement?.querySelector('button') || queueField).focus();
    }
    document.querySelector('#queue-count').textContent = count;
    document.querySelector('#queue-empty').hidden = count > 0;
    sendQueue.disabled = !count;
    sendQueue.textContent = count ? 'Send queued items (' + count + ')' : 'Send queued items';
    queueAdd.disabled = messageQueued || !answerText(queueField.value);
  }
  function reviewed(section) {
    section.removeAttribute('data-review');
  }
  function forget(field) {
    for (const step of history) step.delete(field);
    history = history.filter(step => step.size);
  }
  function refresh() {
    if (sending) return;
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
    renderQueue();
    if (undo) undo.hidden = !history.length;
    for (const f of editors) {
      const section = f.closest('section');
      const ask = section.querySelector('.ask-button');
      if (ask) ask.disabled = !askDraft(f);
      for (const retry of section.querySelectorAll('.pending-asks button')) {
        retry.disabled = Boolean(newDraft(f));
      }
      const review = section.querySelector('.review');
      if (review) review.hidden = !needsReview(section);
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
    if (field === queueField) {
      if (!answerText(field.value)) messageQueued = false;
      refresh();
      return;
    }
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
    const exclusion = exclusions.get(field);
    const text = answerText(field.value);
    if (exclusion && !restoring && text && text !== exclusion.text) {
      exclusions.set(field, {...exclusion, active: false, edited: true});
    } else if (exclusion && restoring) {
      exclusions.set(field, exclusion.edited ? {...exclusion, active: !text || text === exclusion.text} : {...exclusion, text});
    }
    forget(field);
    refresh();
  });
  queueAdd?.addEventListener('click', () => {
    if (!answerText(queueField.value)) return;
    messageQueued = true;
    refresh();
    queueField.focus();
  });
  sendQueue?.addEventListener('click', async () => {
    const answers = fields.filter(changed);
    const text = messageQueued ? answerText(queueField.value) : '';
    const home = document.querySelector('#item-ask-editor').dataset.home;
    const totals = answers.length ? await submitAnswers(answers) : {sent: 0, pending: 0, failed: 0, unconfirmed: 0, skipped: 0};
    const details = totals.failed || totals.unconfirmed ? [totals.message] : [];
    if (text) {
      messageQueued = false;
      queueField.value = '';
      rejections.delete(queueField);
      const body = {part: whole.id, id: freshId(), text, home};
      const result = await sendAsk(whole, body);
      totals[result.delivery] += 1;
      if (result.delivery !== 'sent') details.push(result.message);
    }
    if (!totals.pending && !totals.failed && !totals.unconfirmed && !totals.skipped) {
      output.textContent = 'Queued items sent.';
    } else {
      output.textContent = [
        totals.sent + ' sent.',
        totals.pending + ' saved for retry.',
        totals.failed + ' failed.',
        totals.unconfirmed ? totals.unconfirmed + ' unconfirmed.' : '',
        totals.skipped ? totals.skipped + ' skipped.' : '',
        ...details,
      ].filter(Boolean).join(' ');
    }
  });
  document.addEventListener('click', e => {
    if (e.target.closest('.keep-draft')) {
      reviewed(e.target.closest('section'));
      refresh();
    }
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
        if (option && (option.dataset.choice !== f.value || handEdited(origin) || (choice && isExcluded(f)))) {
          const askError = rejectedAsk(origin) ? rejections.get(f) : undefined;
          step.set(f, {value: f.value, origin, askError, exclusion: exclusions.get(f)});
          if (askError) rejections.delete(f);
          f.value = option.dataset.choice;
          if (choice) exclusions.delete(f);
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
        const draft = field.value;
        const body = {part: section.id, id: freshId(), text: answerText(draft), home: askButton.dataset.home};
        field.value = field.defaultValue;
        if (rejectedAsk(draftOrigins.get(field))) rejections.delete(field);
        if (draftOrigins.has(field)) draftOrigins.set(field, 'filled');
        forget(field);
        sendAsk(section, body, draft);
      }
    }
    const threadButton = e.target.closest('[data-retry-ask]');
    if (threadButton) {
      const section = threadButton.closest('section');
      askRequest(section, {part: section.id, id: threadButton.dataset.retryAsk, action: 'retry'});
    }
    if (e.target === undo && history.length) {
      const step = history.pop();
      for (const [f, {value, origin, askError, exclusion}] of step) {
        f.value = value;
        if (origin) draftOrigins.set(f, origin);
        if (askError) rejections.set(f, askError);
        if (exclusion) {
          exclusions.set(f, exclusion.active ? {text: answerText(value), active: true, edited: false} : exclusion);
        } else {
          exclusions.delete(f);
        }
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
    if (record.state !== 'answered' || record.delivery !== 'sent' || section.querySelector('.badge').textContent === 'Unread reply') {
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
  function sendAsk(section, body, displayText = body.text) {
    const pending = document.createElement('ol');
    pending.className = 'exchange';
    pending.dataset.localAsk = body.id;
    pending.innerHTML = '<li class="msg you"><p class="who"><strong>You</strong> <span class="delivery">Pending</span></p>'
      + '<p class="record"></p><div class="actions"><button type="button">Send ask again</button></div></li>';
    pending.querySelector('.record').textContent = displayText;
    pending.querySelector('button').addEventListener('click', () => askRequest(section, {...body, action: 'retry'}, pending));
    const thread = section.querySelector('.thread');
    thread.querySelector('.pending-asks').append(pending);
    thread.hidden = false;
    thread.open = true;
    return askRequest(section, body, pending);
  }
  async function askRequest(section, body, pending) {
    let failedDraft;
    let delivery = 'failed';
    discussionRequest?.abort();
    sending = true;
    if (undo) undo.hidden = true;
    const controls = document.querySelectorAll('button,input,textarea');
    controls.forEach(c => c.disabled = true);
    output.textContent = 'Sending ask';
    try {
      const response = await fetch('asks', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body)});
      if (requestRejected(response.status)) {
        const {message, saved} = response.status === 409 ? await response.json() :
          {message: await response.text(), saved: false};
        output.textContent = message;
        if (saved === true) delivery = 'pending';
        const blockedRetry = [403, 405, 415].includes(response.status) && body.action === 'retry';
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
        updateThread(section, result.thread);
        if (result.clarifications) updateClarifications(result.clarifications);
        output.textContent = result.message;
        delivery = result.delivery;
      }
    } catch (err) {
      delivery = 'unconfirmed';
      output.textContent = navigator.onLine ? 'Send interrupted. ' + err.message : 'Offline. Reconnect and send again.';
    }
    controls.forEach(c => c.disabled = false);
    sending = false;
    refresh();
    if (failedDraft) failedDraft.focus();
    return {delivery, message: output.textContent};
  }
  function showAnswer(section, field, record, reviewedRevision) {
    section.dataset.state = record.state;
    section.dataset.revision = record.revision;
    section.dataset.pending = record.delivery === 'pending' ? '1' : '0';
    field.defaultValue = field.value = record.answer;
    exclusions.delete(field);
    if (reviewedRevision === optionRevision(section)) reviewed(section);
    if (draftOrigins.has(field)) draftOrigins.set(field, 'filled');
    forget(field);

    const saved = section.querySelector('.saved-answer');
    saved.querySelector('.record').textContent = record.answer;
    saved.querySelector('.delivery').textContent = {sent: 'Sent', pending: 'Not delivered', cancelled: 'Sending stopped'}[record.delivery];
    const time = saved.querySelector('time');
    time.dateTime = record.answered_at;
    time.textContent = localTime(record.answered_at);
    saved.hidden = false;

    if (record.earlier_answers_html !== undefined) {
      const history = section.querySelector('.answer-history');
      history.innerHTML = record.earlier_answers_html;
      localizeTimes(history);
    }

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
  async function submitAnswers(pending) {
    const revisions = pending.map(f => optionRevision(f.closest('section')));
    sending = true;
    undo.hidden = true;
    const controls = document.querySelectorAll('button,input,textarea');
    controls.forEach(c => c.disabled = true);
    let interrupted = '';
    let requestFailure = '';
    let rejected = 0;
    const totals = {sent: 0, pending: 0, failed: 0, unconfirmed: 0, skipped: 0};
    try {
      for (const [i,f] of pending.entries()) {
        output.textContent = 'Sending ' + (i+1) + ' of ' + pending.length;
        const section = f.closest('section');
        const answer = answerText(f.value);
        const option_revision = revisions[i];
        const response = await fetch('answers', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({part:section.id, revision:section.dataset.revision, answer,
            ...(option_revision ? {option_revision} : {})})});
        if (requestRejected(response.status)) {
          if (response.status === 409 && response.headers?.get('Content-Type') === 'application/json') {
            const {clarification} = await response.json();
            updateClarification(section, clarification);
          } else if (response.status === 400 || response.status === 409) {
            rejections.set(f, {message: await response.text(), draft: response.status === 400 ? answer : undefined});
          } else {
            requestFailure ||= await response.text();
          }
          const box = f.closest('details.done');
          if (box) box.open = true;
          rejected += 1;
          continue;
        }
        if (!response.ok) throw new Error(await response.text());
        const record = await response.json();
        showAnswer(section, f, record, option_revision);
        if (record.delivery === 'sent' || record.delivery === 'pending') totals[record.delivery] += 1;
        else rejected += 1;
      }
    } catch (err) {
      interrupted = navigator.onLine ? 'Send interrupted. ' + err.message : 'Offline. Reconnect and send again.';
    }
    const summary = rejected ? rejected + (rejected === 1 ? ' answer rejected.' : ' answers rejected.') : '';
    output.textContent = [summary, requestFailure, interrupted].filter(Boolean).join(' ') || 'Answers saved.';
    totals.failed = rejected;
    totals.unconfirmed = Number(Boolean(interrupted));
    totals.skipped = pending.length - totals.failed - totals.unconfirmed - totals.sent - totals.pending;
    controls.forEach(c => c.disabled = false);
    sending = false;
    refresh();
    return {...totals, message: output.textContent};
  }
  send?.addEventListener('click', () => submitAnswers(fields.filter(changed)));
  refreshAnswerControls = refresh;
  refresh();
}
"""


def activity_time(item) -> str:
    """The moment the Desk sorts a conversation by. The browser shows its local date and time."""
    moment = conversation_activity(item).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'<time class="stamp" datetime="{moment}">{moment[:10]}</time>'


def day_time(moment: object) -> str:
    return f'<time class="day" datetime="{esc(moment)}">{esc(str(moment)[:10])}</time>'


def reference_row(item, *, served):
    """A bookmark: the title links straight to the resource, extra links follow inline, details stay on the item's page."""
    item_id, kind = item["id"], item["kind"]
    page = esc(item_link(item_id, "references" if served else None))
    detail = " data-detail" if served else ""
    if kind == "resource":
        first, *more = resource_links(item["body"])
        title = (f'<a class="ref-title" href="{esc(first["url"])}" rel="noopener noreferrer">{esc(item["title"])}</a>')
        extra = "".join(f'<a class="ref-extra" href="{esc(link["url"])}" rel="noopener noreferrer">{esc(link["text"])}</a>'
                        for link in more)
    else:
        title, extra = f'<a class="ref-title" href="{page}"{detail}>{esc(item["title"])}</a>', ""
    project = f'<span>{esc(item["project"])}</span>' if item.get("project") else ""
    attributes = filter_data(item)
    if served:
        attributes += f' data-id="{item_id}" data-search="{esc(library_search_text(item))}"'
    item_ref = (f'<a class="lib-title ref-id" href="{page}"{detail}>{item_id}</a>' if kind == "resource"
                else f'<span class="ref-id">{item_id}</span>')
    small = project + day_time(item["saved_at"]) + item_ref
    return (f'<li class="lib-item ref-row"{attributes}>'
            f'{title}{f"<span class=ref-more>{extra}</span>" if extra else ""}<span class="ref-meta">{small}</span></li>')


def library_row(item, *, served):
    item_id, kind = item["id"], item["kind"]
    target = unread_target(item) if served and kind in ("questions", "conversation") else None
    context = ("hold-conversations" if kind == "conversation" else "answered") if served else None
    href = esc(item_link(item_id, context, target))
    title = f'<a class="lib-title" href="{href}"{" data-detail" if served else ""}>{esc(item["title"])}</a>'
    project = f'<span class="project-name">{esc(item["project"])}</span>' if item.get("project") else ""
    tags = tag_labels(all_tags(item))
    attributes = filter_data(item)
    if kind == "questions" and not served:
        return f'<li class="lib-item"{attributes}>{title}{f" {project}" if project else ""}{tags}</li>'
    unread = served and thread_unread(item, None)

    if served:
        if kind == "questions":
            attributes += f' data-questions="{len(question_parts(item))}"'
        attributes += f' data-id="{item_id}" data-search="{esc(library_search_text(item))}"'
    if kind == "questions":
        state, _, _ = question_summary(item, replies=served)
        label = f'<span class="badge">{UNREAD_REPLY}</span>' if state == "Replied" else state
        detail = f'<p class="meta">{label}</p>'
    else:
        replied = f'<span class="badge">{UNREAD_REPLY}</span>' if unread else ""
        marked = resolved_mark(item) if kind == "conversation" else ""
        detail = f'<p class="meta"><span>{KIND_LABELS[kind]}</span>{marked}{replied}{day_time(item["saved_at"])}</p>'
    return (f'<li class="lib-item"{attributes}>'
            f'<div class="lib-head"><span class="id">{item_id}</span>'
            f'{title}{project}</div>{detail}{tags}</li>')


def library_panel(name: str, items: list[dict[str, object]], empty: str, *, served: bool) -> str:
    list_id = f"{name}-list"
    filters = filter_bar(list_id, items, restorable=served)
    row = reference_row if name == "references" else library_row
    rows = "".join(row(item, served=served) for item in items)
    listing = f'<ul class="lib-list" id="{list_id}">{rows}</ul>' if rows or (served and name == "conversations") else ""
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
    items = [item for item in newest_first(data["items"])  # type: ignore[arg-type]
             if item["kind"] == "conversation" and not is_archived(item)]
    conversations = library_panel("conversations", items,
                                  "No conversations yet. Start one from the Desk.", served=True)
    names = (("references", "References"), ("answered", "Answered"), ("conversations", "Conversations"))
    tabs = "".join(f'<button type="button" role="tab" id="tab-{name}" aria-controls="panel-{name}" '
                   f'aria-selected="{"true" if number == 0 else "false"}" tabindex="{0 if number == 0 else -1}">{label}</button>'
                   for number, (name, label) in enumerate(names))
    panels = "".join(f'<section class="lib-panel" role="tabpanel" id="panel-{name}" aria-labelledby="tab-{name}"'
                     f'{"" if number == 0 else " hidden"}>{content}</section>'
                     for number, (name, content) in enumerate(zip((n for n, _ in names), (references, answered, conversations))))
    return (f'<dialog id="library" class="library" aria-label="Hold"><div class="lib-top">'
            f'<div role="tablist" aria-label="Hold">{tabs}</div><button type="button" data-close>Close Hold</button></div>'
            f'{panels}</dialog>\n')


def desk_item(item: dict[str, object]) -> bool:
    """A question item stays on the Desk while a question is open or a non-closed question has pending delivery."""
    _, open_parts, pending = question_summary(item, replies=False)
    return bool(open_parts or pending)


def workers_section(ready: bool) -> str:
    if not ready:
        return ('<section id="workers"><h1>Workers</h1><p class="empty">Workers show here once firstmate_home '
                'names a Firstmate home.</p></section>')
    return ('<section id="workers"><div class="section-head"><h1>Workers</h1>'
            '<button type="button" id="refresh-workers">Check workers</button></div>'
            '<p class="meta" id="workers-status" role="status">Loading workers</p><div id="workers-list"></div></section>')


def workers_page(site_title: str, *, ready: bool) -> str:
    """The Workers page: it reads the saved reading and the home only from its own script."""
    return page_shell(f"Workers · {site_title}", site_title,
                      workers_section(ready),
                      served=True, saved_name="index.html", workers=ready, current="workers")


def talk_form(binding):
    return ('<button type="button" id="talk-open" class="talk-open" aria-expanded="false" aria-controls="talk-form">'
            'New conversation</button>'
            '<form id="talk-form" class="talk" novalidate aria-label="New conversation" hidden>'
            '<div class="talk-field"><label for="talk-topic">Conversation title</label>'
            f'<input id="talk-topic" maxlength="{TITLE_LIMIT}" autocomplete="off"></div>'
            '<div class="talk-field"><label for="talk-message">Message</label>'
            f'<textarea id="talk-message" maxlength="{TEXT_LIMIT}" rows="2"></textarea></div>'
            '<div class="actions"><button type="submit" class="primary" disabled '
            f'data-home="{esc(binding)}">Start conversation</button></div>'
            '<p class="meta talk-destination" hidden></p></form>')


PREVIEW_LIMIT = 200


CHECK_ICON = ('<svg class="check" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">'
              '<path d="M3 8.5l3.2 3.2L13 4.8" fill="none" stroke="currentColor" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round"/></svg>')


def resolved_mark(item):
    """The small labelled check on a resolved conversation. It is always present so a script can show or hide it."""
    return f'<span class="resolved-mark"{"" if is_resolved(item) else " hidden"}>{CHECK_ICON}Resolved</span>'


def flag_button(name, on, off, active, title):
    verb = on if active else off
    return f'<button type="button" class="{name}" data-title="{title}" aria-label="{verb} {title}">{verb}</button>'


def conversation_row(item, projects=()):
    latest = part_thread(item, None)[-1]
    target = unread_target(item)
    href = esc(item_link(item["id"], "archived" if is_archived(item) else "conversations", target))
    status = f'<span class="badge">{UNREAD_REPLY}</span>' if target else ""
    if has_pending_ask(item):
        status += '<span>Not delivered</span>'
    if not status:
        status = '<span>Messaged</span>' if latest["author"] == "you" else "<span>Replied</span>"
    activity = conversation_activity(item).isoformat(timespec="microseconds")
    project = f'<span class="project-name">{esc(item["project"])}</span>' if item.get("project") else ""
    preview = " ".join(str(latest["text"]).split())[:PREVIEW_LIMIT]
    pinned, resolved, archived = is_pinned(item), is_resolved(item), is_archived(item)
    title = esc(item["title"])
    pin = flag_button("pin", "Unpin", "Pin", pinned, title)
    move = f'<button type="button" class="move" aria-label="Move {title}">Move</button>'
    if archived:
        pin = pin.replace('<button ', '<button hidden ', 1)
        move = move.replace('<button ', '<button hidden ', 1)
    flags = " ".join(f'data-{name}="{"true" if on else "false"}"' for name, on in
                     (("pinned", pinned), ("resolved", resolved), ("archived", archived)))
    placement = item.get("placement", {})
    where = "".join(f' data-{name}="{esc(placement[name])}"' for name in ("project", "topic") if name in placement)
    label = placement_label(item, projects)  # type: ignore[arg-type]
    return (f'<li class="topic" data-id="{item["id"]}" data-activity="{activity}" {flags}{where}>'
            f'<div class="topic-main"><a class="topic-title" href="{href}">{title}</a>'
            f'<p class="topic-latest">{esc(preview)}</p>'
            f'<p class="meta topic-meta"><span class="placement"{"" if label else " hidden"}>{esc(label)}</span>'
            f'{project}{resolved_mark(item)}{status}{activity_time(item)}</p></div>'
            f'<div class="topic-actions">{pin}{move}{flag_button("resolve", "Reopen", "Resolve", resolved, title)}'
            f'{flag_button("archive", "Unarchive", "Archive", archived, title)}</div>'
            '<p class="meta desk-feedback" role="status"></p></li>')


def conversation_groups(items, projects=()):
    """The Pinned and Recent groups. Each is a list kept in newest-activity order; the script moves rows between them."""
    ordered = by_activity(item for item in items if not is_archived(item))
    groups = []
    for name, heading, pinned in (("pinned", "Pinned", True), ("recent", "Recent conversations", False)):
        rows = "".join(conversation_row(item, projects) for item in ordered if is_pinned(item) == pinned)
        groups.append(f'<section class="topics" id="topics-{name}"{"" if rows else " hidden"}><h2>{heading}</h2>'
                      f'<ul class="topic-list">{rows}</ul></section>')
    return "".join(groups)


def scope_bar(tree):
    """The project filter above the list, the forms that create and rename projects and topics, and the one Move form."""
    projects = "".join(f'<option value="{esc(project["id"])}">{esc(project["name"])}</option>' for project in tree)
    data = json.dumps(tree, ensure_ascii=False).replace("<", "\\u003c")
    shown = "" if tree else " hidden"
    return (f'<div class="scope" id="scope" data-projects="{"yes" if tree else "no"}">'
            f'<script type="application/json" id="groups-data">{data}</script>'
            f'<div class="scope-field" id="scope-project-field"{shown}><label for="scope-project">Project</label>'
            f'<select id="scope-project"><option value="">All</option><option value="-">Ungrouped</option>{projects}</select></div>'
            '<div class="scope-field" id="scope-topic-field" hidden><label for="scope-topic">Topic</label>'
            '<select id="scope-topic"><option value="">All topics</option></select></div>'
            '<div class="scope-actions"><button type="button" id="new-project">New project</button>'
            '<button type="button" id="new-topic" hidden>New topic</button>'
            '<button type="button" id="rename-project" hidden>Rename project</button>'
            '<button type="button" id="rename-topic" hidden>Rename topic</button></div>'
            '<form id="group-form" class="group-form" hidden novalidate>'
            '<div class="scope-field"><label for="group-name" id="group-label">Project name</label>'
            f'<input id="group-name" maxlength="{GROUP_NAME_LIMIT}" autocomplete="off"></div>'
            '<div class="actions"><button type="submit" disabled>Save name</button>'
            '<button type="button" id="group-cancel">Discard name</button></div></form>'
            '<p class="meta desk-feedback" id="group-status" role="status"></p></div>'
            '<form id="move-form" class="move-form" hidden novalidate aria-label="Move conversation">'
            '<div class="scope-field"><label for="move-project">Project</label><select id="move-project"></select></div>'
            '<div class="scope-field" id="move-topic-field"><label for="move-topic">Topic</label><select id="move-topic"></select></div>'
            '<div class="actions"><button type="submit">Save move</button>'
            '<button type="button" id="move-cancel">Cancel move</button></div></form>')


def conversations_panel(items, ask_binding, projects=()):
    form = talk_form(ask_binding) if ask_binding else ""
    live = [item for item in items if not is_archived(item)]
    empty = (f'<p class="empty" id="no-conversations"{" hidden" if live else ""}>'
             'No conversations yet. A conversation you start with Firstmate shows here.</p>')
    scoped = '<p class="empty" id="no-scoped-conversations" hidden></p>'
    tree = project_tree({"projects": projects})
    return f'{form}{scope_bar(tree)}{empty}{scoped}{conversation_groups(items, projects)}'


def archived_panel(items, projects=()):
    """Archived conversations, newest activity first. Replying still works from each one's page."""
    rows = "".join(conversation_row(item, projects) for item in by_activity(item for item in items if is_archived(item)))
    return (f'<p class="empty" id="no-archived"{" hidden" if rows else ""}>'
            'No conversations are archived. A conversation you archive shows here.</p>'
            f'<section class="topics" id="topics-archived"{"" if rows else " hidden"}>'
            f'<h2 class="sr">Archived conversations</h2><ul class="topic-list">{rows}</ul></section>')


def catch_up_row(notice, items_by_id):
    title = esc(notice["title"])
    project = esc(notice.get("project", ""))
    phone_project = f'<p class="meta notice-project">{project}</p>' if project else ""
    links = []
    item = items_by_id.get(notice.get("item"))
    if item is not None:
        links.append(f'<a href="{esc(item_link(item["id"], "needs-you"))}">{esc(item["title"])}</a>')
    if "url" in notice:
        links.append(f'<a href="{esc(notice["url"])}" rel="noopener noreferrer">{esc(notice["link_text"])}</a>')
    return (f'<tr class="notice"{f' data-item="{item["id"]}"' if item is not None else ""}><th scope="row"><span class="id">{notice["id"]}</span> {title}'
            f'{phone_project}<p>{esc(notice["summary"])}</p>{"".join(links)}</th>'
            f'<td class="project">{project}</td><td></td><td>{day_time(notice["saved_at"])}<br>'
            f'<button type="button" data-read="{notice["id"]}" aria-label="Mark read, {title}">Mark read</button>'
            '<p class="meta desk-feedback failed" role="status"></p></td></tr>')


def desk_table(attention, notice_rows="", context=None):
    primary = next((item for item, summary in attention if summary[1]), None)
    rows = "".join(desk_row(item, summary, primary=item is primary, context=context) for item, summary in attention)
    hidden = "" if rows or notice_rows else " hidden"
    return (f'<div class="desk"{hidden}><table><caption class="sr">Items that need you</caption><thead><tr>'
            '<th scope="col">Item</th><th scope="col" class="project">Project</th>'
            f'<th scope="col">Questions</th><th scope="col">Latest</th></tr></thead><tbody>{notice_rows}{rows}</tbody></table></div>')


def needs_you_panel(attention, notices, items_by_id):
    rows = "".join(catch_up_row(notice, items_by_id) for notice in notices)
    hide_bulk = " hidden" if len(notices) < 2 else ""
    hide_empty = " hidden" if attention or notices else ""
    return (f'<button type="button" data-read-all{hide_bulk}>Mark all notices read</button>'
            '<p class="meta desk-feedback failed" role="status"></p>'
            '<p class="sr" id="notice-announcement" role="status"></p>'
            f'{desk_table(attention, rows, "needs-you")}'
            f'<p class="empty"{hide_empty}>Nothing needs you. Questions, unread replies, failed deliveries and notices show here.</p>')


def underway_row(row):
    title, project = esc(row["title"]), esc(row.get("project") or "")
    status = row["status"]
    state = "status exception" if status in ("Question asked", "Stuck") else "status"
    links = []
    if row.get("conversation"):
        links.append(f'<a href="{esc(row["conversation"])}" aria-label="Conversation for {title}">Conversation</a>')
    if row.get("pr"):
        links.append(f'<a href="{esc(row["pr"])}" rel="noopener noreferrer" aria-label="Pull request for {title}">'
                     'Pull request</a>')
    phone_project = f'<p class="meta notice-project">{project}</p>' if project else ""
    return (f'<tr><th scope="row">{title}{phone_project}</th><td class="project">{project}</td>'
            f'<td><span class="{state}">{esc(status)}</span></td><td class="underway-links">{"".join(links)}</td></tr>')


def underway_panel(view):
    """The Under way tab. `view` is None when no Firstmate home is configured."""
    if view is None:
        return '<p class="empty">Work under way shows here once firstmate_home names a Firstmate home.</p>'
    moment = view["observed_at"]
    if moment is None:
        return ('<p class="empty">Quarterdeck has not checked the workers yet. '
                'Work under way shows here after the first check.</p>')
    time = f'<time datetime="{esc(moment)}">{esc(moment)}</time>'
    status = (f'Check failed. Showing the reading from {time}.' if view["failed"] else f'Last checked {time}')
    note = f'<p class="meta underway-status" role="status">{status}</p>'
    if not view["rows"]:
        return (f'{note}<p class="empty">No work is under way. Work that is being built, reviewed or merged shows here.</p>')
    rows = "".join(underway_row(row) for row in view["rows"])
    return (f'{note}<div class="desk underway"><table><caption class="sr">Work under way</caption><thead><tr>'
            '<th scope="col">Work</th><th scope="col" class="project">Project</th><th scope="col">Status</th>'
            f'<th scope="col">Links</th></tr></thead><tbody>{rows}</tbody></table></div>')


def desk_tabs(conversations, attention, underway, archived, total):
    hidden = "" if total else " hidden"
    count = f' <span class="tab-count"{hidden}>{total or ""}</span>'
    panels = (("conversations", "Conversations", conversations),
              ("needs-you", "Needs you", attention),
              ("under-way", "Under way", underway),
              ("archived", "Archived", archived))
    tabs = "".join(f'<button type="button" role="tab" id="desk-tab-{name}" aria-controls="desk-panel-{name}" '
                   f'aria-selected="{"true" if number == 0 else "false"}" tabindex="{0 if number == 0 else -1}">'
                   f'{label}{count if name == "needs-you" else ""}</button>'
                   for number, (name, label, _) in enumerate(panels))
    body = "".join(f'<section class="desk-panel" role="tabpanel" id="desk-panel-{name}" aria-labelledby="desk-tab-{name}"'
                   f'{"" if number == 0 else " hidden"}>{content}</section>'
                   for number, (name, _, content) in enumerate(panels))
    return (f'<div class="desk-tabs" role="tablist" aria-label="Desk">{tabs}</div>'
            '<p class="meta desk-feedback" id="desk-status" role="status"></p>'
            f'{body}')


def latest_unanswered(thread):
    answered = {message["ask"] for message in thread if message["author"] == "firstmate" and "ask" in message}
    pending = (message for message in thread if message["author"] == "you" and message["delivery"] == "sent"
               and message["id"] not in answered)
    return max(pending, key=lambda message: parse_utc(message["at"]), default=None)


def watch_requests(data):
    """Items where Firstmate has a delivered message or ask it has not replied to yet, newest request first."""
    rows = []
    for item in data["items"]:
        if item["kind"] == "conversation":
            thread = part_thread(item, None)
            last_update = max((index for index, message in enumerate(thread)
                               if message["author"] == "firstmate" and "ask" not in message), default=-1)
            waiting = latest_unanswered(thread[last_update + 1:])
            if waiting:
                rows.append((item, "Messaged", waiting["at"], None))
        elif item["kind"] == "questions":
            waiting = []
            for key, thread in item.get("conversation", {}).items():
                latest = latest_unanswered(thread)
                if latest:
                    waiting.append((parse_utc(latest["at"]), latest["at"], key))
            if waiting:
                _, at, key = max(waiting)
                rows.append((item, "Asked", at, item["id"] if key == "item" else f'{item["id"]}.{key}'))
    return sorted(rows, key=lambda row: (parse_utc(row[2]), int(row[0]["id"][1:])), reverse=True)


def watch_page(data, site_title, *, workers_ready):
    rows = []
    for item, status, at, target in watch_requests(data):
        href = esc(item_link(item["id"], "watch", target))
        project = esc(item["project"]) if item.get("project") else ""
        rows.append(f'<tr id="watch-{item["id"]}"><th scope="row"><a href="{href}"><span class="id">{item["id"]}</span> {esc(item["title"])}</a></th>'
                    f'<td class="project">{project}</td><td>{status}</td><td>{day_time(at)}</td></tr>')
    if rows:
        waiting = ('<div class="desk"><table><caption class="sr">Requests Firstmate has not replied to, newest first</caption>'
                   '<thead><tr><th scope="col">Item</th><th scope="col" class="project">Project</th>'
                   '<th scope="col">Latest</th><th scope="col">Sent</th></tr></thead>'
                   f'<tbody>{"".join(rows)}</tbody></table></div>')
    else:
        waiting = '<p class="empty">Firstmate owes you nothing. A message or question it has yet to reply to shows here.</p>'
    if workers_ready:
        work = ('<div id="watch-work"><p class="meta" id="watch-work-status" role="status">Loading current work</p>'
                '<ul class="roster" id="watch-work-list" hidden></ul></div>')
    else:
        work = '<p class="empty">Current work shows here once firstmate_home names a Firstmate home.</p>'
    body = ('<h1>Watch</h1><section class="page-section" aria-labelledby="waiting-title"><h2 id="waiting-title">Waiting on Firstmate</h2>'
            f'{waiting}</section><section class="page-section" aria-labelledby="work-title"><h2 id="work-title">Current work</h2>'
            f'{work}<p class="workers-link"><a href="workers.html">Workers</a></p></section>')
    return page_shell(f"Watch · {site_title}", site_title, body, served=True, saved_name="index.html",
                      current="watch", watching=workers_ready)


def attention_summary(item, *, served):
    kind = item["kind"]
    if kind == "questions":
        state, open_parts, pending = question_summary(item, replies=served)
    elif served and kind == "conversation":
        state, open_parts, pending = ("Replied" if thread_unread(item, None) else "Messaged"), [], False
    else:
        return None
    pending = pending or (served and has_pending_ask(item))
    return (state, open_parts, pending) if open_parts or pending or state == "Replied" else None


def list_page(data: dict[str, object], site_title: str, *, served: bool, saved_name: str = "index.html",
              ask_binding: str | None = None, underway: dict[str, object] | None = None) -> str:
    items = newest_first(data["items"])  # type: ignore[arg-type]
    empty = "Nothing is saved yet. Ask Firstmate to send something to Quarterdeck, such as a link, a list or open questions."
    attention = [(item, summary) for item in items if (summary := attention_summary(item, served=served))]
    if served:
        conversations = [item for item in items if item["kind"] == "conversation"]
        notices = unread_catch_ups(data)
        body = "<h1>Desk</h1>" + desk_tabs(
            conversations_panel(conversations, ask_binding, data.get("projects", [])),
            needs_you_panel(attention, notices, {item["id"]: item for item in items}),
            underway_panel(underway),
            archived_panel(conversations, data.get("projects", [])),
            len(attention) + len(notices),
        )
        return page_shell(site_title, site_title, body, served=True, saved_name=saved_name,
                          library=library_dialog(data), current="desk", starting=True)
    heading = "<h1>Desk</h1>"
    if attention:
        body = heading + desk_table(attention)
    elif not items:
        body = f'{heading}<p class="empty">{esc(empty)}</p>'
    else:
        body = f'{heading}<p class="empty">Nothing is on your Desk. A question that needs an answer shows here.</p>'
    if items:
        references, answered = library_lists(data, served=False)
        body += (f'<section class="page-section"><h2>References</h2>{references}</section>'
                 f'<section class="page-section"><h2>Answered</h2>{answered}</section>')
    return page_shell(site_title, site_title, body, served=False, saved_name=saved_name,
                      filters=bool(items))


def item_body_html(item: dict[str, object]) -> str:
    item_id, body, kind = str(item["id"]), item["body"], item["kind"]
    if kind == "resource":
        return "".join(f'<p><a class="open-link" href="{esc(link["url"])}" rel="noopener noreferrer">{esc(link["text"])}</a></p>'
                       for link in resource_links(body))  # type: ignore[arg-type]
    if kind == "list":
        entries = body["entries"]  # type: ignore[index]
        return '<ol class="numbered">' + "".join(
            f'<li id="{esc(item_id)}.{number}"><span class="id">{esc(item_id)}.{number}</span><span>{esc(entry)}</span></li>'
            for number, entry in enumerate(entries, 1)) + "</ol>"


def list_nav(item, data, context, *, end=False) -> str:
    """Back to the list the page was opened from (the Desk when it names none), and the next unread item in that list."""
    if context is None:
        label, href, following = "Desk", "index.html", None
    else:
        label, href = LIST_KINDS[context["from"]].label, back_link_href(context, item["id"])
        following = next_unread(data, context, item) if data is not None else None
    links = f'<a class="back-link" href="{esc(href)}">Back to {label}</a>'
    if following:
        target_item, target = following
        links += f'<a class="next-link" href="{esc(item_link(target_item["id"], context, target))}">Next unread</a>'
    return f'<nav class="list-nav{" end" if end else ""}" aria-label="List links{" at the end of the page" if end else ""}">{links}</nav>'


def item_page(item: dict[str, object], site_title: str, *, served: bool, saved_name: str = "index.html",
              ask_binding: str | None = None, data: dict[str, object] | None = None, query: str = "") -> str:
    item_id = str(item["id"])
    questions = item["kind"] == "questions"
    talking = item["kind"] == "conversation"
    content = (question_form(item, served, ask_binding=ask_binding) if questions
               else conversation_page(item, ask_binding) if talking else item_body_html(item))
    lead = "" if talking else f'<p class="lead">{linked_text(item["description"])}</p>'
    placed = placement_label(item, (data or {}).get("projects", [])) if talking else ""
    context = parse_list_context(query) if served else None
    body = (
        f'{list_nav(item, data, context) if served else ""}'
        f'<p class="id">{esc(item_id)}</p><h1>{esc(item["title"])}</h1>'
        f'<p class="meta">{esc(KIND_LABELS[str(item["kind"])])}{" · " + esc(placed) if placed else ""}'
        f'{" · " + esc(item["project"]) if item.get("project") else ""}'
        f' · saved <time class="day" datetime="{esc(item["saved_at"])}">{esc(saved_day(item))}</time></p>'
        f'{tag_labels(list(item.get("tags", [])))}'
        f'{conversation_state(item) if talking and served else ""}'
        f'{lead}{content}{list_nav(item, data, context, end=True) if served else ""}'
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


def effective_base_path(args: argparse.Namespace, config: dict[str, object] | None = None) -> str:
    """--base-path when given (even as "" to switch a configured prefix off), else the config's base_path, else none."""
    given = getattr(args, "base_path", None)
    if given is not None:
        return normalize_base_path(given)
    if config is None:
        config = selected_config(args)
    return normalize_base_path(config.get("base_path"))


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


# Every served Desk load is logged so Firstmate's adoption reviews can see when the page is used.
VISITS_NAME = "visits.jsonl"
PHONE_AGENT_RE = re.compile(r"Mobile|iPhone|iPod", re.IGNORECASE)


def visits_path() -> Path:
    return state_dir() / VISITS_NAME


def device_class(user_agent: str | None) -> str:
    """'phone' for a phone browser, otherwise 'desktop' (tablets and unknown browsers included)."""
    return "phone" if user_agent and "ipad" not in user_agent.lower() and PHONE_AGENT_RE.search(user_agent) else "desktop"


class VisitLog:
    """Append-only local log of page loads: time, page and device class only, never an address or content."""

    def __init__(self, path: Path, clock=lambda: datetime.now(timezone.utc)) -> None:  # noqa: ANN001 - test hook
        self.path = path
        self.clock = clock

    def record(self, user_agent: str | None) -> None:
        stamp = self.clock().astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        line = json.dumps({"ts": stamp, "page": "main", "device": device_class(user_agent)}) + "\n"
        payload = line.encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"visit log must be a regular file: {self.path}")
            if stat.S_IMODE(info.st_mode) != 0o600:
                os.fchmod(descriptor, 0o600)

            end = info.st_size
            if end and os.pread(descriptor, 1, end - 1) != b"\n":
                payload = b"\n" + payload

            if os.write(descriptor, payload) != len(payload):
                os.ftruncate(descriptor, end)
                raise OSError(errno.EIO, "incomplete visit log write", self.path)
        finally:
            os.close(descriptor)


def ask_destination(ask_home: Path | None, *, require_directory: bool) -> tuple[Path | None, str | None]:
    if ask_home is not None:
        try:
            resolved = ask_home.resolve(strict=require_directory)
            if require_directory and not resolved.is_dir():
                return None, None
        except (OSError, RuntimeError):
            return None, None
        return resolved, hashlib.sha256(os.fsencode(resolved)).hexdigest()
    return None, None


def make_handler(site_title: str, base_path: str = "", store: Path | None = None, home: Path | None = None,
                 *, ask_home: Path | None = None, workers_home: Path | None = None,
                 retention_days: int = CATCH_UP_DAYS,
                 saved_workers: SavedWorkers | None = None) -> type[BaseHTTPRequestHandler]:
    """Every page is built from the item store when it is requested."""
    visit_log = VisitLog(visits_path())
    if saved_workers is None and workers_home is not None:
        saved_workers = SavedWorkers(workers_home, workers_cache_path(store))

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
                reuse = request.query == "reuse=1"
                try:
                    snapshot = saved_workers.refresh(reuse=reuse)
                except SnapshotError:
                    self.send_text(502, json.dumps({"error": "The workers could not be read."}), "application/json")
                else:
                    self.send_text(200, json.dumps(snapshot), "application/json")
                return
            if name == "workers-saved.json" and workers_home is not None:
                self.send_text(200, json.dumps(saved_workers.view()), "application/json")
                return
            if name == "workers.html":
                self.send_text(200, workers_page(site_title, ready=workers_home is not None), "text/html; charset=utf-8")
                return
            picture = IMAGE_URL_RE.fullmatch(name)
            discussion = re.fullmatch(r"(q[1-9][0-9]*)[.]discussion[.]json", name)
            if name not in ("index.html", "watch.html") and not picture and not discussion and not ITEM_PAGE_RE.fullmatch(name):
                self.send_text(404, "Not found\n")
                return
            try:
                data = read_store(store or store_path())
            except ValueError:
                self.send_text(500, "Quarterdeck could not read its item store\n")
                return
            if name == "watch.html":
                self.send_text(200, watch_page(data, site_title, workers_ready=workers_home is not None),
                               "text/html; charset=utf-8")
                return
            if picture:
                self.send_image(data, *picture.groups())
                return
            if name == "index.html":
                try:
                    visit_log.record(self.headers.get("User-Agent"))
                except (OSError, ValueError) as exc:  # a visit that cannot be logged must never stop the page
                    print(f"quarterdeck: could not log the visit: {exc}", file=sys.stderr, flush=True)
                _, ask_binding = ask_destination(ask_home, require_directory=True)
                underway = (underway_view(saved_workers, read_task_links(workers_home / TASK_LINKS_DEFAULT),
                                          data["items"]) if workers_home is not None else None)
                self.send_text(200, list_page(data, site_title, served=True, ask_binding=ask_binding, underway=underway),
                               "text/html; charset=utf-8",
                               prevent_framing=True)
                return
            try:
                item = find_item(data, discussion[1] if discussion else name.removesuffix(".html"))
            except ValueError:
                self.send_text(404, "Not found\n")
                return
            if discussion:
                if item["kind"] == "questions":
                    _, binding = ask_destination(ask_home, require_directory=False)
                    snapshot = set_discussion(item, editable=binding is not None and conversation_editable(item, None))
                    self.send_text(200, json.dumps(snapshot), "application/json")
                else:
                    self.send_text(404, "Not found\n")
                return
            ask_binding = None
            if item["kind"] in ("questions", "conversation"):
                _, ask_binding = ask_destination(ask_home, require_directory=item["kind"] == "conversation")
            content = item_page(item, site_title, served=True, ask_binding=ask_binding, data=data, query=request.query)
            prevent_framing = has_answer_controls(item) or item["kind"] == "conversation" or (
                item["kind"] == "questions" and (bool(part_thread(item, None)) or question_summary(item)[0] == "Replied"))
            self.send_text(200, content, "text/html; charset=utf-8", prevent_framing=prevent_framing)

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            endpoint = self.path.removeprefix(base_path)
            subjects = {"/answers": "answer", "/asks": "ask", "/conversations": "conversation", "/replies/read": "reply acknowledgment",
                        "/pins": "pin", "/resolutions": "resolution", "/archives": "archive",
                        "/groups": "project", "/moves": "move",
                        "/catch-ups/read": "catch-up read"}
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
                ask_path, ask_binding = ask_destination(ask_home, require_directory=True)
                home_available = ask_path is not None
                if endpoint == "/asks" and not home_available:
                    ask_path, ask_binding = ask_destination(ask_home, require_directory=False)
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
                    record = ask_message(active_store, ask_path, ask_binding, payload, home_available=home_available)
                elif endpoint == "/conversations":
                    record = start_conversation(active_store, ask_path, ask_binding, payload)
                elif endpoint == "/pins":
                    record = pin_conversation(active_store, payload)
                elif endpoint == "/resolutions":
                    record = set_conversation_flag(active_store, payload, "resolved")
                elif endpoint == "/archives":
                    record = set_conversation_flag(active_store, payload, "archived")
                elif endpoint == "/groups":
                    record = change_projects(active_store, payload)
                elif endpoint == "/moves":
                    record = move_conversation(active_store, payload)
                elif endpoint == "/catch-ups/read":
                    if not isinstance(payload, dict) or set(payload) != {"ids"}:
                        raise ValueError("Invalid catch-up read.")
                    ids = payload["ids"]
                    if not isinstance(ids, list) or not ids or not all(
                        isinstance(value, str) and CATCH_UP_ID_RE.fullmatch(value) for value in ids
                    ):
                        raise ValueError("Invalid catch-up IDs.")
                    if len(set(ids)) != len(ids):
                        raise ValueError("Duplicate catch-up IDs.")
                    record = {"read": mark_catch_ups_read(active_store, ids, retention_days)}
                else:
                    record = mark_replies_read(active_store, payload)
            except AskHomeUnavailable:
                self.send_text(403, "Configure an available firstmate_home to ask Firstmate.")
            except OptionChanged as exc:
                self.send_text(409, json.dumps({"clarification": clarified_html(exc.notes, True)}), "application/json")
            except CatchUpConflict as exc:
                self.send_text(409, str(exc))
            except AnswerConflict as exc:
                if endpoint in ("/asks", "/conversations", "/groups"):
                    record = {"message": str(exc), "saved": isinstance(exc, SavedAskConflict)}
                    self.send_text(409, json.dumps(record), "application/json")
                else:
                    self.send_text(409, str(exc))
            except (OSError, StoreReadError):
                self.send_text(500, f"Could not save the {subject}.")
            except (ValueError, RecursionError):
                self.send_text(400, f"Check the conversation and {subject}." if endpoint in ("/pins", "/resolutions", "/archives", "/moves")
                               else "Check the name and try again." if endpoint == "/groups"
                               else f"Check the question and {subject}.")
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
            return (config_path.parent / inbox_path(homes[0].get("path"))).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
    return None


def serve(args: argparse.Namespace) -> int:
    servers: list[ThreadingHTTPServer] = []
    try:
        config_path = selected_config_path(args)
        if config_path is not None:
            try:
                config_path = config_path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValueError(f"could not resolve serving config {config_path}: {exc}") from exc
        config = load_config(config_path)
        tls = tls_context(args)
        base_path = effective_base_path(args, config)
        firstmate = configured_firstmate_home(config)
        saved_workers = SavedWorkers(firstmate, workers_cache_path()) if firstmate is not None else None
        handler = make_handler(page_title(args, config), base_path, home=answer_home(config, config_path),
                               ask_home=firstmate, workers_home=firstmate, retention_days=catch_up_days(config),
                               saved_workers=saved_workers)
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
              "read saved items, answers, conversations, images, and unread Catch-up notices. They can submit answers, pin, resolve and archive topics, acknowledge "
              "replies, and mark Catch-up notices read by ID. "
              "With firstmate_home set, they can read or check the worker and second mate roster, "
              "send asks, start conversations and send messages.",
              file=sys.stderr, flush=True)
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    stop_refreshing = threading.Event()
    if firstmate is not None and saved_workers is not None:
        threading.Thread(target=keep_workers_fresh, daemon=True,
                         args=(saved_workers, WORKERS_REFRESH_SECONDS, stop_refreshing)).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_refreshing.set()
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
              "read saved items and unread Catch-up notices. They can send answers and mark Catch-up notices read by ID as the owner.",
              file=sys.stderr)
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
    "catch-up": ["how-to/catch-up.md", "reference/cli-and-config.md", "reference/item-store.md"],
    "stats": ["how-to/read-the-report.md", "reference/cli-and-config.md"],
    "add": ["how-to/save-items.md", "how-to/classify-items.md", "reference/cli-and-config.md", "reference/item-store.md"],
    "edit": ["how-to/save-items.md", "reference/cli-and-config.md", "reference/item-store.md"],
    "list": ["how-to/save-items.md", "reference/cli-and-config.md"],
    "show": ["how-to/read-answers.md", "how-to/save-items.md", "reference/cli-and-config.md"],
    "reply": ["how-to/reply-from-the-command-line.md", "how-to/talk-to-firstmate.md", "reference/cli-and-config.md", "reference/item-store.md"],
    "post": ["how-to/reply-from-the-command-line.md", "reference/cli-and-config.md", "reference/item-store.md"],
    **dict.fromkeys(("resolve", "reopen", "archive", "unarchive"),
                    ["how-to/talk-to-firstmate.md", "reference/cli-and-config.md", "reference/item-store.md"]),
    "converse": ["how-to/reply-from-the-command-line.md", "how-to/talk-to-firstmate.md", "reference/cli-and-config.md", "reference/item-store.md"],
    "remove": ["how-to/save-items.md", "reference/cli-and-config.md"],
    "render": ["how-to/serve-the-page.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "serve": ["how-to/catch-up.md", "how-to/answer-questions.md", "how-to/use-the-library.md", "how-to/ask-firstmate.md", "how-to/talk-to-firstmate.md", "how-to/read-workers.md", "how-to/serve-the-page.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
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
    add.add_argument("--url", action="append", metavar="URL", help="a resource's http(s) address; repeat with --link-text for more links")
    add.add_argument("--link-text", action="append", metavar="TEXT", help="the words of a resource's link")
    add.add_argument("--project", help="the item's project name or owner/name repository")
    add.add_argument("--tag", dest="tags", action="append", metavar="TAG",
                     help=f"a lowercase topic tag, repeatable, up to {TAG_LIMIT}; allowed: {', '.join(CLASSIFICATION['tag'])}")
    add.add_argument("--body-file", metavar="PATH", help="the body of a list or questions; - reads standard input. Questions may carry PNG, JPEG or still WebP images "
                                                          "(up to 5 MB each) that are copied in when saved")

    edit = command("edit", "change a resource or question item's title, description or links",
                   "Change a resource or question item's title, description or, for a resource, its links, in place. "
                   "The ID, saved date, answers and messages stay. Giving --link-text and --url replaces all of a "
                   "resource's links. Lists and conversations cannot be edited.", edit_item)
    edit.add_argument("id", help="item ID, such as Q7")
    edit.add_argument("--title", help="the new title")
    edit.add_argument("--description", help="the new description")
    edit.add_argument("--url", action="append", metavar="URL", help="the http(s) address for the matching --link-text")
    edit.add_argument("--link-text", action="append", metavar="TEXT", help="the words of a link; repeat for more links. All links are replaced")

    command("list", "list saved items, newest first", "List saved items, newest first.", list_items)

    show = command("show", "show one item in full", "Show one item in full, with its parts numbered (Q7.1, Q7.2, ...).", show_item)
    show.add_argument("id", help="item ID, such as Q7")

    def attachment_options(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--link-text", action="append", metavar="TEXT", help="the words of a link; repeat for more links")
        sub.add_argument("--url", action="append", metavar="URL", help="the http(s) address for the matching --link-text")
        sub.add_argument("--image", action="append", metavar="PATH", help="a PNG, JPEG or still WebP file (up to 5 MB) copied into the private store; repeat for more")
        sub.add_argument("--alt", action="append", metavar="TEXT", help="alt text that describes the matching --image")

    reply = command("reply", "add Firstmate's reply to an ask or a conversation message",
                    "Add Firstmate's reply under an ask on a question item (Q7) or one question (Q7.1), or under the owner's "
                    "message in any conversation. For a note that begins CONVERSATION, use its item ID (such as Q12) and message ID. "
                    "The reply shows as Unread reply until the owner opens it. A reply leaves answers "
                    "and states intact. Pair each --link-text with a --url and each --image with an --alt, in the order given.", reply_item)
    reply.add_argument("id", help="item or question ID, such as Q7 or Q7.1; a conversation's item ID, such as Q12")
    reply.add_argument("--ask", required=True, metavar="ID", help="the ask or message ID from the inbox note")
    reply.add_argument("--text", required=True, help="the reply")
    attachment_options(reply)
    reply.add_argument("--clarify", action="append", metavar="Q7.1=TEXT",
                       help="on a reply to the whole item: context that now shows at that question; repeat for more questions")
    reply.add_argument("--option-changed", action="append", metavar="Q7.1",
                       help="with --clarify for the same question: an option really changed, so the owner reviews a draft before sending")

    post = command("post", "post Firstmate's own update on a conversation",
                   "Add an update from Firstmate to a conversation, with no ask to answer. It shows in time order as a "
                   "Firstmate update and as Unread reply until the owner opens it. Sends no inbox note. Pair each --link-text "
                   "with a --url and each --image with an --alt, in the order given.", post_update)
    post.add_argument("id", help="a conversation's item ID, such as Q12")
    post.add_argument("--text", required=True, help="the message")
    attachment_options(post)

    for name, handler, verb in (("resolve", resolve_conversation, "mark a conversation resolved"),
                                ("reopen", reopen_conversation, "mark a resolved conversation not resolved"),
                                ("archive", archive_conversation, "move a conversation to Archived"),
                                ("unarchive", unarchive_conversation, "return an archived conversation to the Desk")):
        sub_parser = command(name, verb, f"{verb[0].upper()}{verb[1:]}. It changes nothing else: messages stay, "
                             "replies still work and the other flags are kept.", handler)
        sub_parser.add_argument("id", help="a conversation's item ID, such as Q12")

    converse_cmd = command("converse", "open a conversation in Firstmate's name and print its ID",
                           "Open a conversation with a title and a first message from Firstmate, shown as a Firstmate update "
                           "and as Unread reply until the owner opens it. Sends no inbox note; the owner and Firstmate continue "
                           "it as any conversation. Pair each --link-text with a --url and each --image with an --alt.", converse)
    converse_cmd.add_argument("--title", required=True, help="a short topic that says what the conversation is about")
    converse_cmd.add_argument("--config", type=Path, help="use the same JSON config file as serve")
    converse_cmd.add_argument("--text", required=True, help="the message")
    converse_cmd.add_argument("--project", metavar="ID", help="file the conversation in this project, by ID such as P1; see quarterdeck projects")
    converse_cmd.add_argument("--topic", metavar="ID", help="with --project: file it in this topic of the project, by ID such as T2")
    attachment_options(converse_cmd)

    command("projects", "list the projects and topics a conversation can be filed in",
                           "List the owner's projects and the topic in each, with their IDs. The owner creates and renames "
                           "them on the Desk; use an ID with converse or move.", list_projects)

    move_cmd = command("move", "move a conversation to a project and topic, or back to Ungrouped",
                       "Change which project and topic a conversation is filed in. The conversation keeps its ID, messages, "
                       "pin and latest activity.", move_item)
    move_cmd.add_argument("id", help="a conversation's item ID, such as Q12")
    move_cmd.add_argument("--project", metavar="ID", help="the project, by ID such as P1")
    move_cmd.add_argument("--topic", metavar="ID", help="with --project: a topic of that project, by ID such as T2")
    move_cmd.add_argument("--ungrouped", action="store_true", help="return the conversation to Ungrouped")

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

    catch_up = command("catch-up", "save and read catch-up notices",
                       "Short notices Firstmate saves when finished work is ready. List unread notices with catch-up list. "
                       "After a notice has been read for catch_up_days days (default "
                       f"{CATCH_UP_DAYS}), the next save or mark-read removes it. Quarterdeck never saves one by itself.",
                       None)
    catch_up_actions = catch_up.add_subparsers(dest="catch_up_action", required=True)
    catch_up_add = catch_up_actions.add_parser("add", help="save a notice and print its ID",
                                               description="Save a notice and print its ID, such as C4.")
    catch_up_add.set_defaults(handler=add_catch_up)
    catch_up_add.add_argument("--title", required=True, help="a short title that says what is ready")
    catch_up_add.add_argument("--summary", required=True, help="one sentence about what happened or what to do next")
    catch_up_add.add_argument("--url", help="the http(s) address of a pull request or a page")
    catch_up_add.add_argument("--link-text", help="the words of that link; required with --url")
    catch_up_add.add_argument("--item", metavar="ID", help="a saved item the notice is about, such as Q7")
    catch_up_add.add_argument("--project", help="the notice's project name or owner/name repository")
    catch_up_list = catch_up_actions.add_parser("list", help="list unread notices, newest first",
                                                description="List unread notices, newest first.")
    catch_up_list.set_defaults(handler=list_catch_ups)
    catch_up_read = catch_up_actions.add_parser("read", help="mark notices read",
                                                description="Print notice details, then mark the notices read.")
    catch_up_read.set_defaults(handler=read_catch_up)
    catch_up_read.add_argument("ids", nargs="*", metavar="ID", help="notice IDs, such as C4")
    catch_up_read.add_argument("--all", action="store_true", help="mark every unread notice read")

    remove = command("remove", "delete an item", "Delete an item. Its ID is never given to another item.", remove_item)
    remove.add_argument("id", help="item ID, such as Q7")

    render_parser = command("render", "write the page to HTML files",
                            "Write HTML pages for the Desk, resources, lists and question items.", render)
    render_parser.add_argument("--output", help="HTML file path for the Desk page; the other pages go beside it")
    render_parser.add_argument("--config", type=Path, help="optional JSON config file")
    render_parser.add_argument("--title", help="override the page title")

    serve_parser = command("serve", "serve the page over HTTP",
                           "Anyone who can reach the service can read saved items, answers, conversations and reply images. "
                           "They can read unread Catch-up notices, including their details and links. They can submit answers, pin, resolve and archive topics, acknowledge replies, and mark Catch-up notices read by ID "
                           "as the owner. With firstmate_home set, they can also "
                           "send asks, start conversations, send messages and read or check that home's workers "
                           "and second mates from the Workers page or workers.json. Browser "
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
