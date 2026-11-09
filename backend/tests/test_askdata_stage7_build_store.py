"""阶段 7：构建写入必须在事务外可见（catalog 跨线程写）。

上游 store_factory 的约定是「返回活动事务，或一个 autocommit 句柄」。
平台实现早期始终不提交，于是表行留在主线程的隐式事务里，写列的线程池
连接看不到它，复合外键直接失败。这组用例锁住该约定。
"""

from __future__ import annotations

import uuid

import pytest

from app.integrations import askdata_semantic


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.open = False

    def in_transaction(self):
        return self.open

    def commit(self):
        self.commits += 1
        self.open = False


@pytest.fixture()
def storage(monkeypatch):
    connection = FakeConnection()

    connection.open = True
    monkeypatch.setattr(askdata_semantic, "SessionLocal", lambda: connection)
    monkeypatch.setattr(
        askdata_semantic,
        "_BuildOntologyStore",
        lambda db, version_id, guard, commit=None: type(
            "Store", (), {"db": db, "version_id": version_id, "commit": staticmethod(commit)}
        )(),
    )
    build = askdata_semantic.BuildStorage(uuid.uuid4(), lambda writing: None)
    return build, connection


def test_store_commits_when_no_explicit_transaction_is_open(storage):
    build, connection = storage
    build.store().commit()
    assert connection.commits == 1


def test_store_stays_silent_inside_an_explicit_transaction(storage):
    build, connection = storage
    with build.transaction():
        build.store().commit()
        assert connection.commits == 0
    assert connection.commits == 1


def test_commit_is_skipped_when_the_connection_has_nothing_open(storage):
    build, connection = storage
    store = build.store()
    connection.open = False
    store.commit()
    assert connection.commits == 0
