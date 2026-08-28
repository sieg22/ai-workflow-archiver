#!/usr/bin/env python3
"""
Weavy / Figma Weave Workflow Archiver v1.5.2

Interactive workflow (Windows and macOS):
  1. Windows: double-click BACKUP_WEAVY.bat
     macOS: double-click BACKUP_WEAVY.command
  2. Enter author once for the session (optional)
  3. Enter project name
  4. The script clears the clipboard and waits for a new workflow copy
  5. In Figma Weave select the workflow and press Ctrl+C (Windows) or Cmd+C (macOS)
  6. Archiving starts automatically — no paste into the terminal and no extra Enter
  7. The HTML report opens automatically
  8. Enter the next project name, or leave it blank to exit

Default output:
PROJECT_NAME/
├── media/
│   ├── input/
│   └── output/
├── metadata/
│   ├── clipboard_original.txt
│   ├── workflow_original.json
│   ├── workflow_normalized.json
│   ├── asset_manifest.json
│   ├── download_results.json
│   └── manifest_sha256.json
└── PROJECT_NAME_report.html

No ZIP is created by default.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERSION = "1.5.2"
DEFAULT_WORKERS = 4

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"


def copy_shortcut() -> str:
    return "Cmd+C" if IS_MACOS else "Ctrl+C"


def platform_name() -> str:
    if IS_MACOS:
        return "macOS"
    if IS_WINDOWS:
        return "Windows"
    return sys.platform



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str = "") -> None:
    print(msg, flush=True)


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = str(name or "asset")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    return (name or "asset")[:max_len].rstrip(". ")


def short_text(text: str, max_len: int = 70) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= max_len else text[:max_len - 1] + "…"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def cloudinary_timestamp(url: str | None) -> datetime | None:
    m = re.search(r"/v(\d{10})/", url or "")
    if not m:
        return None
    try:
        return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
    except Exception:
        return None


def local_dt(dt: datetime | None) -> datetime | None:
    return dt.astimezone() if dt else None


def fmt_dt(dt: datetime | None, seconds: bool = True) -> str:
    dt = local_dt(dt)
    if not dt:
        return "Unknown"
    return dt.strftime("%d %b %Y · %H:%M:%S" if seconds else "%d %b %Y · %H:%M")


def utc_iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def url_basename(url: str) -> str:
    return Path(urllib.parse.urlparse(url).path).name or "asset"


def url_extension(url: str, fallback: str = ".bin") -> str:
    return Path(urllib.parse.urlparse(url).path).suffix or fallback


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def report_relative_url(report_path: Path, asset_path: Path) -> str:
    rel = os.path.relpath(asset_path, report_path.parent).replace("\\", "/")
    return urllib.parse.quote(rel, safe="/:._-()[]")


def open_report(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            webbrowser.open(path.resolve().as_uri())
    except Exception:
        try:
            webbrowser.open(path.resolve().as_uri())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def read_clipboard() -> str:
    """
    Read clipboard silently on Windows or macOS.

    Clipboard contents are captured into memory only and are never printed.
    """

    # macOS native clipboard.
    if IS_MACOS:
        try:
            cp = subprocess.run(
                ["pbpaste"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=30,
            )
            text = cp.stdout.decode("utf-8", errors="replace")
            if text.strip():
                return text
        except Exception:
            pass

    # tkinter is a useful cross-platform fallback.
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        if isinstance(text, str) and text.strip():
            return text
    except Exception:
        pass

    # Windows PowerShell fallback.
    if IS_WINDOWS:
        for exe in ("powershell.exe", "pwsh.exe"):
            try:
                kwargs = {}
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

                cp = subprocess.run(
                    [
                        exe, "-NoProfile", "-NonInteractive", "-Command",
                        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
                        "$x=Get-Clipboard -Raw;"
                        "[Console]::Out.Write($x)"
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=30,
                    **kwargs,
                )
                text = cp.stdout.decode("utf-8", errors="replace")
                if text.strip():
                    return text
            except Exception:
                pass

    raise RuntimeError(
        "Could not read the clipboard. Save the copied workflow to a .json/.txt "
        "file and run the script with that file instead."
    )


def parse_source_text(literal: str) -> dict:
    text = literal.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # Intentionally report only line/column, never the copied content.
        raise ValueError(
            f"Clipboard/file is not valid JSON (line {e.lineno}, column {e.colno})."
        ) from None

    if (
        not isinstance(data, dict)
        or not isinstance(data.get("nodes"), list)
        or not isinstance(data.get("edges"), list)
    ):
        raise ValueError("Expected top-level Weavy 'nodes' and 'edges' arrays.")

    return data



def clipboard_hash(text: str | None) -> str:
    return hashlib.sha256(
        (text or "").encode("utf-8", errors="replace")
    ).hexdigest()


def clipboard_sequence_number() -> int | None:
    """
    Return a native clipboard change counter when available.

    Windows: GetClipboardSequenceNumber
    macOS: NSPasteboard.changeCount via JXA/osascript

    A native counter lets Multi-chunk mode notice a second copy event even when
    the copied text is identical.
    """
    if IS_WINDOWS:
        try:
            import ctypes
            return int(ctypes.windll.user32.GetClipboardSequenceNumber())
        except Exception:
            return None

    if IS_MACOS:
        try:
            js = (
                'ObjC.import("AppKit");'
                'var p=$.NSPasteboard.generalPasteboard;'
                'p.changeCount.toString();'
            )
            cp = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", js],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=5,
            )
            value = cp.stdout.decode("utf-8", errors="replace").strip()
            return int(value)
        except Exception:
            return None

    return None


def read_clipboard_safe() -> str | None:
    try:
        return read_clipboard()
    except Exception:
        return None


def clear_clipboard() -> None:
    """Clear the text clipboard before waiting for a new workflow copy."""

    if IS_MACOS:
        try:
            subprocess.run(
                ["pbcopy"],
                input=b"",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=10,
            )
            return
        except Exception:
            pass

    # tkinter fallback.
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.update()
        root.destroy()
        return
    except Exception:
        pass

    if IS_WINDOWS:
        for exe in ("powershell.exe", "pwsh.exe"):
            try:
                kwargs = {}
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                subprocess.run(
                    [
                        exe, "-NoProfile", "-NonInteractive", "-Command",
                        "Set-Clipboard -Value ''"
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    **kwargs,
                )
                return
            except Exception:
                pass


def read_hotkey() -> str | None:
    """
    Read one key without requiring Enter while waiting for clipboard content.

    Windows uses msvcrt.
    macOS uses a short non-blocking cbreak read from the Terminal.
    """
    if IS_WINDOWS:
        try:
            import msvcrt

            if not msvcrt.kbhit():
                return None

            ch = msvcrt.getwch()

            if ch in ("\x00", "\xe0"):
                if msvcrt.kbhit():
                    msvcrt.getwch()
                return None

            return ch.upper()
        except Exception:
            return None

    if IS_MACOS:
        try:
            import select
            import termios
            import tty

            if not sys.stdin.isatty():
                return None

            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)

            try:
                tty.setcbreak(fd)
                ready, _, _ = select.select([sys.stdin], [], [], 0)
                if not ready:
                    return None
                ch = sys.stdin.read(1)
                return ch.upper() if ch else None
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        except Exception:
            return None

    return None


def wait_for_workflow_action() -> tuple[str, str | None, dict | None]:
    """
    Normal waiting mode.

    Returns:
      ("workflow", literal, data)
      ("rename", None, None)
      ("multi", None, None)

    While waiting, R and M are single-key hotkeys; Enter is not required.
    """
    clear_clipboard()

    log("")
    log("Clipboard cleared.")
    log("")
    log(f"Now go to Figma Weave, select the workflow nodes, and press {copy_shortcut()}.")
    log("The script will AUTOMATICALLY detect the copied workflow from the clipboard.")
    log("")
    log("If the copied workflow is large, Weavy may take longer to prepare the clipboard,")
    log("and parsing may also take longer after the content is received.")
    log("")
    log("While waiting:")
    log("  [R] Rename project")
    log("  [M] Multi-chunk mode for very large projects")
    log("")
    log("Do NOT paste anything into this window.")
    log("Do NOT press Enter after copying.")
    log("")
    log(f"Waiting for {copy_shortcut()} clipboard content...")

    last_seq = clipboard_sequence_number()
    last_hash = clipboard_hash(read_clipboard_safe())

    while True:
        hotkey = read_hotkey()

        if hotkey == "R":
            log("")
            log("Returning to project name...")
            return "rename", None, None

        if hotkey == "M":
            log("")
            log("Entering Multi-chunk mode...")
            return "multi", None, None

        time.sleep(0.20)

        seq = clipboard_sequence_number()
        current = None
        changed = False

        if seq is not None and last_seq is not None:
            if seq != last_seq:
                last_seq = seq
                changed = True
        else:
            current = read_clipboard_safe()
            current_hash = clipboard_hash(current)
            if current_hash != last_hash:
                last_hash = current_hash
                changed = True

        if not changed:
            continue

        if current is None:
            current = read_clipboard_safe()

        if not current or not current.strip():
            continue

        # Important UX: acknowledge receipt BEFORE JSON parsing.
        log("")
        log("Clipboard content received.")
        log("Validating and parsing the Weavy workflow...")
        log("Large workflows can take noticeably longer here. Please keep this window open.")

        try:
            data = parse_source_text(current)
            log(
                f"Workflow parsed: {len(data.get('nodes', []))} nodes, "
                f"{len(data.get('edges', []))} edges."
            )
            log("Starting archive...")
            return "workflow", current, data
        except Exception:
            log("Clipboard content was received, but it is not a valid Weavy workflow.")
            log(f"Still waiting for {copy_shortcut()}...")


def _edge_key(edge: dict) -> tuple:
    if edge.get("id"):
        return ("id", str(edge["id"]))
    return (
        "link",
        edge.get("source"),
        edge.get("target"),
        edge.get("sourceHandle"),
        edge.get("targetHandle"),
    )


def _generation_key(generation: dict) -> tuple:
    if generation.get("batchId"):
        return ("batch", str(generation["batchId"]))

    kind = generation.get("kind") or {}
    return (
        "fallback",
        kind.get("id"),
        kind.get("publicId"),
        kind.get("url"),
        json.dumps(
            generation.get("input") or [],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
    )


def _media_key(item: dict) -> tuple:
    return (
        item.get("id"),
        item.get("publicId"),
        item.get("url"),
        item.get("name"),
    )


def merge_duplicate_node(existing: dict, incoming: dict) -> dict:
    """
    Merge overlapping copies of the same Weavy node.

    Incoming/current state wins for scalar/current fields, while historical
    generation/result/file lists are unioned so overlap cannot lose history.
    """
    merged = copy.deepcopy(existing)

    # Latest copy wins at top level, but preserve merged data below.
    for key, value in incoming.items():
        if key != "data":
            merged[key] = copy.deepcopy(value)

    old_data = copy.deepcopy(existing.get("data") or {})
    new_data = copy.deepcopy(incoming.get("data") or {})
    data = copy.deepcopy(old_data)
    data.update(new_data)

    # Union generation history.
    old_gens = old_data.get("generations") or []
    new_gens = new_data.get("generations") or []
    generation_map = {}

    for g in list(old_gens) + list(new_gens):
        generation_map[_generation_key(g)] = copy.deepcopy(g)

    if generation_map:
        data["generations"] = list(generation_map.values())

    # Union result history if result is a list.
    old_result = old_data.get("result")
    new_result = new_data.get("result")
    if isinstance(old_result, list) or isinstance(new_result, list):
        result_map = {}
        for item in (
            (old_result if isinstance(old_result, list) else [])
            + (new_result if isinstance(new_result, list) else [])
        ):
            if isinstance(item, dict):
                key = (
                    item.get("batchId"),
                    item.get("id"),
                    item.get("publicId"),
                    item.get("url"),
                )
            else:
                key = ("value", repr(item))
            result_map[key] = copy.deepcopy(item)
        data["result"] = list(result_map.values())

    # Union import file lists.
    old_files = old_data.get("files") or []
    new_files = new_data.get("files") or []
    if old_files or new_files:
        file_map = {}
        for item in list(old_files) + list(new_files):
            if isinstance(item, dict):
                file_map[_media_key(item)] = copy.deepcopy(item)
        data["files"] = list(file_map.values())

    merged["data"] = data
    return merged


def recover_embedded_model_edges(workflow: dict) -> int:
    """
    Recover some cross-chunk connections from model data.kind.inputs.

    This helps when an explicit edge was omitted because its two endpoints were
    never copied together. It cannot recover every possible utility-node edge,
    so overlapping chunks are still recommended.
    """
    nodes = workflow.get("nodes") or []
    edges = workflow.get("edges") or []
    node_ids = {n.get("id") for n in nodes}
    existing_keys = {_edge_key(e) for e in edges}
    recovered = 0

    for target in nodes:
        target_id = target.get("id")
        kind = (target.get("data") or {}).get("kind") or {}
        inputs = kind.get("inputs") or []

        if not isinstance(inputs, list):
            continue

        for pair in inputs:
            if not isinstance(pair, list) or not pair:
                continue

            spec = pair[0] if isinstance(pair[0], dict) else {}
            connection = (
                pair[1]
                if len(pair) > 1 and isinstance(pair[1], dict)
                else None
            )

            if not connection:
                continue

            source_id = connection.get("nodeId")
            output_id = connection.get("outputId")
            input_id = spec.get("id")

            if (
                not source_id
                or not target_id
                or source_id not in node_ids
                or target_id not in node_ids
            ):
                continue

            source_handle = (
                f"{source_id}-output-{output_id}"
                if output_id else None
            )
            target_handle = (
                f"{target_id}-input-{input_id}"
                if input_id else None
            )

            candidate = {
                "id": (
                    "recovered-"
                    + hashlib.sha1(
                        (
                            f"{source_id}|{target_id}|"
                            f"{output_id}|{input_id}"
                        ).encode("utf-8")
                    ).hexdigest()[:12]
                ),
                "type": "custom",
                "source": source_id,
                "target": target_id,
                "sourceHandle": source_handle,
                "targetHandle": target_handle,
                "data": {"recoveredFromEmbeddedInput": True},
            }

            key = _edge_key(candidate)

            # Also compare source/target/handles against explicit edges.
            link_key = (
                "link",
                source_id,
                target_id,
                source_handle,
                target_handle,
            )
            explicit_link_keys = {
                (
                    "link",
                    e.get("source"),
                    e.get("target"),
                    e.get("sourceHandle"),
                    e.get("targetHandle"),
                )
                for e in edges
            }

            if key in existing_keys or link_key in explicit_link_keys:
                continue

            edges.append(candidate)
            existing_keys.add(key)
            recovered += 1

    workflow["edges"] = edges
    return recovered


def merge_workflow_chunk(
    merged: dict | None,
    chunk: dict,
) -> tuple[dict, dict]:
    """
    Merge one clipboard chunk into the accumulated workflow.
    """
    if merged is None:
        merged = {
            key: copy.deepcopy(value)
            for key, value in chunk.items()
            if key not in {"nodes", "edges"}
        }
        merged["nodes"] = []
        merged["edges"] = []

    node_map = {
        n.get("id"): n
        for n in merged.get("nodes", [])
        if n.get("id")
    }
    edge_map = {
        _edge_key(e): e
        for e in merged.get("edges", [])
    }

    new_nodes = duplicate_nodes = 0
    new_edges = duplicate_edges = 0

    for node in chunk.get("nodes", []):
        nid = node.get("id")
        if not nid:
            continue

        if nid in node_map:
            duplicate_nodes += 1
            node_map[nid] = merge_duplicate_node(
                node_map[nid], node
            )
        else:
            new_nodes += 1
            node_map[nid] = copy.deepcopy(node)

    for edge in chunk.get("edges", []):
        key = _edge_key(edge)
        if key in edge_map:
            duplicate_edges += 1
        else:
            new_edges += 1
            edge_map[key] = copy.deepcopy(edge)

    merged["nodes"] = list(node_map.values())
    merged["edges"] = list(edge_map.values())

    stats = {
        "new_nodes": new_nodes,
        "duplicate_nodes": duplicate_nodes,
        "new_edges": new_edges,
        "duplicate_edges": duplicate_edges,
        "total_nodes": len(merged["nodes"]),
        "total_edges": len(merged["edges"]),
    }

    return merged, stats


def collect_multi_chunk_workflow(
    project_name: str,
) -> tuple[str, str | None, dict | None]:
    """
    Collect multiple overlapping Weavy clipboard selections.

    Returns:
      ("workflow", literal_json, merged_data)
      ("rename", None, None)
      ("cancel", None, None)

    Hotkeys:
      D = done and archive
      R = rename project (collected chunks are discarded)
      X = cancel project
    """
    clear_clipboard()

    log("")
    log("MULTI-CHUNK MODE")
    log("================")
    log("")
    log("Copy the large project in several overlapping selections.")
    log("Overlapping chunks are RECOMMENDED so cross-chunk connections are preserved.")
    log("Repeated nodes/edges/generations are automatically detected and merged.")
    log("")
    log(f"After each {copy_shortcut()}, the chunk is detected automatically — no Enter is required.")
    log("")
    log("Hotkeys:")
    log("  [D] Done — finish collection and archive")
    log("  [R] Rename project — return to project-name step")
    log("  [X] Cancel this project")
    log("")
    log("Waiting for chunk 1...")

    merged = None
    chunk_count = 0
    last_seq = clipboard_sequence_number()
    last_hash = clipboard_hash(read_clipboard_safe())

    while True:
        hotkey = read_hotkey()

        if hotkey == "R":
            log("")
            log("Returning to project name. Collected chunks will be discarded.")
            return "rename", None, None

        if hotkey == "X":
            log("")
            log("Multi-chunk project cancelled.")
            return "cancel", None, None

        if hotkey == "D":
            if not merged or not merged.get("nodes"):
                log("No valid chunks have been collected yet.")
                continue

            recovered = recover_embedded_model_edges(merged)

            log("")
            log(
                f"Multi-chunk collection complete: "
                f"{len(merged.get('nodes', []))} nodes, "
                f"{len(merged.get('edges', []))} edges."
            )
            if recovered:
                log(
                    f"Recovered {recovered} additional connection(s) "
                    f"from embedded model input references."
                )
            log("Starting archive...")

            literal = json.dumps(
                merged,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return "workflow", literal, merged

        time.sleep(0.20)

        seq = clipboard_sequence_number()
        current = None
        changed = False

        if seq is not None and last_seq is not None:
            if seq != last_seq:
                last_seq = seq
                changed = True
        else:
            current = read_clipboard_safe()
            current_hash = clipboard_hash(current)
            if current_hash != last_hash:
                last_hash = current_hash
                changed = True

        if not changed:
            continue

        if current is None:
            current = read_clipboard_safe()

        if not current or not current.strip():
            continue

        next_chunk = chunk_count + 1

        log("")
        log(f"Chunk {next_chunk} clipboard content received.")
        log("Parsing chunk...")
        log("Large chunks may take longer to parse.")

        try:
            chunk = parse_source_text(current)
        except Exception:
            log("Clipboard content is not a valid Weavy workflow chunk.")
            log(f"Still waiting for chunk {next_chunk}...")
            continue

        merged, stats = merge_workflow_chunk(merged, chunk)
        chunk_count += 1

        log(
            f"Chunk {chunk_count} accepted: "
            f"{len(chunk.get('nodes', []))} nodes, "
            f"{len(chunk.get('edges', []))} edges."
        )
        log(
            f"  +{stats['new_nodes']} new nodes, "
            f"{stats['duplicate_nodes']} duplicate nodes merged."
        )
        log(
            f"  +{stats['new_edges']} new edges, "
            f"{stats['duplicate_edges']} duplicate edges ignored."
        )
        log(
            f"  Accumulated total: "
            f"{stats['total_nodes']} nodes, "
            f"{stats['total_edges']} edges."
        )
        log("")
        log(
            f"Waiting for chunk {chunk_count + 1}... "
            f"[D] Done  [R] Rename  [X] Cancel"
        )



def detect_project_name(data: dict) -> str | None:
    """
    Best-effort only. Tested clipboard payloads often contain only nodes/edges,
    so interactive mode still asks for a project name.
    """
    names = (
        "projectName", "project_name", "workflowName", "workflow_name",
        "pageName", "page_name", "title", "name"
    )

    for key in names:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for container in ("metadata", "project", "workflow", "page"):
        obj = data.get(container)
        if isinstance(obj, dict):
            for key in names:
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    return None


# ---------------------------------------------------------------------------
# Archive model
# ---------------------------------------------------------------------------

class Archive:
    def __init__(
        self,
        data: dict,
        root: Path,
        project_name: str,
        author: str = "",
    ):
        self.data = data
        self.root = root
        self.project_name = project_name
        self.author = author.strip()

        self.nodes = data.get("nodes", [])
        self.edges = data.get("edges", [])
        self.node_by_id = {n["id"]: n for n in self.nodes if n.get("id")}

        # Requested lean folder structure.
        self.media_dir = root / "media"
        self.input_dir = self.media_dir / "input"
        self.output_dir = self.media_dir / "output"
        self.metadata_dir = root / "metadata"

        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

        self.branch_by_node: dict[str, int] = {}
        self.branch_names: dict[int, str] = {}
        self.components: list[list[str]] = []

        self.import_by_url: dict[str, dict] = {}
        self.generation_occurrences: list[dict] = []
        self.generations: list[dict] = []
        self.asset_by_url: dict[str, dict] = {}
        self.assets: list[dict] = []
        self.models: list[dict] = []

    # ----------------------------- nodes ----------------------------------

    @staticmethod
    def import_file(node: dict) -> dict | None:
        files = node.get("data", {}).get("files") or []
        return files[0] if files else None

    @staticmethod
    def model_id(node: dict) -> str:
        model = node.get("data", {}).get("model")
        return str(model.get("name") or "") if isinstance(model, dict) else ""

    def model_name(self, node: dict) -> str:
        d = node.get("data", {})
        mid = self.model_id(node)

        if mid == "fp_magnific_upscale":
            return "Magnific Upscale"
        if "seedance-2.0" in mid:
            return "Seedance 2.0 Reference"
        if "seedance-2.5" in mid:
            return "Seedance 2.5 Reference"

        menu = d.get("menu")
        if isinstance(menu, dict) and menu.get("displayName"):
            return str(menu["displayName"])

        return str(d.get("name") or node.get("originalName") or mid or "Model")

    def node_name(self, node: dict) -> str:
        typ = node.get("type")
        d = node.get("data", {})

        if typ == "import":
            f = self.import_file(node)
            return str((f or {}).get("name") or d.get("name") or "Imported media")
        if node.get("isModel"):
            return self.model_name(node)
        if typ == "promptV3":
            return "Prompt — " + short_text(d.get("prompt") or "", 46)

        return str(d.get("name") or node.get("originalName") or typ or "Node")

    # ------------------------------ graph ---------------------------------

    def build_branches(self) -> None:
        adj: dict[str, set[str]] = defaultdict(set)

        for edge in self.edges:
            s, t = edge.get("source"), edge.get("target")
            if s and t:
                adj[s].add(t)
                adj[t].add(s)

        seen: set[str] = set()

        for nid in self.node_by_id:
            if nid in seen:
                continue

            stack = [nid]
            seen.add(nid)
            comp: list[str] = []

            while stack:
                x = stack.pop()
                comp.append(x)
                for y in adj[x]:
                    if y not in seen:
                        seen.add(y)
                        stack.append(y)

            self.components.append(comp)

        # Give every connected component a useful generic label.
        # Duplicate labels receive a numeric suffix so the report remains clear.
        label_counts: dict[str, int] = defaultdict(int)

        for index, comp in enumerate(self.components, 1):
            base_name = self.infer_branch_name(comp)
            label_counts[base_name] += 1
            occurrence = label_counts[base_name]

            self.branch_names[index] = (
                base_name
                if occurrence == 1
                else f"{base_name} ({occurrence})"
            )

            for nid in comp:
                self.branch_by_node[nid] = index

    def infer_branch_name(self, comp: list[str]) -> str:
        """
        Infer a human-readable task label using generic workflow metadata only.

        This intentionally does not contain project-specific prompt keywords.
        Task labels are presentation metadata; they do not affect generation
        deduplication, downloading, Canvas reconstruction, or integrity checks.
        """
        models: list[str] = []
        media_names: list[str] = []
        output_types: set[str] = set()
        has_upscale = False

        for nid in comp:
            node = self.node_by_id[nid]

            f = self.import_file(node)
            if f:
                name = str(f.get("name") or "").strip()
                if name:
                    media_names.append(name)

            if node.get("isModel"):
                model_name = self.model_name(node)
                if model_name:
                    models.append(model_name)

                model_text = (
                    model_name + " " + self.model_id(node)
                ).lower()

                if "upscale" in model_text or "magnific" in model_text:
                    has_upscale = True

                for generation in node.get("data", {}).get("generations") or []:
                    kind = generation.get("kind") or {}
                    output_type = str(kind.get("type") or "").lower()
                    if output_type:
                        output_types.add(output_type)

        # Prefer an input/reference filename as the task context because it
        # distinguishes parallel branches without attempting semantic AI
        # interpretation of private prompt text.
        context = ""

        if media_names:
            stems = []
            for name in media_names:
                stem = Path(name).stem.strip() or name.strip()
                if stem and stem not in stems:
                    stems.append(stem)

            if stems:
                context = short_text(stems[0], 34)
                if len(stems) > 1:
                    context += f" +{len(stems) - 1}"

        if not context and models:
            context = short_text(models[0], 42)

        if has_upscale:
            action = "Image upscale"
        elif "video" in output_types:
            action = "Video generation"
        elif "image" in output_types:
            action = "Image generation"
        elif "audio" in output_types:
            action = "Audio generation"
        elif models:
            action = short_text(models[0], 46) + " workflow"
            context = (
                context
                if context and context != short_text(models[0], 42)
                else ""
            )
        elif media_names:
            action = "Media workflow"
        else:
            action = "Workflow branch"

        return f"{action} — {context}" if context else action


    # ------------------------------ media ---------------------------------

    def index_imports(self) -> None:
        for node in self.nodes:
            f = self.import_file(node)
            if f and f.get("url"):
                self.import_by_url[str(f["url"])] = {"node": node, "file": f}

    # --------------------------- generations ------------------------------

    def collect_generations(self) -> None:
        occurrences: list[dict] = []

        for node in self.nodes:
            if not node.get("isModel"):
                continue

            for index, g in enumerate(node.get("data", {}).get("generations") or []):
                raw_input = g.get("input") or []

                if isinstance(raw_input, list):
                    try:
                        inp = dict(raw_input)
                    except Exception:
                        inp = {}
                elif isinstance(raw_input, dict):
                    inp = dict(raw_input)
                else:
                    inp = {}

                occurrences.append({
                    "node_id": node.get("id"),
                    "node_name": self.model_name(node),
                    "model_id": self.model_id(node),
                    "node_created_at": parse_iso(node.get("createdAt")),
                    "index": index,
                    "batch_id": g.get("batchId"),
                    "input": inp,
                    "output": g.get("kind") or {},
                })

        self.generation_occurrences = occurrences

        dedup: dict[tuple, dict] = {}

        for occ in occurrences:
            out = occ["output"]

            if occ.get("batch_id"):
                key = ("batch", str(occ["batch_id"]))
            else:
                fingerprint = json.dumps(
                    {"input": occ["input"], "output": out},
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )
                key = ("fallback", hashlib.sha1(fingerprint.encode("utf-8")).hexdigest())

            if key not in dedup:
                dedup[key] = {
                    "batch_id": occ.get("batch_id"),
                    "input": occ["input"],
                    "output": out,
                    "model_name": occ["node_name"],
                    "model_id": occ["model_id"],
                    "occurrences": [],
                }

            dedup[key]["occurrences"].append({
                "node_id": occ["node_id"],
                "node_name": occ["node_name"],
                "node_created_at": occ["node_created_at"],
            })

        generations = list(dedup.values())

        for g in generations:
            g["occurrences"].sort(
                key=lambda o: o["node_created_at"]
                or datetime.max.replace(tzinfo=timezone.utc)
            )

            origin = g["occurrences"][0]
            g["origin_node_id"] = origin["node_id"]
            g["branch_index"] = self.branch_by_node.get(origin["node_id"])
            g["branch_name"] = self.branch_names.get(
                g["branch_index"], "Workflow branch"
            )
            g["time"] = (
                cloudinary_timestamp(g["output"].get("url"))
                or origin["node_created_at"]
            )

        generations.sort(
            key=lambda g: g["time"]
            or datetime.max.replace(tzinfo=timezone.utc)
        )

        by_origin: dict[str, list[dict]] = defaultdict(list)

        for g in generations:
            by_origin[str(g["origin_node_id"])].append(g)

        for arr in by_origin.values():
            for run_no, g in enumerate(arr, 1):
                g["run_no"] = run_no

        self.generations = generations

    # -------------------------- asset manifest ----------------------------

    def build_assets(self) -> None:
        asset_by_url: dict[str, dict] = {}
        used_paths: set[str] = set()

        def allocate(rel: str, identity: str) -> str:
            rel = rel.replace("\\", "/")

            if rel.lower() not in used_paths:
                used_paths.add(rel.lower())
                return rel

            stem, ext = os.path.splitext(rel)
            suffix = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
            rel = f"{stem} [{suffix}]{ext}"
            used_paths.add(rel.lower())
            return rel

        # Imported/reference media.
        for url, rec in self.import_by_url.items():
            f = rec["file"]
            original_name = str(f.get("name") or url_basename(url))
            archive_name = sanitize_filename(original_name, 130)

            if not Path(archive_name).suffix:
                archive_name += url_extension(url)

            local_path = allocate(f"media/input/{archive_name}", url)

            asset_by_url[url] = {
                "url": url,
                "kind": "input",
                "original_name": original_name,
                "archive_name": Path(local_path).name,
                "local_path": local_path,
                "type": f.get("type"),
                "width": f.get("width"),
                "height": f.get("height"),
                "duration": f.get("duration"),
                "fps": f.get("fps"),
                "publicId": f.get("publicId"),
                "id": f.get("id"),
                "thumbnailUrl": f.get("thumbnailUrl"),
            }

        # Generated outputs.
        for g in self.generations:
            out = g["output"]
            url = str(out.get("url") or "")

            if not url:
                continue

            typ = out.get("type")
            fallback_ext = (
                ".mp4" if typ == "video"
                else ".png" if typ == "image"
                else ".bin"
            )
            ext = url_extension(url, fallback_ext)
            dt = local_dt(g["time"])
            stamp = dt.strftime("%Y-%m-%d_%H-%M-%S") if dt else "unknown-time"
            batch = str(g.get("batch_id") or out.get("id") or "run")[:8]

            archive_name = sanitize_filename(
                f'{g["branch_name"]} - {g["model_name"]} - '
                f'Run {g["run_no"]} - {stamp} - {batch}',
                155,
            ) + ext

            date_folder = (
                dt.strftime("%Y-%m-%d")
                if dt else "unknown-date"
            )
            category_folder = self.generation_category(g)

            local_path = allocate(
                f"media/output/{date_folder}/{category_folder}/{archive_name}",
                url,
            )

            if url not in asset_by_url:
                asset_by_url[url] = {
                    "url": url,
                    "kind": "output",
                    "original_name": url_basename(url),
                    "archive_name": Path(local_path).name,
                    "local_path": local_path,
                    "type": typ,
                    "width": out.get("width"),
                    "height": out.get("height"),
                    "duration": out.get("duration"),
                    "fps": out.get("fps"),
                    "publicId": out.get("publicId"),
                    "id": out.get("id"),
                    "thumbnailUrl": out.get("thumbnailUrl"),
                    "batchId": g.get("batch_id"),
                }

        # Generation inputs missing from import nodes.
        for g in self.generations:
            for field, value in g["input"].items():
                if not isinstance(value, str):
                    continue

                if not (
                    field.endswith("_urls")
                    or field in {"image", "video", "audio"}
                ):
                    continue

                for url in [
                    x.strip()
                    for x in value.splitlines()
                    if x.strip().startswith(("http://", "https://"))
                ]:
                    if url in asset_by_url:
                        continue

                    archive_name = sanitize_filename(url_basename(url), 130)
                    local_path = allocate(f"media/input/{archive_name}", url)

                    asset_by_url[url] = {
                        "url": url,
                        "kind": "input",
                        "original_name": archive_name,
                        "archive_name": Path(local_path).name,
                        "local_path": local_path,
                        "type": None,
                    }

        self.asset_by_url = asset_by_url
        self.assets = list(asset_by_url.values())

    # ------------------------------ models --------------------------------

    def build_models(self) -> None:
        grouped: dict[tuple, dict] = {}

        for node in [n for n in self.nodes if n.get("isModel")]:
            d = node.get("data", {})
            model = d.get("model") if isinstance(d.get("model"), dict) else {}

            key = (
                self.model_name(node),
                self.model_id(node),
                str(model.get("service") or ""),
                str(model.get("version") or ""),
            )

            if key not in grouped:
                grouped[key] = {
                    "display_name": key[0],
                    "model_id": key[1],
                    "service": key[2],
                    "version": key[3],
                    "node_ids": [],
                }

            grouped[key]["node_ids"].append(node["id"])

        for m in grouped.values():
            node_set = set(m["node_ids"])
            m["unique_runs"] = sum(
                1 for g in self.generations
                if g["origin_node_id"] in node_set
            )

        self.models = list(grouped.values())

    # ------------------------------ local ---------------------------------

    def asset_abs(self, asset: dict) -> Path:
        return self.root / asset["local_path"].replace("/", os.sep)

    def inspect_local(self) -> dict:
        result = {
            "total": len(self.assets),
            "local": 0,
            "missing": 0,
            "input_total": 0,
            "input_local": 0,
            "output_total": 0,
            "output_local": 0,
        }

        for asset in self.assets:
            p = self.asset_abs(asset)
            ok = p.exists() and p.stat().st_size > 0

            result["local"] += int(ok)
            result["missing"] += int(not ok)

            if asset["kind"] == "output":
                result["output_total"] += 1
                result["output_local"] += int(ok)
            else:
                result["input_total"] += 1
                result["input_local"] += int(ok)

        result["complete"] = result["missing"] == 0
        return result

    # ----------------------------- download --------------------------------

    def download_one(self, asset: dict) -> tuple[str, dict]:
        dst = self.asset_abs(asset)
        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists() and dst.stat().st_size > 0:
            return "skipped", {
                "url": asset["url"],
                "local_path": asset["local_path"],
                "bytes": dst.stat().st_size,
                "sha256": sha256_file(dst),
            }

        tmp = dst.with_suffix(dst.suffix + ".part")

        try:
            req = urllib.request.Request(
                asset["url"],
                headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
            )

            with urllib.request.urlopen(req, timeout=120) as response, tmp.open("wb") as out:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    out.write(block)

            if tmp.stat().st_size <= 0:
                raise IOError("Downloaded file is empty.")

            tmp.replace(dst)

            return "downloaded", {
                "url": asset["url"],
                "local_path": asset["local_path"],
                "bytes": dst.stat().st_size,
                "sha256": sha256_file(dst),
            }

        except Exception as e:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

            return "failed", {
                "url": asset["url"],
                "local_path": asset["local_path"],
                "error": str(e),
            }

    def download_all(self, workers: int) -> None:
        log(f"Downloading {len(self.assets)} media files...")

        results = {"downloaded": [], "skipped": [], "failed": []}

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(self.download_one, asset): asset
                for asset in self.assets
            }

            done = 0

            for future in as_completed(futures):
                done += 1
                asset = futures[future]

                try:
                    status, record = future.result()
                except Exception as e:
                    status, record = "failed", {
                        "url": asset["url"],
                        "local_path": asset["local_path"],
                        "error": str(e),
                    }

                results[status].append(record)
                tag = {
                    "downloaded": "OK",
                    "skipped": "SKIP",
                    "failed": "FAIL",
                }[status]

                # Only short progress lines. Never print clipboard/prompt content.
                log(
                    f"  [{done:>3}/{len(self.assets)}] "
                    f"{tag:<4} {asset['archive_name']}"
                )

        (self.metadata_dir / "download_results.json").write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------ data ----------------------------------

    def normalized(self) -> dict:
        nodes = []

        for node in self.nodes:
            pos = node.get("position") or {}

            nodes.append({
                "id": node.get("id"),
                "friendly_name": self.node_name(node),
                "type": node.get("type"),
                "is_model": bool(node.get("isModel")),
                "created_at": node.get("createdAt"),
                "branch_name": self.branch_names.get(
                    self.branch_by_node.get(node.get("id"))
                ),
                "position": {"x": pos.get("x"), "y": pos.get("y")},
                "model_name": self.model_name(node) if node.get("isModel") else None,
                "model_id": self.model_id(node) if node.get("isModel") else None,
                "current_prompt": (
                    node.get("data", {}).get("prompt")
                    if node.get("type") == "promptV3"
                    else None
                ),
                "current_params": node.get("data", {}).get("params"),
                "import_file": self.import_file(node),
            })

        edges = []

        for edge in self.edges:
            s, t = edge.get("source"), edge.get("target")

            edges.append({
                "source_id": s,
                "source_name": (
                    self.node_name(self.node_by_id[s])
                    if s in self.node_by_id else None
                ),
                "source_handle": edge.get("sourceHandle"),
                "target_id": t,
                "target_name": (
                    self.node_name(self.node_by_id[t])
                    if t in self.node_by_id else None
                ),
                "target_handle": edge.get("targetHandle"),
            })

        generations = []

        for g in self.generations:
            out = g["output"]
            url = str(out.get("url") or "")

            generations.append({
                "batch_id": g.get("batch_id"),
                "branch_name": g.get("branch_name"),
                "model_name": g.get("model_name"),
                "model_id": g.get("model_id"),
                "origin_node_id": g.get("origin_node_id"),
                "run_no": g.get("run_no"),
                "approx_generation_time": utc_iso(g.get("time")),
                "input": g.get("input"),
                "output": out,
                "local_output_path": self.asset_by_url.get(url, {}).get("local_path"),
                "history_occurrences": [
                    {
                        "node_id": o["node_id"],
                        "node_name": o["node_name"],
                        "node_created_at": utc_iso(o["node_created_at"]),
                    }
                    for o in g["occurrences"]
                ],
            })

        return {
            "format": "weavy-workflow-archive-v1",
            "version": VERSION,
            "project_name": self.project_name,
            "author": self.author,
            "summary": {
                "nodes": len(self.nodes),
                "model_nodes": sum(1 for n in self.nodes if n.get("isModel")),
                "prompt_nodes": sum(1 for n in self.nodes if n.get("type") == "promptV3"),
                "input_nodes": sum(1 for n in self.nodes if n.get("type") == "import"),
                "generation_occurrences": len(self.generation_occurrences),
                "unique_generations": len(self.generations),
                "unique_assets": len(self.assets),
            },
            "branches": [
                {"name": self.branch_names[i], "node_ids": comp}
                for i, comp in enumerate(self.components, 1)
            ],
            "nodes": nodes,
            "edges": edges,
            "generations": generations,
            "assets": self.assets,
            "models": self.models,
        }

    # --------------------------- optional CSV -----------------------------

    def write_full_data(self) -> None:
        extra = self.metadata_dir / "extra_data"
        extra.mkdir(parents=True, exist_ok=True)

        norm = self.normalized()

        def write_csv(name: str, rows: list[dict], fields: list[str]) -> None:
            with (extra / name).open(
                "w", encoding="utf-8-sig", newline=""
            ) as f:
                w = csv.DictWriter(
                    f, fieldnames=fields, extrasaction="ignore"
                )
                w.writeheader()
                for row in rows:
                    w.writerow(row)

        write_csv(
            "nodes.csv",
            norm["nodes"],
            [
                "friendly_name", "id", "type", "created_at",
                "branch_name", "model_name", "model_id"
            ],
        )

        gen_rows = []

        for g in norm["generations"]:
            inp = g["input"] or {}
            out = g["output"] or {}

            gen_rows.append({
                "branch_name": g["branch_name"],
                "model_name": g["model_name"],
                "run_no": g["run_no"],
                "batch_id": g["batch_id"],
                "approx_generation_time": g["approx_generation_time"],
                "seed": inp.get("seed"),
                "duration": inp.get("duration"),
                "resolution": inp.get("resolution"),
                "aspect_ratio": inp.get("aspect_ratio"),
                "prompt": inp.get("prompt"),
                "output_url": out.get("url"),
                "local_output_path": g.get("local_output_path"),
            })

        write_csv(
            "generations.csv",
            gen_rows,
            [
                "branch_name", "model_name", "run_no", "batch_id",
                "approx_generation_time", "seed", "duration",
                "resolution", "aspect_ratio", "prompt",
                "output_url", "local_output_path"
            ],
        )

    # ------------------------------ report --------------------------------

    def hybrid_media(
        self,
        report_path: Path,
        url: str,
        media_type: str,
        thumb: str | None = None,
    ) -> str:
        asset = self.asset_by_url.get(url)
        local_url = ""

        if asset:
            local_url = report_relative_url(
                report_path, self.asset_abs(asset)
            )

        if media_type == "video":
            poster = f' poster="{esc(thumb)}"' if thumb else ""
            return (
                f'<video controls preload="metadata"{poster} '
                f'data-remote="{esc(url)}">'
                f'<source src="{esc(local_url)}" type="video/mp4">'
                f'</video>'
            )

        return (
            f'<img src="{esc(local_url)}" '
            f'data-remote="{esc(url)}" alt="">'
        )

    def generation_category(self, g: dict) -> str:
        model = str(g.get("model_name") or "").lower()
        out_type = str((g.get("output") or {}).get("type") or "").lower()

        if "upscale" in model or "magnific" in model:
            return "upscale"
        if out_type == "video":
            return "video"
        if out_type == "image":
            return "image"
        if out_type == "audio":
            return "audio"
        return "other"

    def report_html(self, report_path: Path) -> str:
        health = self.inspect_local()

        # ------------------------------------------------------------------
        # Header / archive status
        # ------------------------------------------------------------------
        status = (
            "FULLY SELF-CONTAINED"
            if health["complete"]
            else "REMOTE FALLBACK REQUIRED"
        )
        status_class = "good" if health["complete"] else "warn"
        status_detail = (
            f'{health["local"]}/{health["total"]} local'
            if health["complete"]
            else f'{health["local"]}/{health["total"]} local · '
                 f'{health["missing"]} missing'
        )
        coverage = (
            round(100 * health["local"] / health["total"], 1)
            if health["total"] else 100
        )

        author_line = esc(self.author) if self.author else ""

        # ------------------------------------------------------------------
        # Overview branches
        # ------------------------------------------------------------------
        branch_cards = []

        for idx, comp in enumerate(self.components, 1):
            runs = sum(
                1 for g in self.generations
                if g["branch_index"] == idx
            )
            models = sum(
                1 for nid in comp
                if self.node_by_id[nid].get("isModel")
            )

            # Pure prompt/import/utility components are useful on Canvas,
            # Inputs and Prompts, but are not workflow tasks for Overview.
            if runs == 0 and models == 0:
                continue

            branch_cards.append(
                f'<div class="branch">'
                f'<b>{esc(self.branch_names[idx])}</b>'
                f'<span>{runs} run(s) · {models} model node(s)</span>'
                f'</div>'
            )

        # ------------------------------------------------------------------
        # Outputs
        # ------------------------------------------------------------------
        output_cards = []

        for g in self.generations:
            out = g["output"]
            url = str(out.get("url") or "")
            typ = str(out.get("type") or "")

            preview = (
                self.hybrid_media(
                    report_path, url, typ, out.get("thumbnailUrl")
                )
                if url else '<div class="placeholder">OUTPUT</div>'
            )

            inputs = []

            for field, value in g["input"].items():
                if not isinstance(value, str):
                    continue

                if not (
                    field.endswith("_urls")
                    or field in {"image", "video", "audio"}
                ):
                    continue

                for u in [
                    x.strip()
                    for x in value.splitlines()
                    if x.strip().startswith(("http://", "https://"))
                ]:
                    a = self.asset_by_url.get(u)
                    name = (
                        (a or {}).get("archive_name")
                        or (a or {}).get("original_name")
                        or url_basename(u)
                    )
                    inputs.append(esc(name))

            params = []

            for key in (
                "model", "resolution", "duration", "aspect_ratio",
                "scale_factor", "optimized_for", "engine",
                "creativity", "hdr", "resemblance", "fractality",
                "bitrate_mode", "cfg_scale", "shot_type",
                "generate_audio", "enhance_prompt", "seed"
            ):
                if key in g["input"] and g["input"][key] not in ("", None):
                    params.append(
                        f'<span><b>{esc(key.replace("_"," ").title())}</b> '
                        f'{esc(g["input"][key])}</span>'
                    )

            local_path = self.asset_by_url.get(
                url, {}
            ).get("local_path", "")

            meta = []

            if out.get("width") and out.get("height"):
                meta.append(f'{out["width"]}×{out["height"]}')

            if out.get("duration") is not None:
                try:
                    meta.append(f'{float(out["duration"]):.2f}s')
                except Exception:
                    pass

            if out.get("fps") is not None:
                meta.append(f'{out["fps"]} fps')

            sort_ts = int(g["time"].timestamp()) if g.get("time") else 0

            output_cards.append(f"""
            <article class="output-card" data-time="{sort_ts}">
              <div class="preview">{preview}</div>
              <div class="info">
                <div class="head">
                  <div>
                    <small>{esc(g["branch_name"])}</small>
                    <h3>{esc(g["model_name"])} — Run {g["run_no"]}</h3>
                  </div>
                  <time>{esc(fmt_dt(g["time"]))}</time>
                </div>

                <div class="label">Inputs</div>
                <div class="chips">
                  {''.join(f'<span>{x}</span>' for x in inputs) or '<em>None</em>'}
                </div>

                <div class="label">Parameters</div>
                <div class="chips">{''.join(params)}</div>

                <details>
                  <summary>Prompt</summary>
                  <pre>{esc(g["input"].get("prompt") or "")}</pre>
                </details>

                <div class="footer">
                  <code>{esc(local_path)}</code>
                  <span>{esc(" · ".join(meta))}</span>
                </div>
              </div>
            </article>
            """)

        # ------------------------------------------------------------------
        # Canvas - increased spacing + smaller cards.
        # ------------------------------------------------------------------
        xs = [
            float((n.get("position") or {}).get("x") or 0)
            for n in self.nodes
        ]
        ys = [
            float((n.get("position") or {}).get("y") or 0)
            for n in self.nodes
        ]

        minx, maxx = min(xs or [0]), max(xs or [0])
        miny, maxy = min(ys or [0]), max(ys or [0])

        # V3: more spacing than v2.
        canvas_scale_x = 0.60
        canvas_scale_y = 0.56
        pad = 120
        card_w, card_h = 166, 70

        canvas_w = int(
            (maxx - minx) * canvas_scale_x
            + card_w + pad * 2
        )
        canvas_h = int(
            (maxy - miny) * canvas_scale_y
            + card_h + pad * 2
        )

        positions = {}

        for node in self.nodes:
            pos = node.get("position") or {}
            positions[node["id"]] = (
                int(
                    (float(pos.get("x") or 0) - minx)
                    * canvas_scale_x + pad
                ),
                int(
                    (float(pos.get("y") or 0) - miny)
                    * canvas_scale_y + pad
                ),
            )

        run_count = defaultdict(int)

        for g in self.generations:
            run_count[g["origin_node_id"]] += 1

        canvas_nodes = []

        for node in self.nodes:
            x, y = positions[node["id"]]

            cls = (
                "model" if node.get("isModel")
                else "prompt" if node.get("type") == "promptV3"
                else "media" if node.get("type") == "import"
                else "tool"
            )

            f = self.import_file(node)
            thumb = None

            if f and f.get("type") in {"image", "video"}:
                thumb = (
                    f.get("thumbnailUrl")
                    or (
                        f.get("url")
                        if f.get("type") == "image"
                        else None
                    )
                )

            thumb_html = (
                f'<img src="{esc(thumb)}" alt="">'
                if thumb
                else ("¶" if cls == "prompt" else "◆")
            )

            sub = str(node.get("type") or "")

            if node.get("isModel"):
                count = run_count[node["id"]]
                sub = f"{count} run" + ("" if count == 1 else "s")

            canvas_nodes.append(f"""
            <div class="node {cls}" style="left:{x}px;top:{y}px">
              <div class="thumb">{thumb_html}</div>
              <div class="node-text">
                <b>{esc(self.node_name(node))}</b>
                <span>{esc(sub)}</span>
                <small>{esc(fmt_dt(parse_iso(node.get("createdAt")), False))}</small>
              </div>
            </div>
            """)

        edge_paths = []

        for edge in self.edges:
            s, t = edge.get("source"), edge.get("target")

            if s not in positions or t not in positions:
                continue

            sx, sy = positions[s]
            tx, ty = positions[t]

            x1, y1 = sx + card_w, sy + card_h / 2
            x2, y2 = tx, ty + card_h / 2
            mid = (x1 + x2) / 2

            edge_paths.append(
                f'<path d="M{x1},{y1} '
                f'C{mid},{y1} {mid},{y2} {x2},{y2}"/>'
            )

        # ------------------------------------------------------------------
        # Timeline - outputs only, category colors.
        # ------------------------------------------------------------------
        timeline_rows = []

        for g in self.generations:
            dt = local_dt(g["time"])
            category = self.generation_category(g)

            category_label = {
                "upscale": "Upscale",
                "video": "Video generation",
                "image": "Image generation",
                "audio": "Audio generation",
                "other": "Generation",
            }[category]

            timeline_rows.append(f"""
            <div class="timeline-row cat-{category}">
              <time>
                {esc(dt.strftime("%d %b · %H:%M:%S") if dt else "Unknown")}
              </time>
              <i></i>
              <div>
                <small>{esc(category_label)} · {esc(g["branch_name"])}</small>
                <b>{esc(g["model_name"])} — Run {g["run_no"]}</b>
              </div>
            </div>
            """)

        # ------------------------------------------------------------------
        # Inputs
        # ------------------------------------------------------------------
        input_cards = []

        for node in [
            n for n in self.nodes
            if n.get("type") == "import"
        ]:
            f = self.import_file(node)

            if not f:
                continue

            url = str(f.get("url") or "")
            asset = self.asset_by_url.get(url, {})
            p = self.asset_abs(asset) if asset else None
            local_ok = bool(
                p and p.exists() and p.stat().st_size > 0
            )

            badge = "LOCAL ✓" if local_ok else "REMOTE FALLBACK"
            badge_cls = "ok" if local_ok else "warn"

            typ = str(f.get("type") or "file")

            if typ == "image" and url:
                preview = self.hybrid_media(
                    report_path, url, "image"
                )
            elif typ == "video" and f.get("thumbnailUrl"):
                preview = (
                    f'<img src="{esc(f.get("thumbnailUrl"))}" alt="">'
                )
            else:
                preview = (
                    f'<div class="placeholder">'
                    f'{esc(typ.upper())}</div>'
                )

            meta = [typ]

            if f.get("width") and f.get("height"):
                meta.append(f'{f["width"]}×{f["height"]}')

            if f.get("duration") is not None:
                try:
                    meta.append(f'{float(f["duration"]):.2f}s')
                except Exception:
                    pass

            input_cards.append(f"""
            <article class="input-card">
              <div class="input-preview">{preview}</div>
              <div class="input-body">
                <div class="input-title">
                  <b>{esc(f.get("name") or "Media")}</b>
                  <span class="{badge_cls}">{badge}</span>
                </div>
                <small>{esc(" · ".join(meta))}</small>
                <code>{esc(asset.get("local_path") or "")}</code>
              </div>
            </article>
            """)

        # ------------------------------------------------------------------
        # Prompts
        # ------------------------------------------------------------------
        prompt_cards = []

        for g in self.generations:
            prompt = str(g["input"].get("prompt") or "")

            if not prompt:
                continue

            prompt_cards.append(f"""
            <article class="prompt-card">
              <div>
                <small>{esc(g["branch_name"])}</small>
                <h3>{esc(g["model_name"])} — Run {g["run_no"]}</h3>
              </div>
              <time>{esc(fmt_dt(g["time"]))}</time>
              <pre>{esc(prompt)}</pre>
            </article>
            """)

        # ------------------------------------------------------------------
        # Models
        # ------------------------------------------------------------------
        model_cards = []

        for m in self.models:
            model_cards.append(f"""
            <article class="model-card">
              <small>MODEL</small>
              <h3>{esc(m["display_name"])}</h3>
              <code>{esc(m["model_id"])}</code>
              <div>
                <span>{m["unique_runs"]} real run(s)</span>
                <span>{esc(m["service"] or "provider unknown")}</span>
              </div>
            </article>
            """)

        created = [
            parse_iso(n.get("createdAt"))
            for n in self.nodes
            if n.get("createdAt")
        ]
        created = [x for x in created if x]

        first_created = min(created) if created else None
        last_created = max(created) if created else None

        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(self.project_name)}</title>
<style>
:root {{
  --bg:#0d1015;--panel:#151922;--panel2:#1b202a;
  --text:#f7f8fa;--muted:#9ba7b7;--line:#303846;
  --blue:#4da3ff;--green:#45c98e;--red:#ff6d6d;
  --yellow:#f3bc52;--purple:#aa8cff;--cyan:#56cfe1;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
a{{color:#a9c8ff;text-decoration:none}}
header{{padding:30px 42px 22px;background:#141922;border-bottom:1px solid var(--line)}}
.header-main{{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:24px;align-items:end}}
h1{{font-size:32px;letter-spacing:-.03em;margin:0 0 5px}}
.byline{{font-size:10px;color:#9ba7b7;min-height:15px}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(115px,1fr));gap:8px;margin-top:16px;max-width:850px}}
.metric{{border:1px solid var(--line);border-radius:11px;padding:11px;background:rgba(255,255,255,.03)}}
.metric b{{font-size:21px;display:block}}.metric span{{font-size:9px;color:var(--muted)}}
.range{{margin-top:10px;color:#909dac;font-size:10px}}
.archive-status{{border:1px solid var(--line);border-radius:12px;padding:12px 13px;background:var(--panel)}}
.archive-status.good{{border-color:#33755d}}.archive-status.warn{{border-color:#916034}}
.archive-status small{{font-size:8px;color:#8794a4;text-transform:uppercase;letter-spacing:.1em}}
.archive-status b{{display:block;font-size:12px;margin:4px 0 3px}}
.archive-status.good b{{color:#70dfae}}.archive-status.warn b{{color:#ffc16a}}
.archive-status span{{font-size:9px;color:#a9b4c1}}
.bar{{height:5px;background:#252c36;border-radius:99px;overflow:hidden;margin-top:8px}}.bar>div{{height:100%;width:{coverage}%;background:#78b8ff}}
nav{{position:sticky;top:0;z-index:30;display:flex;gap:7px;overflow:auto;padding:9px 42px;background:rgba(13,16,21,.95);border-bottom:1px solid var(--line)}}
nav button{{border:1px solid var(--line);background:var(--panel);color:#aab4c1;border-radius:999px;padding:7px 11px;cursor:pointer;white-space:nowrap}}
nav button.active{{background:#fff;color:#111}}
section{{display:none;padding:25px 42px 48px}}section.active{{display:block}}
h2{{margin:0 0 13px;font-size:22px}}h3{{margin:3px 0 5px}}
.branch-grid{{display:grid;grid-template-columns:repeat(2,minmax(280px,1fr));gap:8px}}
.branch{{display:flex;justify-content:space-between;gap:10px;border:1px solid var(--line);border-radius:10px;padding:11px;background:var(--panel)}}
.branch b{{font-size:11px}}.branch span{{color:var(--muted);font-size:9px}}
.output-toolbar{{display:flex;gap:6px;margin:-2px 0 12px}}
.output-toolbar button{{border:1px solid var(--line);background:var(--panel);color:#b8c2ce;padding:6px 9px;border-radius:7px;cursor:pointer;font-size:8px}}
.output-toolbar button.active{{background:#fff;color:#111;border-color:#fff}}
.output-list{{display:grid;gap:12px}}
.output-card{{display:grid;grid-template-columns:minmax(290px,40%) 1fr;border:1px solid var(--line);border-radius:13px;overflow:hidden;background:var(--panel)}}
.preview{{min-height:255px;background:#080a0d;display:flex;align-items:center;justify-content:center}}
.preview video,.preview img{{width:100%;height:100%;max-height:470px;object-fit:contain}}
.placeholder{{color:#697484;font-weight:800;letter-spacing:.12em}}
.info{{padding:15px 17px}}.head{{display:flex;justify-content:space-between;gap:14px}}
.head small,.prompt-card small,.model-card small{{color:#8491a2;text-transform:uppercase;letter-spacing:.08em;font-size:8px}}
time{{font-size:9px;color:#bec8d5}}
.label{{font-size:7px;color:#8390a1;font-weight:800;text-transform:uppercase;letter-spacing:.1em;margin:11px 0 4px}}
.chips{{display:flex;gap:5px;flex-wrap:wrap}}
.chips span{{font-size:8px;border:1px solid #35404d;background:#202630;border-radius:6px;padding:4px 6px}}
details{{margin-top:10px;border-top:1px solid var(--line);padding-top:8px}}
summary{{cursor:pointer;font-size:10px;font-weight:700}}
pre{{white-space:pre-wrap;max-height:390px;overflow:auto;background:#10141a;padding:9px;border-radius:8px;font-size:8px;color:#d1d9e3;line-height:1.45}}
.footer{{display:flex;justify-content:space-between;gap:10px;border-top:1px solid var(--line);margin-top:9px;padding-top:8px}}
code{{color:#a8c9ff;font-size:7px;word-break:break-all}}.footer span{{font-size:8px;color:#8d99a8;white-space:nowrap}}
.canvas-tools{{display:flex;gap:5px;margin-bottom:8px}}
.canvas-tools button{{border:1px solid var(--line);background:var(--panel);color:#d4dce6;padding:5px 8px;border-radius:6px;cursor:pointer}}
.canvas-wrap{{height:77vh;overflow:auto;border:1px solid var(--line);border-radius:11px;background:#10141a}}
.canvas{{position:relative;width:{canvas_w}px;height:{canvas_h}px;background-image:radial-gradient(#28313b 1px,transparent 1px);background-size:20px 20px;transform-origin:top left}}
.canvas svg{{position:absolute;inset:0;width:100%;height:100%}}
.canvas path{{fill:none;stroke:#596474;stroke-width:1.4;opacity:.62}}
.node{{position:absolute;width:{card_w}px;height:{card_h}px;background:var(--panel2);border:1px solid #3a4452;border-radius:8px;display:flex;overflow:hidden;z-index:2}}
.node.media{{border-top:3px solid var(--blue)}}.node.prompt{{border-top:3px solid var(--green)}}.node.model{{border-top:3px solid var(--red)}}
.thumb{{width:47px;min-width:47px;background:#0b0e13;display:flex;align-items:center;justify-content:center;color:#a8b3c0;font-size:18px;overflow:hidden}}
.thumb img{{width:100%;height:100%;object-fit:cover}}
.node-text{{padding:6px;overflow:hidden}}
.node-text b{{display:block;font-size:8px;line-height:1.2;max-height:20px;overflow:hidden}}
.node-text span,.node-text small{{display:block;font-size:6px;color:#9aa6b5;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.timeline{{max-width:900px}}
.timeline-row{{display:grid;grid-template-columns:125px 18px 1fr;gap:10px;min-height:54px}}
.timeline-row time{{font-size:9px;color:#bec8d5;padding-top:4px}}
.timeline-row i{{position:relative}}
.timeline-row i:before{{content:"";position:absolute;left:6px;top:7px;width:8px;height:8px;border-radius:50%}}
.timeline-row i:after{{content:"";position:absolute;left:9px;top:16px;bottom:-1px;width:1px;background:#333b46}}
.timeline-row.cat-upscale i:before{{background:var(--purple)}}
.timeline-row.cat-video i:before{{background:var(--red)}}
.timeline-row.cat-image i:before{{background:var(--blue)}}
.timeline-row.cat-audio i:before{{background:var(--green)}}
.timeline-row.cat-other i:before{{background:var(--yellow)}}
.timeline-row small{{display:block;color:#7f8b9b;font-size:7px;text-transform:uppercase;letter-spacing:.06em}}
.timeline-row b{{font-size:10px}}
.legend{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}}
.legend span{{font-size:8px;color:#9aa6b5}}
.legend i{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}}
.legend .u{{background:var(--purple)}}.legend .v{{background:var(--red)}}.legend .im{{background:var(--blue)}}.legend .a{{background:var(--green)}}.legend .o{{background:var(--yellow)}}
.input-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(205px,1fr));gap:9px}}
.input-card{{border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--panel)}}
.input-preview{{height:130px;background:#080a0d;display:flex;align-items:center;justify-content:center}}
.input-preview img{{width:100%;height:100%;object-fit:cover}}
.input-body{{padding:8px 9px;display:grid;gap:4px}}
.input-title{{display:flex;justify-content:space-between;gap:6px}}
.input-title b{{font-size:9px;word-break:break-word}}
.input-title span{{font-size:6px;padding:3px 5px;border-radius:999px;white-space:nowrap}}
.input-title .ok{{background:#183c2d;color:#72deb0}}
.input-title .warn{{background:#422f1b;color:#ffc169}}
.input-body small{{font-size:7px;color:var(--muted)}}
.prompt-list,.model-list{{display:grid;gap:9px}}
.prompt-card,.model-card{{border:1px solid var(--line);border-radius:10px;background:var(--panel);padding:12px}}
.prompt-card{{display:grid;grid-template-columns:1fr auto;gap:5px 13px}}
.prompt-card pre{{grid-column:1/-1;margin:3px 0 0}}
.model-card div{{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}}
.model-card span{{font-size:7px;border:1px solid #35404d;border-radius:999px;padding:4px 6px;color:#bbc6d4}}
@media(max-width:900px){{
  header,section{{padding-left:18px;padding-right:18px}}
  nav{{padding-left:18px;padding-right:18px}}
  .header-main{{grid-template-columns:1fr}}
  .metrics{{grid-template-columns:repeat(2,1fr)}}
  .branch-grid{{grid-template-columns:1fr}}
  .output-card{{grid-template-columns:1fr}}
  .preview{{min-height:205px}}
  .head,.footer{{display:block}}
}}
</style>
</head>
<body>
<header>
  <div class="header-main">
    <div>
      <h1>{esc(self.project_name)}</h1>
      <div class="byline">{author_line}</div>

      <div class="metrics">
        <div class="metric"><b>{len(self.nodes)}</b><span>Nodes</span></div>
        <div class="metric"><b>{sum(1 for n in self.nodes if n.get("isModel"))}</b><span>Model nodes</span></div>
        <div class="metric"><b>{len(self.generations)}</b><span>Real generations</span></div>
        <div class="metric"><b>{health["total"]}</b><span>Media files</span></div>
      </div>

      <div class="range">
        {esc(fmt_dt(first_created))} → {esc(fmt_dt(last_created))}
      </div>
    </div>

    <div class="archive-status {status_class}">
      <small>Archive status</small>
      <b>{status}</b>
      <span>{status_detail}</span>
      <div class="bar"><div></div></div>
    </div>
  </div>
</header>

<nav>
  <button class="tab active" data-tab="overview">Overview</button>
  <button class="tab" data-tab="outputs">Outputs</button>
  <button class="tab" data-tab="canvas">Canvas</button>
  <button class="tab" data-tab="timeline">Timeline</button>
  <button class="tab" data-tab="inputs">Inputs</button>
  <button class="tab" data-tab="prompts">Prompts</button>
  <button class="tab" data-tab="models">Models</button>
</nav>

<section id="overview" class="active">
  <h2>Workflow tasks</h2>
  <div class="branch-grid">{''.join(branch_cards)}</div>
</section>

<section id="outputs">
  <h2>Outputs</h2>
  <div class="output-toolbar">
    <button id="sortNewest" class="active" onclick="sortOutputs('desc')">Newest first</button>
    <button id="sortOldest" onclick="sortOutputs('asc')">Oldest first</button>
  </div>
  <div id="outputList" class="output-list">{''.join(reversed(output_cards))}</div>
</section>

<section id="canvas">
  <h2>Canvas</h2>
  <div class="canvas-tools">
    <button onclick="zoomCanvas(1.15)">Zoom in</button>
    <button onclick="zoomCanvas(.87)">Zoom out</button>
    <button onclick="resetCanvas()">Reset</button>
  </div>
  <div class="canvas-wrap">
    <div id="wfCanvas" class="canvas">
      <svg viewBox="0 0 {canvas_w} {canvas_h}" preserveAspectRatio="none">
        {''.join(edge_paths)}
      </svg>
      {''.join(canvas_nodes)}
    </div>
  </div>
</section>

<section id="timeline">
  <h2>Output timeline</h2>
  <div class="legend">
    <span><i class="u"></i>Upscale</span>
    <span><i class="v"></i>Video generation</span>
    <span><i class="im"></i>Image generation</span>
    <span><i class="a"></i>Audio generation</span>
    <span><i class="o"></i>Other</span>
  </div>
  <div class="timeline">{''.join(timeline_rows)}</div>
</section>

<section id="inputs">
  <h2>Inputs</h2>
  <div class="input-grid">{''.join(input_cards)}</div>
</section>

<section id="prompts">
  <h2>Prompt history</h2>
  <div class="prompt-list">{''.join(prompt_cards)}</div>
</section>

<section id="models">
  <h2>Models</h2>
  <div class="model-list">{''.join(model_cards)}</div>
</section>

<script>
document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('section').forEach(x=>x.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(btn.dataset.tab).classList.add('active');
}}));

// Local-first -> remote fallback.
document.querySelectorAll('img[data-remote]').forEach(img=>{{
  img.addEventListener('error',()=>{{
    if(!img.dataset.usedRemote && img.dataset.remote){{
      img.dataset.usedRemote='1';
      img.src=img.dataset.remote;
    }}
  }});
}});

document.querySelectorAll('video[data-remote]').forEach(video=>{{
  const fallback=()=>{{
    if(video.dataset.usedRemote || !video.dataset.remote) return;
    video.dataset.usedRemote='1';
    const src=video.querySelector('source');
    if(src){{
      src.src=video.dataset.remote;
      video.load();
    }}
  }};
  video.addEventListener('error',fallback);
  const src=video.querySelector('source');
  if(src) src.addEventListener('error',fallback);
}});


function sortOutputs(direction){{
  const list=document.getElementById('outputList');
  const cards=Array.from(list.querySelectorAll('.output-card'));
  cards.sort((a,b)=>{{
    const ta=Number(a.dataset.time||0);
    const tb=Number(b.dataset.time||0);
    return direction==='asc' ? ta-tb : tb-ta;
  }});
  cards.forEach(card=>list.appendChild(card));
  document.getElementById('sortNewest').classList.toggle('active',direction==='desc');
  document.getElementById('sortOldest').classList.toggle('active',direction==='asc');
}}

let z=1;
function applyZoom(){{
  document.getElementById('wfCanvas').style.transform='scale('+z+')';
}}
function zoomCanvas(f){{
  z=Math.max(.45,Math.min(2.2,z*f));
  applyZoom();
}}
function resetCanvas(){{
  z=1;
  applyZoom();
}}
</script>
</body>
</html>
"""

    # ------------------------------ hashes --------------------------------

    def write_hashes(self) -> None:
        items = []

        for p in sorted(self.root.rglob("*")):
            if (
                p.is_file()
                and p != self.metadata_dir / "manifest_sha256.json"
            ):
                try:
                    items.append({
                        "path": str(
                            p.relative_to(self.root)
                        ).replace("\\", "/"),
                        "bytes": p.stat().st_size,
                        "sha256": sha256_file(p),
                    })
                except Exception:
                    pass

        (self.metadata_dir / "manifest_sha256.json").write_text(
            json.dumps(items, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ------------------------------- zip ----------------------------------

    def zip_archive(self) -> Path:
        zip_path = self.root.parent / f"{self.root.name}.zip"

        if zip_path.exists():
            zip_path.unlink()

        with zipfile.ZipFile(
            zip_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as z:
            for p in self.root.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(self.root.parent))

        return zip_path



def exact_project_root(parent: Path, project_name: str) -> Path:
    return parent / sanitize_filename(project_name, 90)


def load_existing_asset_manifest(root: Path) -> list[dict]:
    path = root / "metadata" / "asset_manifest.json"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass

    return []


def managed_media_path(root: Path, relative_path: str | None) -> Path | None:
    """
    Convert a manifest media path into a safe path under this archive root.

    Only files below media/ are eligible for migration/removal.
    """
    if not relative_path:
        return None

    rel = Path(str(relative_path).replace("\\\\", "/"))

    if rel.is_absolute() or ".." in rel.parts:
        return None

    if not rel.parts or rel.parts[0].lower() != "media":
        return None

    return root.joinpath(*rel.parts)


def remove_empty_media_dirs(root: Path) -> None:
    media_root = root / "media"

    if not media_root.exists():
        return

    dirs = sorted(
        [p for p in media_root.rglob("*") if p.is_dir()],
        key=lambda p: len(p.parts),
        reverse=True,
    )

    for directory in dirs:
        try:
            directory.rmdir()
        except OSError:
            pass


def reconcile_existing_media(
    root: Path,
    old_assets: list[dict],
    new_assets: list[dict],
) -> dict:
    """
    Reuse media from an existing archive update.

    Matching is primarily by remote URL. If a filename/path changed because
    report task labels changed, the existing local file is moved to the new
    deterministic location instead of being downloaded again.

    Only files listed in the previous asset_manifest.json are considered
    managed and eligible for cleanup. Untracked user files are never removed.
    """
    old_by_url = {
        str(a.get("url")): a
        for a in old_assets
        if a.get("url")
    }
    new_by_url = {
        str(a.get("url")): a
        for a in new_assets
        if a.get("url")
    }

    migrated = 0
    reused = 0
    removed = 0

    # Reuse/migrate assets still referenced by the new workflow.
    for url, new_asset in new_by_url.items():
        old_asset = old_by_url.get(url)
        if not old_asset:
            continue

        old_path = managed_media_path(root, old_asset.get("local_path"))
        new_path = managed_media_path(root, new_asset.get("local_path"))

        if not old_path or not new_path or not old_path.exists():
            continue

        if old_path == new_path:
            reused += 1
            continue

        new_path.parent.mkdir(parents=True, exist_ok=True)

        if new_path.exists() and new_path.stat().st_size > 0:
            # The desired destination is already valid; remove only the old
            # manifest-managed duplicate.
            try:
                old_path.unlink()
                removed += 1
            except OSError:
                pass
            reused += 1
            continue

        try:
            shutil.move(str(old_path), str(new_path))
            migrated += 1
        except OSError:
            pass

    # Remove assets that were managed by the old archive but are no longer
    # referenced by the new workflow.
    new_urls = set(new_by_url)

    for url, old_asset in old_by_url.items():
        if url in new_urls:
            continue

        old_path = managed_media_path(root, old_asset.get("local_path"))

        if old_path and old_path.exists() and old_path.is_file():
            try:
                old_path.unlink()
                removed += 1
            except OSError:
                pass

    remove_empty_media_dirs(root)

    return {
        "reused": reused,
        "migrated": migrated,
        "removed": removed,
    }


def choose_existing_archive_action(
    parent: Path,
    project_name: str,
) -> tuple[str, str]:
    """
    Resolve an existing same-name archive after workflow capture.

    Returns:
      ("new", project_name)
      ("update", project_name)
      ("cancel", project_name)

    Rename loops here and does not require copying the workflow again.
    """
    while True:
        exact = exact_project_root(parent, project_name)

        if not exact.exists():
            return "new", project_name

        next_copy = choose_root(parent, project_name)

        log("")
        log("An archive with this project name already exists:")
        log(f"  {exact}")
        log("")
        log("Choose how to continue:")
        log("  [U] Update existing archive")
        log(f"  [N] Create a new copy: {next_copy.name}")
        log("  [R] Rename project")
        log("  [C] Cancel")
        log("")
        choice = input("Choice [U/N/R/C]: ").strip().upper()

        if choice == "U":
            return "update", project_name

        if choice == "N":
            return "new", project_name

        if choice == "R":
            new_name = input(
                f"Project name [{project_name}]: "
            ).strip()
            if new_name:
                project_name = new_name
            continue

        if choice == "C":
            return "cancel", project_name

        log("Please enter U, N, R, or C.")



# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def choose_root(parent: Path, project_name: str) -> Path:
    clean = sanitize_filename(project_name, 90)
    root = parent / clean

    if not root.exists():
        return root

    n = 2

    while True:
        candidate = parent / f"{clean}_{n}"
        if not candidate.exists():
            return candidate
        n += 1


def archive_project(
    literal: str,
    data: dict,
    project_name: str,
    parent: Path,
    author: str,
    download: bool,
    workers: int,
    full_data: bool,
    make_zip: bool,
    auto_open: bool,
    root_mode: str = "new",
) -> tuple[Path, Path | None]:

    if root_mode == "update":
        root = exact_project_root(parent, project_name)
        old_assets = load_existing_asset_manifest(root)
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = choose_root(parent, project_name)
        old_assets = []
        root.mkdir(parents=True)

    arc = Archive(
        data=data,
        root=root,
        project_name=project_name,
        author=author,
    )

    # Keep all source/metadata files together.
    (arc.metadata_dir / "clipboard_original.txt").write_text(
        literal, encoding="utf-8"
    )
    (arc.metadata_dir / "workflow_original.json").write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    log(f"\nAnalyzing {project_name}...")

    arc.build_branches()
    arc.index_imports()
    arc.collect_generations()
    arc.build_assets()
    arc.build_models()

    if root_mode == "update":
        update_stats = reconcile_existing_media(
            root,
            old_assets,
            arc.assets,
        )
        log(
            "  Existing archive update: "
            f"{update_stats['reused']} media reused · "
            f"{update_stats['migrated']} moved to new paths · "
            f"{update_stats['removed']} obsolete managed files removed"
        )

    normalized = arc.normalized()

    (arc.metadata_dir / "workflow_normalized.json").write_text(
        json.dumps(
            normalized,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (arc.metadata_dir / "asset_manifest.json").write_text(
        json.dumps(
            arc.assets,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    log(
        f"  {len(arc.nodes)} nodes · "
        f"{len(arc.generations)} real generations · "
        f"{len(arc.assets)} media files"
    )

    if download:
        arc.download_all(workers)
    else:
        (arc.metadata_dir / "download_results.json").write_text(
            json.dumps(
                {
                    "downloaded": [],
                    "skipped": [],
                    "failed": [],
                    "note": "Media download skipped.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log("Media download skipped.")

    if full_data:
        arc.write_full_data()

    report_name = (
        sanitize_filename(project_name, 90)
        + "_report.html"
    )
    report_path = root / report_name

    report_path.write_text(
        arc.report_html(report_path),
        encoding="utf-8",
    )

    arc.write_hashes()

    zip_path = arc.zip_archive() if make_zip else None

    health = arc.inspect_local()

    log(
        f"  Local media: {health['local']}/{health['total']} · "
        + (
            "FULLY SELF-CONTAINED"
            if health["complete"]
            else "REMOTE FALLBACK REQUIRED"
        )
    )
    log(f"  Saved: {root}")

    if zip_path:
        log(f"  ZIP: {zip_path}")

    if auto_open:
        open_report(report_path)

    return root, zip_path


# ---------------------------------------------------------------------------
# Interactive continuous mode
# ---------------------------------------------------------------------------

def interactive_loop(args) -> int:
    parent = Path(args.out).resolve()
    parent.mkdir(parents=True, exist_ok=True)

    log(f"\nWEAVY LOCAL ARCHIVER v{VERSION} — {platform_name()}")
    log("========================================\n")

    # Author applies to all projects in this session.
    author = args.author

    if author is None:
        author = input("Author (optional): ").strip()

    log("\nLeave project name blank to exit.\n")

    while True:
        project_name = input("Project name: ").strip()

        if not project_name:
            break

        try:
            while True:
                action, literal, data = wait_for_workflow_action()

                if action == "rename":
                    new_name = input(
                        f"Project name [{project_name}]: "
                    ).strip()
                    if new_name:
                        project_name = new_name
                    continue

                if action == "multi":
                    multi_action, literal, data = (
                        collect_multi_chunk_workflow(project_name)
                    )

                    if multi_action == "rename":
                        new_name = input(
                            f"Project name [{project_name}]: "
                        ).strip()
                        if new_name:
                            project_name = new_name
                        continue

                    if multi_action == "cancel":
                        literal = None
                        data = None
                        break

                    action = multi_action

                if action == "workflow" and literal and data:
                    root_mode, project_name = choose_existing_archive_action(
                        parent,
                        project_name,
                    )

                    if root_mode == "cancel":
                        break

                    archive_project(
                        literal=literal,
                        data=data,
                        project_name=project_name,
                        parent=parent,
                        author=author or "",
                        download=not args.no_download,
                        workers=args.workers,
                        full_data=args.full_data,
                        make_zip=args.zip,
                        auto_open=not args.no_open,
                        root_mode=root_mode,
                    )
                    break

            # A cancelled project simply returns to the next Project name.
        except Exception as e:
            log(f"ERROR: {e}")
            log("No copied workflow content was printed.")

        log("\nReady for the next project.\n")

    log("Done.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Archive Weavy/Figma Weave workflow clipboard data."
    )

    parser.add_argument(
        "input",
        nargs="?",
        help="Optional workflow JSON/TXT file.",
    )
    parser.add_argument(
        "--clipboard",
        action="store_true",
        help="One-off clipboard mode.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Continuous multi-project clipboard mode.",
    )
    parser.add_argument(
        "--name",
        help="Project name for one-off mode.",
    )
    parser.add_argument(
        "--author",
        default=None,
        help="Author shown near the HTML title.",
    )
    parser.add_argument(
        "--out",
        default=".",
        help="Parent backup directory.",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Metadata/report only.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Concurrent media download workers.",
    )
    parser.add_argument(
        "--full-data",
        action="store_true",
        help="Also export CSV audit tables under metadata/extra_data/.",
    )
    parser.add_argument(
        "--zip",
        action="store_true",
        help="Also create a ZIP. Off by default.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not automatically open the HTML report.",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update the exact same-name archive folder instead of creating _2.",
    )

    args = parser.parse_args()
    args.workers = max(1, args.workers)

    if args.interactive:
        return interactive_loop(args)

    try:
        if args.input and not args.clipboard:
            path = Path(args.input).resolve()
            literal = path.read_text(encoding="utf-8-sig")
            data = parse_source_text(literal)
            fallback_name = path.stem
        else:
            literal = read_clipboard()
            data = parse_source_text(literal)
            fallback_name = None

        detected = detect_project_name(data)
        project_name = args.name or detected or fallback_name

        if not project_name:
            project_name = input("Project name: ").strip()

        if not project_name:
            raise ValueError("Project name is required.")

        author = args.author or ""

        parent = Path(args.out).resolve()
        parent.mkdir(parents=True, exist_ok=True)

        archive_project(
            literal=literal,
            data=data,
            project_name=project_name,
            parent=parent,
            author=author,
            download=not args.no_download,
            workers=args.workers,
            full_data=args.full_data,
            make_zip=args.zip,
            auto_open=not args.no_open,
            root_mode="update" if args.update_existing else "new",
        )

        return 0

    except KeyboardInterrupt:
        log("\nCancelled.")
        return 130

    except Exception as e:
        log(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
