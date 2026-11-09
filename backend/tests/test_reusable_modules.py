"""Behavioral boundaries shared by callers of the extracted helpers."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.session import SessionLocal
from app.models import User
from app.services.executions.skills import docker_identifier as execution_docker_identifier
from app.services.records import active_record, save
from app.utils.canonical_json import digest, encoded
from app.utils.docker import docker_identifier


def test_active_record_preserves_transaction_and_excludes_deleted_users(make_user):
    identifier = make_user("lookup-user")
    with SessionLocal() as db:
        user = active_record(db, User, identifier)
        user.username = "uncommitted-name"
        assert active_record(db, User, identifier).username == "uncommitted-name"
        db.rollback()
        assert db.get(User, identifier).username == "lookup-user"

        user.deleted_at = datetime.now(UTC)
        db.commit()
        for missing in (identifier, uuid4()):
            with pytest.raises(HTTPException) as error:
                active_record(db, User, missing)
            assert error.value.status_code == 404
            assert error.value.detail == "Resource not found"


def test_shared_save_rolls_back_uniqueness_conflict(make_user):
    make_user("reserved-user")
    identifier = make_user("editable-user")
    with SessionLocal() as db:
        user = db.get(User, identifier)
        user.username = "reserved-user"
        with pytest.raises(HTTPException) as error:
            save(db, user)
        assert error.value.status_code == 409
        assert error.value.detail == "Resource name or code is already reserved"
        assert db.get(User, identifier).username == "editable-user"


@pytest.mark.parametrize(
    "prefix,suffix,executable",
    [
        ("", "", True),
        ("/docker/", "", True),
        ("/system.slice/docker-", ".scope", True),
        ("/arbitrary-prefix/", "", False),
    ],
)
def test_observed_identity_does_not_relax_execution_validation(prefix, suffix, executable):
    identifier = "a" * 64
    value = prefix + identifier + suffix
    assert docker_identifier(value) == identifier
    if executable:
        assert execution_docker_identifier(value) == identifier
    else:
        with pytest.raises(ValueError, match="Invalid asset Docker ID"):
            execution_docker_identifier(value)


def test_report_json_preserves_existing_bytes_and_digest():
    value = {"z": 1, "a": "运维"}
    assert encoded(value) == '{"a":"运维","z":1}'.encode()
    assert digest(value) == "2813334da739834f8cb078d304e480d8ef0984a7176ccd317d225445a52794b8"
    assert digest({"a": "运维", "z": 1}) == digest(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_report_json_rejects_nonfinite_values(value):
    with pytest.raises(ValueError):
        digest({"value": value})
