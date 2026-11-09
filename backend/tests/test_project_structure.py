"""Compatibility and dependency boundaries for the SentinelOps reorganization."""

import ast
import json
from importlib.util import resolve_name
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.main import app
from app.models import Base

BACKEND = Path(__file__).resolve().parents[1]


def imported_names(tree, package):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            for alias in node.names:
                yield node, f"{module}.{alias.name}"


def package_for(path):
    parts = path.relative_to(BACKEND).with_suffix("").parts
    return ".".join(parts[:-1])


@pytest.mark.parametrize(
    "source,package,expected",
    [
        (
            "import app.services.resources.crud as resources",
            "app.api",
            "app.services.resources.crud",
        ),
        (
            "from .metrics import metric_expression",
            "app.services.monitoring",
            "app.services.monitoring.metrics.metric_expression",
        ),
        ("from .. import records", "app.services.resources", "app.services.records"),
        ("from ...core.secrets import cipher", "app.services.resources", "app.core.secrets.cipher"),
    ],
)
def test_dependency_scan_resolves_absolute_and_relative_imports(source, package, expected):
    assert [name for _, name in imported_names(ast.parse(source), package)] == [expected]


def test_openapi_matches_existing_contract():
    expected = json.loads(
        (BACKEND.parent / "docs/backend/openapi.json").read_text(encoding="utf-8")
    )
    assert app.openapi() == expected


def test_all_models_preserve_metadata_and_python_identity():
    expected = json.loads((BACKEND / "tests/contracts/metadata.json").read_text(encoding="utf-8"))
    actual = {
        t.name: {
            "table": str(CreateTable(t).compile(dialect=postgresql.dialect())),
            "indexes": sorted(
                str(CreateIndex(i).compile(dialect=postgresql.dialect())) for i in t.indexes
            ),
        }
        for t in Base.metadata.tables.values()
    }

    def normalize(metadata):
        return {
            name: {"table": sorted(value["table"].splitlines()), "indexes": value["indexes"]}
            for name, value in metadata.items()
        }

    assert normalize(actual) == normalize(expected)
    assert all(mapper.class_.__module__ == "app.models" for mapper in Base.registry.mappers)


def test_layer_dependencies_and_transaction_ownership():
    violations = []
    for path in (BACKEND / "app").rglob("*.py"):
        layer = path.relative_to(BACKEND / "app").parts[0]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node, module in imported_names(tree, package_for(path)):
            if layer in {
                "services",
                "repositories",
                "workers",
                "integrations",
            } and module.startswith("app.api"):
                violations.append(f"{path.name}:{node.lineno}: imports API")
            if layer == "repositories" and module.startswith(
                ("fastapi", "app.services", "app.workers", "app.integrations")
            ):
                violations.append(f"{path.name}:{node.lineno}: repository imports upper layer")
            if (
                layer == "api"
                and module.startswith("sqlalchemy")
                and not module.startswith("sqlalchemy.orm.")
                and module != "sqlalchemy.orm"
            ):
                violations.append(f"{path.name}:{node.lineno}: API imports SQLAlchemy")
        for node in ast.walk(tree):
            if (
                layer == "repositories"
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"commit", "rollback"}
            ):
                violations.append(f"{path.name}:{node.lineno}: repository owns transaction")
    assert not violations, "\n".join(violations)
    assert {p.name for p in (BACKEND / "app").glob("*.py")} == {"__init__.py", "main.py"}


def test_reusable_modules_do_not_import_business_flows():
    foundation = (
        "app.api",
        "app.db",
        "app.models",
        "app.repositories",
        "app.services",
        "app.workers",
        "app.integrations",
    )
    boundaries = {
        "core/secrets.py": foundation,
        "core/service_auth.py": foundation,
        "services/records.py": ("app.services", "app.integrations", "app.workers", "app.api"),
        "services/monitoring/metrics.py": foundation,
        "services/llm/status.py": foundation,
    }
    boundaries.update(
        {
            str(path.relative_to(BACKEND / "app")).replace("\\", "/"): foundation
            for path in (BACKEND / "app/utils").glob("*.py")
        }
    )
    violations = []
    for relative, forbidden in boundaries.items():
        path = BACKEND / "app" / relative
        for node, module in imported_names(
            ast.parse(path.read_text(encoding="utf-8")), package_for(path)
        ):
            if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                violations.append(f"{relative}:{node.lineno}: imports {module}")
    assert not violations, "\n".join(violations)


def test_callers_import_shared_helpers_from_their_own_modules():
    moved = {
        "app.integrations.ssh": {"cipher"},
        "app.services.monitoring.config": {
            "file_secret",
            "service_authorization",
            "prometheus",
            "metric_expression",
            "llm_metric_expression",
        },
        "app.services.resources.crud": {
            "active_record",
            "save",
            "metric_availability",
            "metrics_result",
            "unconfigured_metric",
            "server_metrics",
            "container_metrics",
        },
        "app.services.llm.probes": {"endpoint_status"},
        "app.services.sources.identity": {"docker_identifier"},
        "app.services.reports.logic": {"encoded", "digest"},
    }
    forbidden = {f"{module}.{name}" for module, names in moved.items() for name in names}
    violations = []
    for folder in ("app", "scripts", "tests"):
        for path in (BACKEND / folder).rglob("*.py"):
            for node, module in imported_names(
                ast.parse(path.read_text(encoding="utf-8")), package_for(path)
            ):
                if module in forbidden or module in {f"{name}.*" for name in moved}:
                    violations.append(f"{path.name}:{node.lineno}: imports {module}")
    assert not violations, "\n".join(violations)
