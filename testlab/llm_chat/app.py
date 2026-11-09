"""Single-turn LLM test service with inspection records and result callbacks."""

import json
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import httpx2
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODEL = os.environ.get("AI_MODEL", "deepseek-flash")
BASE_URL = os.environ.get("AI_BASE_URL", "https://api.deepseek.com")
KEY_FILE = Path(os.environ.get("AI_KEY_FILE", "/run/secrets/llm_key"))
RECORDS_FILE = Path(os.environ.get("CHAT_RECORDS_FILE", "/data/records.jsonl"))
DATABASE_FILE = Path(os.environ.get("CHAT_DATABASE_FILE", "/data/chat.sqlite3"))


@contextmanager
def database():
    connection = sqlite3.connect(DATABASE_FILE, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database():
    DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT,
                checked INTEGER NOT NULL DEFAULT 0 CHECK (checked IN (0, 1)),
                checked_at TEXT,
                check_result TEXT
            )
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(conversations)")}
        for name, definition in {
            "checked": "INTEGER NOT NULL DEFAULT 0 CHECK (checked IN (0, 1))",
            "checked_at": "TEXT",
            "check_result": "TEXT",
        }.items():
            if name not in columns:
                db.execute(f"ALTER TABLE conversations ADD COLUMN {name} {definition}")
        db.execute("""
            CREATE INDEX IF NOT EXISTS conversations_updated_at
            ON conversations(updated_at, id) WHERE deleted_at IS NULL
        """)
        if RECORDS_FILE.exists():
            for line in RECORDS_FILE.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                for field in ("created_at", "updated_at", "deleted_at"):
                    if record[field] is not None:
                        record[field] = (
                            datetime.fromisoformat(record[field])
                            .astimezone(UTC)
                            .isoformat(timespec="microseconds")
                        )
                db.execute(
                    """
                    INSERT INTO conversations
                    (id, question, answer, model, created_at, updated_at, deleted_at)
                    VALUES (:id, :question, :answer, :model, :created_at, :updated_at, :deleted_at)
                    ON CONFLICT(id) DO NOTHING
                """,
                    record,
                )


@asynccontextmanager
async def lifespan(_app):
    initialize_database()
    yield


app = FastAPI(title="LLM 对话测试服务", lifespan=lifespan)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入问题")
        return value.strip()


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["pass", "fail", "unknown"]
    summary: str = Field(min_length=1, max_length=4000)
    details: str = Field(default="", max_length=8000)


class RecordCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    result: CheckResult


class RecordChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[RecordCheck] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({record.id for record in self.records}) != len(self.records):
            raise ValueError("同一批次不能重复提交相同记录")
        return self


def record_data(row):
    record = dict(row)
    record["checked"] = bool(record["checked"])
    record["check_result"] = json.loads(record["check_result"]) if record["check_result"] else None
    return record


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).with_name("index.html"))


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL}


@app.post("/ask")
def ask(body: Question):
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        raise HTTPException(503, "模型密钥未配置") from None
    if not key:
        raise HTTPException(503, "模型密钥未配置")
    try:
        response = httpx2.post(
            BASE_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": body.question}],
                "stream": False,
                "max_tokens": 1024,
                "thinking": {"type": "disabled"},
            },
            timeout=180,
            follow_redirects=False,
            trust_env=False,
        )
    except httpx2.TimeoutException:
        raise HTTPException(504, "模型响应超时，请重试") from None
    except httpx2.RequestError:
        raise HTTPException(502, "模型连接失败，请重试") from None
    if response.status_code != 200:
        raise HTTPException(502, f"模型请求失败（HTTP {response.status_code}）")
    try:
        answer = response.json()["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("empty_answer")
    except (ValueError, KeyError, IndexError, TypeError):
        raise HTTPException(502, "模型未返回有效回答") from None
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    record = {
        "id": str(uuid4()),
        "question": body.question,
        "answer": answer,
        "model": MODEL,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "checked": False,
        "checked_at": None,
        "check_result": None,
    }
    with database() as db:
        db.execute(
            """
            INSERT INTO conversations
            (id, question, answer, model, created_at, updated_at, deleted_at)
            VALUES (:id, :question, :answer, :model, :created_at, :updated_at, :deleted_at)
        """,
            record,
        )
    return record


@app.get("/records")
def records(
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    cursor: Annotated[int, Query(ge=0)] = 0,
    checked: bool | None = None,
    record_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    check_status: Literal["pass", "fail", "unknown"] | None = None,
):
    if any(t is not None and t.tzinfo is None for t in (updated_after, updated_before)):
        raise HTTPException(422, "时间必须包含时区")
    if updated_after and updated_before and updated_after >= updated_before:
        raise HTTPException(422, "开始时间必须早于结束时间")
    conditions, parameters = ["deleted_at IS NULL"], []
    if checked is not None:
        conditions.append("checked = ?")
        parameters.append(int(checked))
    if record_id is not None:
        conditions.append("id = ?")
        parameters.append(record_id)
    if q is not None:
        conditions.append("(instr(question, ?) > 0 OR instr(answer, ?) > 0)")
        parameters.extend([q, q])
    if check_status is not None:
        conditions.append("json_extract(check_result, '$.status') = ?")
        parameters.append(check_status)
    for timestamp, operator in ((updated_after, ">="), (updated_before, "<")):
        if timestamp is not None:
            conditions.append(f"updated_at {operator} ?")
            parameters.append(timestamp.astimezone(UTC).isoformat(timespec="microseconds"))
    with database() as db:
        selected = db.execute(
            "SELECT * FROM conversations WHERE "
            + " AND ".join(conditions)
            + " ORDER BY updated_at, id LIMIT ? OFFSET ?",
            [*parameters, limit + 1, cursor],
        ).fetchall()
    items = [
        {
            "id": row["id"],
            "version": "1",
            "updated_at": row["updated_at"],
            "facts": record_data(row),
        }
        for row in selected[:limit]
    ]
    end = cursor + len(items)
    return {"items": items, "next_cursor": str(end) if len(selected) > limit else None}


@app.post("/records/checks")
def mark_checked(body: RecordChecks):
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    ids = [record.id for record in body.records]
    placeholders = ",".join("?" for _ in ids)
    with database() as db:
        existing = {
            row["id"]
            for row in db.execute(
                f"SELECT id FROM conversations WHERE id IN ({placeholders}) AND deleted_at IS NULL",
                ids,
            )
        }
        missing = [identifier for identifier in ids if identifier not in existing]
        if missing:
            raise HTTPException(404, {"message": "对话记录不存在", "ids": missing})
        for record in body.records:
            db.execute(
                """
                UPDATE conversations
                SET checked=1, checked_at=?, check_result=?, updated_at=?
                WHERE id=? AND deleted_at IS NULL
                """,
                (now, record.result.model_dump_json(), now, record.id),
            )
        saved = {
            row["id"]: record_data(row)
            for row in db.execute(f"SELECT * FROM conversations WHERE id IN ({placeholders})", ids)
        }
    return {"updated": len(ids), "items": [saved[identifier] for identifier in ids]}
