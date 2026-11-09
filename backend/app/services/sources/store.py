"""Bounded, immutable text snapshots. No project code is executed."""

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

from app.core.config import get_settings
from app.integrations.git import SourceUnavailable as SourceUnavailable
from app.integrations.git import git_command as git_command
from app.integrations.git import tree_bytes as tree_bytes
from app.integrations.svn import svn_command
from app.schemas.sources import relative_path
from app.utils.redaction import redact

IGNORED_DIRS = {
    ".git",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".local",
    ".uv-cache",
    ".pnpm-store",
    "dist",
    "build",
    "logs",
    "coverage",
    ".aws",
    ".ssh",
    ".tools",
    ".python",
    ".codex",
    ".agents",
    ".workbuddy",
    ".vscode",
    ".idea",
    ".docker",
}
SECRET_NAME = re.compile(
    r"(^\.env($|\.(?!example$|template$|sample$)))|"
    r"^(?:\.git-credentials|\.netrc|\.npmrc|id_rsa|id_ed25519|credentials?|secrets?)$|"
    r"(?:credentials?|secrets?)[._-].*\.(?:json|toml|ya?ml|ini|txt|conf|env|db|sqlite)$|"
    r"^(?:credentials?|secrets?)\.(?:json|toml|ya?ml|ini|txt|conf|env|db|sqlite)$|"
    r"\.(pem|key|p12|pfx|keystore)$",
    re.I,
)
CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".js",
    ".tsx",
    ".jsx",
    ".vue",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".kt",
    ".swift",
    ".rb",
    ".php",
}


def gap(items, reason):
    if reason not in items and len(items) < 100:
        items.append(reason)


def allowed_path(name, directory=False):
    try:
        relative_path(name)
    except ValueError:
        return False
    path = PurePosixPath(name)
    if any(p in IGNORED_DIRS for p in path.parts):
        return False
    if directory:
        return True
    if any(p in {"credentials", "secrets", ".credentials", ".secrets"} for p in path.parts[:-1]):
        if path.suffix.lower() not in CODE_SUFFIXES:
            return False
    return not SECRET_NAME.search(path.name)


def contained(root, path):
    root, path = root.resolve(), path.resolve()
    if not path.is_relative_to(root):
        raise SourceUnavailable("path_outside_root")
    return path


def local_root(relative):
    base = get_settings().source_input_dir.resolve()
    relative = relative_path(relative)
    path = base
    for part in PurePosixPath(relative).parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise SourceUnavailable("local_symlink_rejected")
    path = contained(base, path)
    if not path.is_dir():
        raise SourceUnavailable("local_directory_unavailable")
    return path


def source_text(data, name):
    if b"\x00" in data:
        return None
    try:
        value = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    # Preserve line numbers when masking multi-line keys.
    value = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        lambda m: "[REDACTED PRIVATE KEY]" + "\n" * m[0].count("\n"),
        value,
        flags=re.S,
    )
    if PurePosixPath(name).suffix.lower() in CODE_SUFFIXES:
        # Mask literal credentials while preserving expressions such as token = os.getenv(...).
        term = r"(?:password|passphrase|api[_-]?key|private[_-]?key|secret|token)"
        identifier = r"(?:[A-Za-z_]\w*?)?" + term
        prefix = rf"""(?i)((?<![\w'"])(?:"{identifier}"|'{identifier}'|{identifier})\s*[:=]\s*)"""

        def mask(match):
            if not match[3]:
                return match[0]
            return match[1] + match[2] + "[REDACTED]" + "\n" * match[3].count("\n") + match[2]

        value = re.sub(prefix + r"([\"']{3})(.*?)\2", mask, value, flags=re.S)
        value = re.sub(prefix + r"([\"'])((?:\\.|(?!\2)[^\\\r\n])*)\2", mask, value)
        value = re.sub(r"(?i)(bearer\s+)[\w.\-/+=]+", r"\1[REDACTED]", value)
        value = re.sub(r"\b(?:sk-|gh[pousr]_|github_pat_)[A-Za-z0-9_-]+", "[REDACTED]", value)
    else:
        value = redact(value)
    return re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[REDACTED]@", value)


def append_file(destination, name, data, manifest, gaps, budget):
    settings = get_settings()
    if not allowed_path(name):
        return
    if len(data) > settings.source_max_file_bytes:
        gap(gaps, "file_size_limit")
        return
    value = source_text(data, name)
    if value is None:
        gap(gaps, "binary_or_non_utf8_files_excluded")
        return
    encoded = value.encode()
    if budget[0] + len(encoded) > settings.source_max_snapshot_bytes:
        gap(gaps, "snapshot_size_limit")
        return
    if len(manifest) >= settings.source_max_files:
        gap(gaps, "file_count_limit")
        return
    target = destination.joinpath(*PurePosixPath(name).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encoded)
    manifest.append(
        {
            "path": name,
            "bytes": len(encoded),
            "sha256": hashlib.sha256(data).hexdigest(),
            "stored_sha256": hashlib.sha256(encoded).hexdigest(),
            "lines": len(value.splitlines()),
        }
    )
    budget[0] += len(encoded)


def read_local(path, root):
    settings = get_settings()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise SourceUnavailable("non_regular_file_excluded")
        if before.st_size > settings.source_max_file_bytes:
            raise SourceUnavailable("file_size_limit")
        actual = Path(f"/proc/self/fd/{stream.fileno()}") if sys.platform == "linux" else path
        contained(root, actual)
        data = stream.read(settings.source_max_file_bytes + 1)
        after = os.fstat(stream.fileno())
    signature = (after.st_ino, after.st_size, after.st_mtime_ns)
    if (before.st_ino, before.st_size, before.st_mtime_ns) != signature:
        raise SourceUnavailable("local_directory_changed")
    return data, signature


def capture_local(relative, destination, deadline):
    root, manifest, gaps, budget, signatures = local_root(relative), [], [], [0], {}
    visited = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        if time.monotonic() >= deadline:
            raise SourceUnavailable("sync_timeout")
        dirs[:] = sorted(
            d
            for d in dirs
            if allowed_path((Path(parent) / d).relative_to(root).as_posix(), directory=True)
            and not (Path(parent) / d).is_symlink()
            and not (Path(parent) / d).is_junction()
        )
        for filename in sorted(files):
            if time.monotonic() >= deadline:
                raise SourceUnavailable("sync_timeout")
            visited += 1
            if visited > get_settings().source_max_files:
                gap(gaps, "file_count_limit")
                dirs.clear()
                break
            path = Path(parent) / filename
            name = path.relative_to(root).as_posix()
            if not allowed_path(name):
                continue
            if path.is_symlink() or path.is_junction():
                gap(gaps, "symlinks_excluded")
                continue
            try:
                data, signature = read_local(path, root)
                signatures[path] = signature
                append_file(destination, name, data, manifest, gaps, budget)
            except SourceUnavailable as exc:
                if str(exc) == "local_directory_changed":
                    raise
                gap(gaps, str(exc))
            except OSError:
                gap(gaps, "local_file_unavailable")
        if visited > get_settings().source_max_files:
            break
    for path, expected in signatures.items():
        try:
            current = path.stat(follow_symlinks=False)
            if (current.st_ino, current.st_size, current.st_mtime_ns) != expected:
                raise SourceUnavailable("local_directory_changed")
        except OSError:
            raise SourceUnavailable("local_directory_changed") from None
    return manifest, gaps, budget[0]


def capture_git(request, destination, deadline, credentials=None):
    url = request.get("git_url")
    if not url:
        raise SourceUnavailable("git_not_configured")
    cache = get_settings().source_store_dir / "git-cache" / hashlib.sha256(url.encode()).hexdigest()
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        git_command(["init", "--bare", str(cache)], deadline)
    expected = request.get("expected_commit")
    commit = None
    if expected:
        try:
            commit = (
                git_command(
                    ["rev-parse", "--verify", "--end-of-options", expected + "^{commit}"],
                    deadline,
                    cwd=cache,
                )
                .decode()
                .strip()
            )
        except SourceUnavailable:
            pass
    if commit is None:
        git_command(
            ["fetch", "--force", "--no-tags", "--", url, expected or request["git_ref"]],
            deadline,
            cwd=cache,
            credentials=credentials,
            credential_host=urlsplit(url).netloc,
            cache=cache,
        )
        commit = (
            git_command(
                ["rev-parse", "--verify", "FETCH_HEAD^{commit}"],
                deadline,
                cwd=cache,
            )
            .decode()
            .strip()
        )
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise SourceUnavailable("invalid_git_commit")
    if expected and commit != expected:
        raise SourceUnavailable("git_commit_mismatch")
    return capture_git_tree(cache, commit, destination, deadline)


def capture_git_tree(cache, commit, destination, deadline):
    listing = git_command(["ls-tree", "-r", "-z", "-l", commit], deadline, cwd=cache)
    manifest, gaps, budget = [], [], [0]
    visited = 0
    for record in listing.split(b"\0"):
        if not record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            gap(gaps, "non_utf8_paths_excluded")
            continue
        if not allowed_path(name):
            continue
        visited += 1
        if visited > get_settings().source_max_files:
            gap(gaps, "file_count_limit")
            break
        mode, kind, object_id, size = metadata.split()
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            gap(gaps, "symlinks_or_submodules_excluded")
            continue
        if int(size) > get_settings().source_max_file_bytes:
            gap(gaps, "file_size_limit")
            continue
        data = git_command(
            ["cat-file", "blob", object_id.decode()],
            deadline,
            cwd=cache,
            max_output=get_settings().source_max_file_bytes,
        )
        if data.startswith(b"version https://git-lfs.github.com/spec/"):
            gap(gaps, "lfs_objects_not_downloaded")
            continue
        append_file(destination, name, data, manifest, gaps, budget)
    return manifest, gaps, budget[0], commit


def svn_xml(arguments, deadline, credentials):
    data = svn_command(arguments, deadline, credentials=credentials)
    try:
        if b"<!DOCTYPE" in data.upper():
            raise ValueError
        return ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError):
        raise SourceUnavailable("invalid_svn_response") from None


def capture_svn(request, destination, deadline, credentials=None):
    url = request.get("svn_url")
    if not url:
        raise SourceUnavailable("svn_not_configured")
    selected = request.get("expected_revision") or request.get("svn_revision", "HEAD")
    info = svn_xml(["info", "--xml", "-r", selected, url + "@" + selected], deadline, credentials)
    entry = info.find("entry")
    revision = entry.get("revision", "") if entry is not None else ""
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,18})", revision):
        raise SourceUnavailable("invalid_svn_response")
    if selected != "HEAD" and revision != selected:
        raise SourceUnavailable("svn_revision_mismatch")
    if entry.get("kind") != "dir":
        raise SourceUnavailable("svn_directory_required")
    # Resolve HEAD once; both operative and peg revisions stay fixed for every read.
    target = url + "@" + revision
    listing = svn_xml(["list", "--xml", "-R", "-r", revision, target], deadline, credentials)
    properties = svn_xml(["proplist", "--xml", "-R", "-r", revision, target], deadline, credentials)
    manifest, gaps, budget, special = [], [], [0], set()
    for item in properties.findall("target"):
        names = {p.get("name") for p in item.findall("property")}
        if "svn:externals" in names:
            gap(gaps, "svn_externals_not_downloaded")
        if "svn:special" in names:
            special.add(unquote(item.get("path", "")))
    visited = 0
    for item in listing.findall("./list/entry"):
        name = item.findtext("name", "")
        if item.get("kind") != "file" or not allowed_path(name):
            continue
        visited += 1
        if visited > get_settings().source_max_files:
            gap(gaps, "file_count_limit")
            break
        file_url = url + "/" + quote(name, safe="/")
        if unquote(file_url) in special:
            gap(gaps, "symlinks_excluded")
            continue
        try:
            size = int(item.findtext("size", "-1"))
        except ValueError:
            raise SourceUnavailable("invalid_svn_response") from None
        if size < 0:
            raise SourceUnavailable("invalid_svn_response")
        if size > get_settings().source_max_file_bytes:
            gap(gaps, "file_size_limit")
            continue
        data = svn_command(
            ["cat", "--ignore-keywords", "-r", revision, file_url + "@" + revision],
            deadline,
            credentials=credentials,
            max_output=get_settings().source_max_file_bytes,
        )
        append_file(destination, name, data, manifest, gaps, budget)
    return manifest, gaps, budget[0], revision


def build_snapshot(identifier, request, credentials=None):
    settings = get_settings()
    root = settings.source_store_dir.resolve()
    (root / "snapshots").mkdir(parents=True, exist_ok=True)
    (root / "staging").mkdir(exist_ok=True)
    if tree_bytes(root) + settings.source_max_snapshot_bytes > settings.source_max_store_bytes:
        raise SourceUnavailable("source_store_limit")
    deadline = time.monotonic() + settings.source_sync_timeout_seconds
    gaps, source, commit, revision = [], None, None, None
    with tempfile.TemporaryDirectory(prefix="capture-", dir=root / "staging") as staging:
        destination = Path(staging) / "files"
        destination.mkdir()
        if request["source"] == "git" or (
            request["source"] == "auto"
            and (request.get("git_url") or request.get("expected_commit"))
        ):
            try:
                manifest, issues, total, commit = capture_git(
                    request,
                    destination,
                    deadline,
                    credentials,
                )
                gaps.extend(issues)
                source = "git"
            except SourceUnavailable as exc:
                gap(gaps, str(exc))
                if request["source"] == "git":
                    raise
        if request["source"] == "svn" or (request["source"] == "auto" and request.get("svn_url")):
            try:
                manifest, issues, total, revision = capture_svn(
                    request, destination, deadline, credentials
                )
                gaps.extend(issues)
                source = "svn"
            except SourceUnavailable as exc:
                gap(gaps, str(exc))
                if request["source"] == "svn":
                    raise
        if source is None:
            if not request.get("local_path"):
                raise SourceUnavailable(gaps[0] if gaps else "no_source_available")
            # Reserve a separate bounded local attempt after a Git timeout.
            local_deadline = time.monotonic() + min(30, settings.source_sync_timeout_seconds)
            for attempt in range(2):
                shutil.rmtree(contained(root / "staging", destination))
                destination.mkdir()
                try:
                    manifest, issues, total = capture_local(
                        request["local_path"],
                        destination,
                        local_deadline,
                    )
                    gaps.extend(issues)
                    source = "local"
                    gap(gaps, "local_source_version_unverified")
                    break
                except SourceUnavailable as exc:
                    if str(exc) != "local_directory_changed" or attempt:
                        raise
        if not manifest:
            raise SourceUnavailable("no_readable_source_files")
        manifest.sort(key=lambda item: item["path"])
        subdir = request.get("source_subdir", ".")
        if subdir != "." and not any(x["path"].startswith(subdir + "/") for x in manifest):
            gap(gaps, "primary_source_directory_missing")
        content_hash = hashlib.sha256(
            json.dumps(
                [(x["path"], x["sha256"]) for x in manifest],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        final = contained(root / "snapshots", root / "snapshots" / str(identifier))
        destination.rename(final)
        return {
            "source": source,
            "git_commit": commit,
            "svn_revision": revision,
            "content_hash": content_hash,
            "manifest": manifest,
            "data_gaps": gaps,
            "file_count": len(manifest),
            "total_bytes": total,
            "status": "partial"
            if any(g != "local_source_version_unverified" for g in gaps)
            else "ready",
        }


def read_snapshot_file(snapshot, path):
    path = relative_path(path)
    metadata = next((x for x in snapshot.manifest if x["path"] == path), None)
    if metadata is None:
        raise SourceUnavailable("source_file_not_in_snapshot")
    root = get_settings().source_store_dir / "snapshots" / str(snapshot.id)
    try:
        target = contained(root, root.joinpath(*PurePosixPath(path).parts))
        if target.is_symlink():
            raise SourceUnavailable("snapshot_integrity_error")
        with target.open("rb") as stream:
            data = stream.read(metadata["bytes"] + 1)
        if hashlib.sha256(data).hexdigest() != metadata["stored_sha256"]:
            raise SourceUnavailable("snapshot_integrity_error")
        return data.decode("utf-8")
    except OSError:
        raise SourceUnavailable("snapshot_storage_unavailable") from None
