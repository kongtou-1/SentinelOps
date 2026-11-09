"""Prepare a source regression image without changing the running healthy service."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "testlab"))
from manage import run, write_json

repo = ROOT / "testlab/service"
bug = ROOT / ".local/testlab/source-bug"
if not bug.exists():
    run(["git", "clone", "--no-hardlinks", str(repo), str(bug)])
    run(["git", "-C", str(bug), "checkout", "-b", "fault/source-503"])
if "Regression: ignores runtime state." not in (bug / "service.py").read_text(
    encoding="utf-8"
):
    source = bug / "service.py"
    text = source.read_text(encoding="utf-8").replace(
        "def work():\n    mode = scenario()",
        'def work():\n    mode = "business_error"  # Regression: ignores runtime state.',
    )
    assert text != source.read_text(encoding="utf-8")
    source.write_text(text.replace('"1.0.0"', '"1.0.0-bug"'), encoding="utf-8")
    dockerfile = bug / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text().replace('"1.0.0"', '"1.0.0-bug"'), encoding="utf-8"
    )
    run(["git", "-C", str(bug), "add", "service.py", "Dockerfile"])
    run(
        [
            "git",
            "-C",
            str(bug),
            "-c",
            "user.name=Test Lab",
            "-c",
            "user.email=testlab@example.test",
            "commit",
            "-m",
            "Reproduce business 503 caused by source regression",
        ]
    )
    run(["git", "-C", str(bug), "tag", "v1.0.0-bug"])
revision = run(["git", "-C", str(bug), "rev-parse", "HEAD"])
run(
    [
        "git",
        "-C",
        str(repo),
        "fetch",
        str(bug),
        "fault/source-503:refs/heads/fault/source-503",
        "--tags",
    ]
)
print(
    run(
        [
            "docker",
            "build",
            "--build-arg",
            "REVISION=" + revision,
            "-t",
            "sentinelops-testlab-business:v1-bug",
            str(bug),
        ]
    )
)
results = {}
for tag, expected in (("v1", 200), ("v1-bug", 503)):
    output = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "sentinelops-testlab-business:" + tag,
            "/app/.venv/bin/python",
            "-c",
            'import sys; sys.path.insert(0,"/lab"); import service; print(service.work()[0])',
        ]
    )
    assert int(output) == expected, (tag, output)
    results[tag] = {
        "http_business_status": expected,
        "image_id": run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                "sentinelops-testlab-business:" + tag,
            ]
        ),
    }
write_json(
    ROOT / ".local/testlab/source-versions.json",
    {
        "fixed_commit": run(["git", "-C", str(repo), "rev-parse", "main"]),
        "bug_commit": revision,
        "images": results,
        "note": "Bug image is prepared but not deployed; current service remains healthy.",
    },
)
print(
    "Source regression and working images prepared; current business deployment unchanged"
)
