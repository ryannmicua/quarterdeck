#!/usr/bin/env python3
"""Quarterdeck: a local page of the things the captain asked Firstmate to save by hand."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import gzip
import html
import ipaddress
import json
import os
import re
import shlex
import signal
import socket
import ssl
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


# --- The item store -----------------------------------------------------------------
# One JSON file Quarterdeck owns. Every item was saved by hand through `quarterdeck add`; its body is stored by value
# at save time and never read again from anywhere else. An ID is never reused, even after its item is removed.

STORE_VERSION = 1
KIND_LABELS = {"resource": "Resource", "list": "List", "questions": "Questions"}
KINDS = tuple(KIND_LABELS)
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


def question_entries(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("questions body must be a non-empty JSON array")
    questions = []
    for number, entry in enumerate(raw, 1):
        where = f"question {number}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        unknown = sorted(set(entry) - {"question", "context", "options", "suggestion", "links"})
        if unknown:
            raise ValueError(f"{where} has unsupported field(s): {', '.join(unknown)}")
        options = entry.get("options", [])
        if not isinstance(options, list):
            raise ValueError(f"{where} options must be a list of text")
        cleaned: dict[str, object] = {
            "question": clean_text(entry.get("question"), f"{where} question", TEXT_LIMIT, multiline=True),
            "context": clean_text(entry.get("context"), f"{where} context", TEXT_LIMIT, multiline=True),
            "options": [clean_text(option, f"{where} option", TEXT_LIMIT) for option in options],
        }
        if entry.get("suggestion") not in (None, ""):
            cleaned["suggestion"] = clean_text(entry["suggestion"], f"{where} suggestion", TEXT_LIMIT, multiline=True)
        links = question_links(entry.get("links", []), where)
        if links:
            cleaned["links"] = links
        if not cleaned["options"] and "suggestion" not in cleaned:
            raise ValueError(f"{where} needs options or a suggestion to decide from")
        questions.append(cleaned)
    return questions


def validated_body(kind: str, body: object) -> dict[str, object]:
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    if kind == "resource":
        return {"url": clean_url(body.get("url")), "link_text": clean_text(body.get("link_text"), "link text", TITLE_LIMIT)}
    if kind == "list":
        return {"entries": list_entries(body.get("entries"))}
    return {"questions": question_entries(body.get("questions"))}


def make_body(kind: str, *, url: str | None, link_text: str | None, body: str | None) -> dict[str, object]:
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
    return validated_body(kind, {"questions": questions})


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
            body = validated_body(item["kind"], item.get("body"))
            if (title, description, body) != (item["title"], item["description"], item["body"]):
                raise ValueError("item text or body is not in the saved format")
            if "answers" in item:
                validate_records(item)
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
def changing_store(path: Path) -> Iterator[dict[str, object]]:
    """Read the store under an exclusive lock, let the caller change it, then write it back atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path.with_name(path.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_store(path)
        yield data
        write_private(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


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
        if any(k in record for k in ("answer", "answered_at", "delivery", "request_id")):
            if (clean_text(record.get("answer"), "answer", TEXT_LIMIT, multiline=True) != record["answer"]
                    or record.get("delivery") not in ("pending", "sent")
                    or not re.fullmatch(r"quarterdeck-[0-9a-f]{32}", str(record.get("request_id")))):
                raise ValueError("invalid saved answer")
            datetime.strptime(str(record.get("answered_at")), "%Y-%m-%dT%H:%M:%SZ")


def put_record(item, part, state, **fields):
    record = {**fields, "state": state, "revision": uuid.uuid4().hex}
    item.setdefault("answers", {})[str(part)] = record
    return record


def mark_item(args):
    def action():
        item_id, dot, part = args.id.partition(".")
        with changing_store(store_path()) as data:
            item = find_item(data, item_id)
            parts = question_parts(item)
            if dot:
                if not re.fullmatch(r"[1-9][0-9]*", part) or int(part) not in parts:
                    raise ValueError("Unknown question.")
                parts = [int(part)]
            for number in parts:
                previous = part_record(item, number)
                put_record(item, number, args.state, **{k: previous[k] for k in
                           ("answer", "answered_at", "delivery", "request_id") if k in previous})
        print(f"{args.id.upper()} {args.state.capitalize()}")
        return 0
    return run_item_command(action)


class AnswerConflict(ValueError):
    pass


def find_answer_item(data, item_id):
    try:
        return find_item(data, item_id)
    except ValueError as exc:
        if int(item_id[1:]) < data["next_id"]:
            raise AnswerConflict("Item removed. Reload before sending.") from exc
        raise


def send_answer(store, home, payload):
    if not isinstance(payload, dict) or set(payload) != {"part", "answer", "revision"}:
        raise ValueError("Invalid answer fields.")
    if not isinstance(payload["revision"], str) or not re.fullmatch(r"(?:[0-9a-f]{32})?", payload["revision"]):
        raise ValueError("Invalid question revision.")
    match = re.fullmatch(r"(Q[1-9][0-9]*)[.]([1-9][0-9]*)", str(payload["part"]))
    if not match:
        raise ValueError("Unknown question.")
    item_id, part = match[1], int(match[2])
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
        if not (same and state == "answered") and payload["revision"] != old.get("revision", ""):
            raise AnswerConflict("Question changed. Reload before sending.")
        if same:
            record = old
        else:
            record = put_record(item, part, "answered", answer=answer, answered_at=now_text(),
                                delivery="pending", request_id="quarterdeck-" + uuid.uuid4().hex)
    # The first transaction commits before the external call. Serialize delivery against edits and reuse its note ID on retries.
    if home and delivery_pending(record):
        with changing_store(store) as data:
            record_now = part_record(find_answer_item(data, item_id), part)
            if record_now["revision"] != record["revision"]:
                raise AnswerConflict("Question changed. Reload before sending.")
            record = record_now
            if delivery_pending(record):
                try:
                    env = {k: v for k, v in os.environ.items() if not k.startswith("FM_")}
                    result = subprocess.run([home / "bin/fm-inbox.sh", "note", "--request-id", record["request_id"],
                                             "--json", "--", f"{item_id}.{part} {record['answer']}"],
                                            cwd=home, stdin=subprocess.DEVNULL, timeout=10,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**env, "FM_HOME": str(home)})
                    if result.returncode == 0:
                        record["delivery"] = "sent"
                except (OSError, subprocess.TimeoutExpired):
                    pass
    return {k: record[k] for k in ("state", "revision", "answer", "answered_at", "delivery")}


def item_text(item: dict[str, object]) -> str:
    """One item as plain text, numbered the way the page numbers it."""
    item_id, body = str(item["id"]), item["body"]
    lines = [f"{item_id} · {item['title']}", f"{KIND_LABELS[str(item['kind'])]} · saved {saved_day(item)}",
             "", str(item["description"]), ""]
    if item["kind"] == "resource":
        lines.append(f"{body['link_text']}: {body['url']}")  # type: ignore[index]
    elif item["kind"] == "list":
        lines.extend(f"{item_id}.{number}  {entry}" for number, entry in enumerate(body["entries"], 1))  # type: ignore[index]
    else:
        state, open_parts, _ = question_summary(item)
        remaining = len(open_parts)
        lines.extend([f"{state} ({remaining} of {len(question_parts(item))} open)" if remaining else state, ""])
        for number, question in enumerate(body["questions"], 1):  # type: ignore[index]
            lines.append(f"{item_id}.{number}  {question['question']}")
            lines.append(f"      Context: {question['context']}")
            for link in question.get("links", []):
                lines.append(f"      Link: {link['text']}: {link['url']}")
            if question["options"]:
                lines.append("      Options: " + " | ".join(question["options"]))
            if question.get("suggestion"):
                lines.append(f"      Suggestion: {question['suggestion']}")
            record = part_record(item, number)
            lines.append("      " + record.get("state", "open").capitalize())
            if "answer" in record:
                lines.append(f"      Answer: {record['answer']} ({record['answered_at']}, {delivery_label(record)})")
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


def add_item(args: argparse.Namespace) -> int:
    def action() -> int:
        body = make_body(args.kind, url=args.url, link_text=args.link_text, body=read_body(args.body_file))
        item: dict[str, object] = {
            "kind": args.kind, "title": clean_text(args.title, "title", TITLE_LIMIT),
            "description": clean_text(args.description, "description", DESCRIPTION_LIMIT, multiline=True),
            "saved_at": now_text(), "body": body,
        }
        with changing_store(store_path()) as data:
            item = {"id": f"Q{data['next_id']}", **item}
            data["next_id"] = int(data["next_id"]) + 1  # type: ignore[call-overload]
            data["items"].append(item)  # type: ignore[union-attr]
        print(item["id"])
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
        with changing_store(store_path()) as data:
            item = find_item(data, args.id)
            data["items"] = [other for other in data["items"] if other is not item]  # type: ignore[union-attr]
        print(f"Removed {item['id']}; its ID is not reused")
        return 0
    return run_item_command(action)


# --- Workers ------------------------------------------------------------------------------
# The one deliberate read of a Firstmate home: its own read-only fleet snapshot, run without a model.
# Quarterdeck keeps nothing from it, shows only what the snapshot states outright, and never writes to the home.

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
@media (min-width: 40.01rem) { :root { --gutter: 2rem; --page: 64rem; } }
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
table.items { width: 100%; border-collapse: collapse; font-family: var(--sans); font-size: var(--text-sm); }
table.items th { text-align: left; color: var(--soft); font-weight: 600; border-bottom: 2px solid var(--ink); padding: .5rem .6rem .5rem 0; }
table.items td { border-bottom: 1px solid var(--rule); padding: .7rem .6rem .7rem 0; vertical-align: top; }
table.items td.title a { font: 700 var(--text-md)/1.35 var(--serif); }
table.items td.when { white-space: nowrap; }
@media (max-width: 40rem) {
  table.items thead { display: none; }
  table.items, table.items tbody, table.items tr, table.items td { display: block; width: 100%; }
  table.items tr { border-bottom: 1px solid var(--rule); padding: .8rem 0; }
  table.items td { border: 0; padding: .1rem 0; }
  table.items td.kind, table.items td.when { display: inline; color: var(--soft); margin-right: .6rem; }
  table.items td.id { display: inline; margin-right: .6rem; }
}
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
.eyebrow,.actions,.toolbar,label,button,output,.progress { font-family: var(--sans); }
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
button[hidden] { display:none; }
button.primary { color:var(--raised); background:var(--ink); }
button:disabled, button[aria-disabled="true"] { opacity:.55; cursor:default; }
:focus-visible { outline:3px solid var(--focus); outline-offset:2px; }
.toolbar { position:sticky; top:0; z-index:1; background:var(--ground); padding:.6rem 0; border-bottom:1px solid var(--rule); }
.decision,button,textarea { scroll-margin-top:10rem; }
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
table.items td.when { white-space:normal; }
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
@media (max-width: 40rem) {
  .desk .work { grid-template-columns:minmax(0,1fr); }
  .desk .work > .actions { grid-column:1; grid-row:auto; justify-content:flex-start; margin-top:.6rem; }
}
"""


TIME_SCRIPT = """
const localTime = dateTime => new Date(dateTime).toLocaleString(undefined, {dateStyle:'medium', timeStyle:'short'});
for (const time of document.querySelectorAll('.saved-answer time[datetime]:not([datetime=""])')) {
  time.textContent = localTime(time.dateTime);
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
        parts.append(f'<a href="{esc(url)}" rel="noopener noreferrer">{esc(url)}</a>')
        last = position
    return "".join(parts) + esc(text[last:])


def item_page_name(item_id: str) -> str:
    return f"{item_id.lower()}.html"


def page_shell(title: str, site_title: str, body: str, *, served: bool, saved_name: str, interactive: bool = False,
               workers: bool = False) -> str:
    saved_href = quote(saved_name, safe="")
    links = (
        '<link rel="manifest" href="manifest.webmanifest">\n  <link rel="icon" href="icon.svg" type="image/svg+xml">\n'
        f'  <link rel="apple-touch-icon" href="icon-180.png">\n  <meta name="theme-color" content="{APP_NAVY}">\n  '
    ) if served else ""
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  {GENERATED_MARKER}\n  <title>{esc(title)}</title>\n  {links}<style>{PAGE_CSS}</style>\n</head>\n<body>\n"
        f'<header class="bar"><a class="mark" href="{saved_href}">{esc(site_title)}</a>'
        f'<nav><a href="{saved_href}">Saved</a></nav></header>\n'
        f"<main>\n{body}\n</main><script>{TIME_SCRIPT}{ANSWER_SCRIPT if interactive else ''}{WORKERS_SCRIPT if workers else ''}</script>\n</body>\n</html>\n"
    )


def items_table(items: list[dict[str, object]]) -> str:
    rows = "".join(
        f'<tr><td class="id">{esc(item["id"])}</td><td class="kind">{esc(KIND_LABELS[str(item["kind"])])}</td>'
        f'<td class="title"><a href="{item_page_name(str(item["id"]))}">{esc(item["title"])}</a></td>'
        f'<td class="description">{linked_text(item["description"])}</td>'
        f'<td class="when"><time datetime="{esc(item["saved_at"])}">{esc(saved_day(item))}</time></td></tr>'
        for item in items
    )
    return ('<table class="items"><thead><tr><th scope="col">ID</th><th scope="col">Kind</th><th scope="col">Title</th>'
            f'<th scope="col">Description</th><th scope="col">Saved</th></tr></thead><tbody>{rows}</tbody></table>')


def delivery_label(record):
    return "Sent" if record["delivery"] == "sent" else "Not delivered"


def question_summary(item) -> tuple[str, list[int], bool]:
    states = [part_record(item, n).get("state", "open") for n in question_parts(item)]
    open_parts = [n for n, state in enumerate(states, 1) if state == "open"]
    state = "Open" if open_parts else ("Closed" if all(s == "closed" for s in states) else "Answered")
    pending = any(delivery_pending(r) for r in item.get("answers", {}).values())
    return state, open_parts, pending


def question_card(item, summary, *, served, primary):
    state, open_parts, pending = summary
    remaining = len(open_parts)
    href = item_page_name(item["id"])
    if primary:
        href += f'#{item["id"]}.{open_parts[0]}'
    progress = f'{remaining} of {len(question_parts(item))} open' if remaining else state
    action = "Read questions" if not served else ("Answer questions" if remaining else "Review answers")
    link_class = ' class="work-action"' if primary else ' class="button-link"'
    questions = item["body"]["questions"]
    prompts = "".join(f'<li>{esc(questions[n - 1]["question"])}</li>' for n in open_parts)
    first_link = next((link_html(questions[n - 1]["links"][0], prefix=f'{item["id"]}.{n} ')
                       for n in open_parts if questions[n - 1].get("links")), '')
    visual = f'<p class="visual">{first_link}</p>' if first_link else ''
    return (f'<article class="work{' next' if primary else ''}"><div class="eyebrow"><span class="id">{item["id"]}</span> Questions</div>'
            f'<h3>{esc(item["title"])}</h3><p>{linked_text(item["description"])}</p>'
            + (f'<ul>{prompts}</ul>' if prompts else '')
            + visual
            + f'<div class="actions"><span class="badge">{progress}</span>'
            + ('<span>Not delivered</span>' if pending else '')
            + f'<a{link_class} href="{href}">{action}</a></div></article>')


def link_html(link, *, prefix=""):
    return (f'<a class="ref-link" href="{esc(link["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{esc(prefix + link["text"])}</a>')


def links_html(question):
    links = question.get("links")
    return '<ul class="ref-links">' + ''.join(f'<li>{link_html(l)}</li>' for l in links) + '</ul>' if links else ''


def has_answer_controls(item):
    return item["kind"] == "questions" and any(
        part_record(item, n).get("state") != "closed" for n in question_parts(item))


def question_form(item, served):
    cards = []
    state, open_parts, _ = question_summary(item)
    remaining = len(open_parts)
    for number, question in enumerate(item["body"]["questions"], 1):
        part = f'{item["id"]}.{number}'
        record = part_record(item, number)
        status = record.get("state", "open")
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
        editor = ""
        if editable:
            editor = (f' data-revision="{esc(record.get("revision", ""))}"'
                      f' data-state="{status}" data-pending="{int(delivery_pending(record))}"')
            suggestions = question.get("suggestion")
            choices = list(question["options"])
            if suggestions and suggestions not in choices:
                choices.append(suggestions)
            labels = []
            for choice in choices:
                suggested = choice == suggestions
                labels.append(f'<label class="choice"><input type="radio" name="choice-{part}" data-choice="{esc(choice)}"'
                              + (' data-suggested' if suggested else '') + f'> {esc(choice)}'
                              + (' <small>Suggested</small>' if suggested else '') + '</label>')
            controls = (links_html(question) + '<div class="choices">' + ''.join(labels) + '</div>'
                        + f'<label for="answer-{part}">Your answer</label><textarea id="answer-{part}" '
                        + f'maxlength="{TEXT_LIMIT}" rows="2" aria-describedby="error-{part}">{esc(answer)}</textarea>'
                        + f'<p id="error-{part}" hidden></p>')
        else:
            controls = links_html(question) + '<ul>' + ''.join(f'<li>{esc(v)}</li>' for v in question["options"]) + '</ul>'
            if question.get("suggestion"):
                controls += f'<p><strong>Suggested answer</strong><br>{esc(question["suggestion"])}</p>'
        cards.append(f'<section class="decision" id="{part}" aria-labelledby="title-{part}"{editor}>'
                     f'<div class="eyebrow"><span class="id">{part}</span><span class="badge">{status.capitalize()}</span></div>'
                     f'<h2 id="title-{part}">{esc(question["question"])}</h2><p>{esc(question["context"])}</p>{saved}{controls}</section>')
    total = len(cards)
    progress = f"{remaining} of {total} open" if remaining else state
    toolbar = ""
    if served and has_answer_controls(item):
        toolbar = ('<div class="toolbar"><div class="actions"><button id="send" class="primary" disabled>Send answers</button>'
                   + ('<button id="suggest">Use all suggestions</button>' if any(
                       q.get("suggestion") and part_record(item, n).get("state") != "closed"
                       for n, q in enumerate(item["body"]["questions"], 1)) else '')
                   + '<button id="undo" hidden>Undo changes</button></div><output role="status"></output></div>')
    return f'<p class="progress">{progress}</p>' + toolbar + ''.join(cards)


ANSWER_SCRIPT = r"""
const send = document.querySelector('#send');
if (send) {
  const fields = [...document.querySelectorAll('textarea')];
  const sections = [...document.querySelectorAll('.decision')];
  const output = document.querySelector('output');
  const suggest = document.querySelector('#suggest');
  const undo = document.querySelector('#undo');
  const rejections = new Map();
  let history = [];
  const answerText = value => value.replace(/^[\p{White_Space}\x1c-\x1f]+|[\p{White_Space}\x1c-\x1f]+$/gu, '');
  const changed = f => {
    const {pending} = f.closest('section').dataset;
    const answer = answerText(f.value);
    return !rejections.has(f) && answer && (answer !== f.defaultValue || pending === '1');
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
      if (rejection.draft !== undefined && answerText(field.value) !== rejection.draft) {
        rejections.delete(field);
        output.textContent = '';
      }
    }
    const count = fields.filter(changed).length;
    send.disabled = !count;
    send.textContent = count ? 'Send ' + count + (count === 1 ? ' answer' : ' answers') : 'Send answers';
    if (suggest) suggest.disabled = !fields.some(suggestionFor);
    undo.hidden = !history.length;
    for (const f of fields) {
      const error = document.getElementById(f.getAttribute('aria-describedby'));
      const rejection = rejections.get(f);
      error.textContent = rejection ? rejection.message : '';
      error.hidden = !rejection;
      if (rejection) f.setAttribute('aria-invalid', 'true');
      else f.removeAttribute('aria-invalid');
      for (const b of f.closest('section').querySelectorAll('[data-choice]')) {
        b.checked = b.dataset.choice === f.value;
      }
    }
  }
  document.addEventListener('input', e => {
    if (!e.target.matches('textarea')) return;
    forget(e.target);
    refresh();
  });
  document.addEventListener('click', e => {
    const choice = e.target.closest('[data-choice]');
    if (choice || e.target === suggest) {
      const step = new Map();
      for (const f of fields) {
        const section = f.closest('section');
        const option = choice ? (section.contains(choice) && choice) : suggestionFor(f);
        if (option && option.dataset.choice !== f.value) {
          step.set(f, f.value);
          f.value = option.dataset.choice;
        }
      }
      if (step.size) history.push(step);
      refresh();
    }
    if (e.target === undo && history.length) {
      for (const [f, value] of history.pop()) f.value = value;
      refresh();
      (history.length ? undo : send.disabled ? fields[0] : send).focus();
    }
  });
  function showAnswer(section, field, record) {
    section.dataset.state = record.state;
    section.dataset.revision = record.revision;
    section.dataset.pending = record.delivery === 'pending' ? '1' : '0';
    section.querySelector('.badge').textContent = record.state === 'open' ? 'Open' : 'Answered';
    field.defaultValue = field.value = record.answer;
    forget(field);

    const saved = section.querySelector('.saved-answer');
    saved.querySelector('.record').textContent = record.answer;
    saved.querySelector('.delivery').textContent = record.delivery === 'sent' ? 'Sent' : 'Not delivered';
    const time = saved.querySelector('time');
    time.dateTime = record.answered_at;
    time.textContent = localTime(record.answered_at);
    saved.hidden = false;

    const remaining = sections.filter(s => s.dataset.state === 'open').length;
    document.querySelector('.progress').textContent = remaining ? remaining + ' of ' + sections.length + ' open' : 'Answered';
  }
  send.addEventListener('click', async () => {
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


def workers_section(ready: bool) -> str:
    if not ready:
        return ('<section id="workers"><h2>Workers</h2><p class="empty">Workers show here once firstmate_home '
                'names a Firstmate home.</p></section>')
    return ('<section id="workers"><div class="section-head"><h2>Workers</h2>'
            '<button type="button" id="refresh-workers">Refresh workers</button></div>'
            '<p class="meta" id="workers-status" role="status">Loading workers</p><div id="workers-list"></div></section>')


def list_page(data: dict[str, object], site_title: str, *, served: bool, saved_name: str = "index.html",
              workers: bool = False) -> str:
    items = newest_first(data["items"])  # type: ignore[arg-type]
    empty = "Nothing is saved yet. Ask Firstmate to send something to Quarterdeck: a link, a list or open questions."
    attention, reviewed, references = [], [], []
    primary = None
    for item in items:
        if item["kind"] != "questions":
            references.append(item)
            continue
        summary = question_summary(item)
        _, open_parts, pending = summary
        (attention if open_parts or pending else reviewed).append((item, summary))
        if open_parts and primary is None:
            primary = item
    body = ""

    def desk(group) -> str:
        cards = "".join(question_card(item, summary, served=served, primary=item is primary) for item, summary in group)
        return f'<div class="desk">{cards}</div>'

    if attention:
        body += "<h1>My desk</h1>" + desk(attention)
    elif served:
        nothing = empty if not items else "Nothing is waiting for you. Saved questions that need an answer show here."
        body += f'<h1>My desk</h1><p class="empty">{esc(nothing)}</p>'
    if served:
        body += workers_section(workers)
    if reviewed:
        level = 2 if body else 1
        body += f"<h{level}>Reviewed questions</h{level}>" + desk(reviewed)
    if references:
        level = 2 if body else 1
        body += f"<h{level}>References</h{level}>" + items_table(references)
    if not items and not served:
        body = f'<h1>Saved</h1><p class="empty">{esc(empty)}</p>'
    return page_shell(site_title, site_title, body, served=served, saved_name=saved_name, workers=served and workers)


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


def item_page(item: dict[str, object], site_title: str, *, served: bool, saved_name: str = "index.html") -> str:
    item_id = str(item["id"])
    content = question_form(item, served) if item["kind"] == "questions" else item_body_html(item)
    body = (
        f'<p class="id">{esc(item_id)}</p><h1>{esc(item["title"])}</h1>'
        f'<p class="meta">{esc(KIND_LABELS[str(item["kind"])])} · saved <time datetime="{esc(item["saved_at"])}">'
        f'{esc(saved_day(item))}</time></p>'
        f'<p class="lead">{linked_text(item["description"])}</p>{content}'
    )
    return page_shell(f"{item_id} {item['title']} · {site_title}", site_title, body,
                      served=served, saved_name=saved_name, interactive=served and has_answer_controls(item))


def site_pages(data: dict[str, object], site_title: str, saved_name: str) -> dict[str, str]:
    pages = {saved_name: list_page(data, site_title, served=False, saved_name=saved_name)}
    for item in data["items"]:  # type: ignore[union-attr]
        pages[item_page_name(str(item["id"]))] = item_page(item, site_title, served=False, saved_name=saved_name)
    return pages


# --- render -----------------------------------------------------------------------------


def default_output_dir(config: dict[str, object]) -> Path:
    configured = config.get("output_dir")
    return Path(configured).expanduser() if isinstance(configured, str) else state_dir()


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
        protected = {store.resolve(), store.with_name(store.name + ".lock").resolve()}
        if ITEM_PAGE_RE.fullmatch(output.name.lower()):
            raise ValueError("the Saved filename is reserved for item pages")
        pages = site_pages(read_store(store), page_title(args, config), output.name)
        for name in pages:
            destination = output.parent / name
            if destination in protected:
                raise ValueError("the rendered page would overwrite the item store or its lock")
            if (destination.exists() or destination.is_symlink()) and not generated_page(destination):
                raise ValueError(f"refusing to overwrite an unrelated file: {destination}")
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
                 *, workers_home: Path | None = None) -> type[BaseHTTPRequestHandler]:
    """Every page is built from the item store when it is requested."""
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
            if name != "index.html" and not ITEM_PAGE_RE.fullmatch(name):
                self.send_text(404, "Not found\n")
                return
            try:
                data = read_store(store or store_path())
            except ValueError:
                self.send_text(500, "Quarterdeck could not read its item store\n")
                return
            if name == "index.html":
                self.send_text(200, list_page(data, site_title, served=True, workers=workers_home is not None),
                               "text/html; charset=utf-8")
                return
            try:
                item = find_item(data, name.removesuffix(".html"))
            except ValueError:
                self.send_text(404, "Not found\n")
                return
            self.send_text(200, item_page(item, site_title, served=True), "text/html; charset=utf-8",
                           prevent_framing=has_answer_controls(item))

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            if self.path != base_path + "/answers":
                self.send_text(405, "Use the question page to send answers.")
                return
            host = self.headers.get("Host", "")
            https = isinstance(self.connection, ssl.SSLSocket) or self.headers.get_all("X-Forwarded-Proto") == ["https"]
            scheme = "https" if https else "http"
            if (not host or self.headers.get("Origin") != f"{scheme}://{host}"
                    or (is_loopback(str(self.server.server_address[0])) and not request_host_is_loopback(host))):
                self.send_text(403, "Open the question on Quarterdeck to send an answer.")
                return
            if self.headers.get_content_type() != "application/json":
                self.send_text(415, "Expected an answer in JSON.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 256000 or "Transfer-Encoding" in self.headers:
                    raise ValueError("Invalid answer size.")
                payload = json.loads(self.rfile.read(length))
                record = send_answer(store or store_path(), home, payload)
            except AnswerConflict as exc:
                self.send_text(409, str(exc))
            except (OSError, StoreReadError):
                self.send_text(500, "Could not save the answer.")
            except (ValueError, RecursionError):
                self.send_text(400, "Check the question and answer.")
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
        handler = make_handler(page_title(args, config), base_path, home=answer_home(config, config_path),
                               workers_home=Path(explicit_home) if isinstance(explicit_home, str) else None)
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
              "read your saved items and send answers as the owner.", file=sys.stderr, flush=True)
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
    "mark": ["how-to/read-answers.md", "reference/item-store.md"],
    "add": ["how-to/save-items.md", "reference/cli-and-config.md", "reference/item-store.md"],
    "list": ["how-to/save-items.md", "reference/cli-and-config.md"],
    "show": ["how-to/read-answers.md", "how-to/save-items.md", "reference/cli-and-config.md"],
    "remove": ["how-to/save-items.md", "reference/cli-and-config.md"],
    "render": ["how-to/serve-the-page.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "serve": ["how-to/answer-questions.md", "how-to/read-workers.md", "how-to/serve-the-page.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
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
             "questions (a JSON array of {question, context, options, suggestion, links}; "
             "links is a list of {text, url} shown above the options")


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
    add.add_argument("--kind", required=True, choices=KINDS, help="what the item is")
    add.add_argument("--title", required=True, help="a short title that says what it is about")
    add.add_argument("--description", required=True, help="one or two sentences: what it is and why it was saved")
    add.add_argument("--url", help="a resource's http(s) address")
    add.add_argument("--link-text", help="a resource's link text")
    add.add_argument("--body-file", metavar="PATH", help="the body of a list or questions; - reads standard input")

    command("list", "list saved items, newest first", "List saved items, newest first.", list_items)

    show = command("show", "show one item in full", "Show one item in full, with its parts numbered (Q7.1, Q7.2, ...).", show_item)
    show.add_argument("id", help="item ID, such as Q7")

    mark = command("mark", "mark questions answered, closed or open",
                   "Set the state of a question (Q7.3) or every question in an item (Q7).", mark_item)
    mark.add_argument("id", help="question or item ID")
    mark.add_argument("state", choices=("answered", "closed", "open"))

    remove = command("remove", "delete an item", "Delete an item. Its ID is never given to another item.", remove_item)
    remove.add_argument("id", help="item ID, such as Q7")

    render_parser = command("render", "write the page to HTML files",
                            "Write the Saved page and one page per item as HTML files.", render)
    render_parser.add_argument("--output", help="HTML file path for the Saved page; the other pages go beside it")
    render_parser.add_argument("--config", type=Path, help="optional JSON config file")
    render_parser.add_argument("--title", help="override the page title")

    serve_parser = command("serve", "serve the page over HTTP",
                           "Serve saved items and accept same-origin answers. With firstmate_home set, the page also lists that home's running workers, read on entry and on Refresh workers. Anyone who can reach the service can answer as the owner.", serve)
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
