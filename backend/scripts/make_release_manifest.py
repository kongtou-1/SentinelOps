"""Generate registration JSON from local source and an existing Docker image.

This command records build evidence; it does not deploy, push or modify an image.
"""

import argparse
import json
import subprocess
from pathlib import Path


def run(arguments, cwd=None):
    return subprocess.run(arguments, cwd=cwd, check=True, capture_output=True, timeout=30).stdout


def manifest(source, image, version):
    template = (
        '{"image_id":{{json .Id}},"repo_digests":{{json .RepoDigests}},'
        '"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}'
    )
    identity = json.loads(run(["docker", "image", "inspect", "--format", template, image]))
    commit, clean = None, False
    try:
        commit = run(["git", "rev-parse", "--verify", "HEAD"], cwd=source).decode().strip()
        clean = not run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=source)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    status = "clean_git" if clean and identity["revision"] == commit else "declared"
    return {
        "version": version,
        "expected_commit": commit if clean else None,
        "image_ref": image,
        "image_id": identity["image_id"],
        "repo_digest": next(iter(identity["repo_digests"] or []), None),
        "build_source_status": status,
        "change_summary": "",
        "test_result": "",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.source.is_dir() or args.image.startswith("-"):
        parser.error("Provide an existing source directory and image reference")
    try:
        result = manifest(args.source.resolve(), args.image, args.version)
    except (subprocess.CalledProcessError, OSError, ValueError, subprocess.TimeoutExpired):
        parser.exit(1, "Could not read local source/image identity; no credentials are printed.\n")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Release registration JSON saved to {args.output}")


if __name__ == "__main__":
    main()
