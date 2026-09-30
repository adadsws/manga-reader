"""Fast, immutable processing history with exact in-memory lookup keys."""
import atexit
import hashlib
import json
import os
import queue
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


SCHEMA_VERSION = 2


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_json(value):
    return digest_bytes(canonical_json(value).encode("utf-8"))


def compound_key(*values):
    return digest_json(values)


def default_history_root():
    configured = os.environ.get("READER_HISTORY_DIR")
    if configured:
        return Path(configured)
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Reader" / "history"


class HistoryStore:
    """SQLite metadata plus content-addressed blobs; online lookups never query SQLite."""

    def __init__(self, root=None):
        self.root = Path(root) if root else default_history_root()
        self.db_path = self.root / "history.sqlite3"
        self.blobs = self.root / "blobs"
        self.lock = threading.RLock()
        self.page_indexes = {"image": {}, "ocr": {}, "text": {}}
        self.audio_index = {}
        self.errors = []
        self.last_created = 0.0
        self.jobs = queue.Queue(maxsize=256)
        self.closed = False
        self._prepare()
        self._load_indexes()
        self.worker = threading.Thread(
            target=self._writer, name="reader-history-writer", daemon=True
        )
        self.worker.start()
        atexit.register(self.close)

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _prepare(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(parents=True, exist_ok=True)
        with self._database() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS page_versions(
                  id TEXT PRIMARY KEY, created REAL NOT NULL, input_kind TEXT NOT NULL,
                  input_sha TEXT, image_key TEXT, ocr_key TEXT, text_key TEXT,
                  sentences_sha TEXT NOT NULL, source_version TEXT, match_stage TEXT NOT NULL,
                  promote INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
                  hidden INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS active_pages(
                  stage TEXT NOT NULL, stage_key TEXT NOT NULL, version_id TEXT NOT NULL,
                  PRIMARY KEY(stage, stage_key),
                  FOREIGN KEY(version_id) REFERENCES page_versions(id)
                );
                CREATE TABLE IF NOT EXISTS audio_versions(
                  id TEXT PRIMARY KEY, created REAL NOT NULL, audio_key TEXT NOT NULL,
                  audio_sha TEXT NOT NULL, audit_sha TEXT NOT NULL,
                  source_page_version TEXT, promote INTEGER NOT NULL DEFAULT 0,
                  status TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS active_audio(
                  audio_key TEXT PRIMARY KEY, version_id TEXT NOT NULL,
                  FOREIGN KEY(version_id) REFERENCES audio_versions(id)
                );
                CREATE TABLE IF NOT EXISTS runs(
                  id TEXT PRIMARY KEY, created REAL NOT NULL, input_kind TEXT NOT NULL,
                  input_sha TEXT, match_stage TEXT NOT NULL, source_version TEXT,
                  result_version TEXT, reuse INTEGER NOT NULL,
                  promote INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
                  hidden INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS page_created_idx ON page_versions(created DESC);
                CREATE INDEX IF NOT EXISTS audio_created_idx ON audio_versions(created DESC);
                CREATE INDEX IF NOT EXISTS run_created_idx ON runs(created DESC);
                """
            )
            current_version = db.execute("PRAGMA user_version").fetchone()[0]
            if current_version > SCHEMA_VERSION:
                raise ValueError("历史数据库版本高于当前程序支持版本")
            for table in ("page_versions", "audio_versions", "runs"):
                columns = {
                    row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if "hidden" not in columns:
                    db.execute(
                        f"ALTER TABLE {table} ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"
                    )
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _blob_path(self, kind, sha, suffix):
        return self.blobs / kind / sha[:2] / (sha + suffix)

    def _write_blob(self, kind, sha, suffix, body):
        target = self._blob_path(kind, sha, suffix)
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_bytes(body)
        os.replace(temporary, target)
        return target

    def _read_json_blob(self, sha):
        path = self._blob_path("json", sha, ".json")
        body = path.read_bytes()
        if digest_bytes(body) != sha:
            raise ValueError("历史 JSON 完整性校验失败")
        return json.loads(body.decode("utf-8"))

    def _load_indexes(self):
        if not self.db_path.is_file():
            return
        with self._database() as db:
            for table in ("page_versions", "audio_versions", "runs"):
                latest = db.execute(f"SELECT MAX(created) FROM {table}").fetchone()[0]
                if latest is not None:
                    self.last_created = max(self.last_created, float(latest))
            rows = db.execute(
                """SELECT id,sentences_sha,image_key,ocr_key,text_key
                   FROM page_versions
                   WHERE status='complete' AND hidden=0
                   ORDER BY created ASC,id ASC"""
            ).fetchall()
            for version_id, sentences_sha, image_key, ocr_key, text_key in rows:
                try:
                    sentences = self._read_json_blob(sentences_sha)
                except Exception as exc:
                    self.errors.append(str(exc))
                    continue
                if not sentences:
                    continue
                for stage, stage_key in (
                    ("image", image_key), ("ocr", ocr_key), ("text", text_key)
                ):
                    if stage_key:
                        self.page_indexes[stage][stage_key] = {
                            "version_id": version_id,
                            "sentences": sentences,
                        }
            rows = db.execute(
                """SELECT audio_key,id,audio_sha,audit_sha
                   FROM audio_versions
                   WHERE status='complete' AND hidden=0
                   ORDER BY created ASC,id ASC"""
            ).fetchall()
            for audio_key, version_id, audio_sha, audit_sha in rows:
                audio_path = self._blob_path("audio", audio_sha, ".wav")
                audit_path = self._blob_path("json", audit_sha, ".json")
                if audio_path.is_file() and audit_path.is_file():
                    self.audio_index[audio_key] = {
                        "version_id": version_id,
                        "audio_sha": audio_sha,
                        "audit_sha": audit_sha,
                    }

    def _created_at(self):
        # Windows 的墙钟在快速连续调用时可能返回同一值；单调微增保证“最新”确定。
        with self.lock:
            current = time.time()
            if current <= self.last_created:
                current = self.last_created + 0.000001
            self.last_created = current
            return current

    def lookup_page(self, stage, stage_key):
        with self.lock:
            record = self.page_indexes.get(stage, {}).get(stage_key)
            if not record or not record.get("sentences"):
                self.page_indexes.get(stage, {}).pop(stage_key, None)
                return None
            return {
                "version_id": record["version_id"],
                "sentences": record["sentences"],
            }

    def lookup_audio(self, audio_key):
        with self.lock:
            record = self.audio_index.get(audio_key)
            if not record:
                return None
            if "body" in record:
                body = record["body"]
                audit = record.get("audit", [])
            else:
                try:
                    path = self._blob_path("audio", record["audio_sha"], ".wav")
                    body = path.read_bytes()
                    if digest_bytes(body) != record["audio_sha"]:
                        raise ValueError("历史 WAV 完整性校验失败")
                    audit = self._read_json_blob(record["audit_sha"])
                except Exception as exc:
                    self.errors.append(str(exc))
                    self.audio_index.pop(audio_key, None)
                    return None
            return {"version_id": record["version_id"], "body": body, "audit": audit}

    def has_audio(self, audio_key):
        with self.lock:
            return audio_key in self.audio_index

    def _index_page(self, record):
        indexed = []
        if record["hidden"] or not record["sentences"]:
            return indexed
        for stage in ("image", "ocr", "text"):
            stage_key = record.get(stage + "_key")
            if not stage_key:
                continue
            index = self.page_indexes[stage]
            previous = index.get(stage_key)
            index[stage_key] = {
                "version_id": record["id"], "sentences": record["sentences"]
            }
            indexed.append((stage, stage_key, previous))
        return indexed

    def register_page(self, *, input_kind, input_sha, image_key, ocr_key, text_key,
                      sentences, source_version=None, match_stage="none", hidden=False,
                      input_body=None):
        record = {
            "id": uuid.uuid4().hex,
            "created": self._created_at(),
            "input_kind": input_kind,
            "input_sha": input_sha,
            "image_key": image_key,
            "ocr_key": ocr_key,
            "text_key": text_key,
            "sentences": sentences,
            "source_version": source_version,
            "match_stage": match_stage,
            "hidden": bool(hidden),
            "input_body": bytes(input_body) if input_body is not None else None,
        }
        with self.lock:
            record["indexed"] = self._index_page(record)
        self._submit("page", record)
        return record["id"]

    def record_run(self, *, input_kind, input_sha, match_stage, source_version,
                   result_version, reuse, hidden, status="complete"):
        self._submit("run", {
            "id": uuid.uuid4().hex, "created": self._created_at(), "input_kind": input_kind,
            "input_sha": input_sha, "match_stage": match_stage,
            "source_version": source_version, "result_version": result_version,
            "reuse": bool(reuse), "hidden": bool(hidden), "status": status,
        })

    def register_audio(self, audio_key, body, audit, source_page_version=None, hidden=False):
        record = {
            "id": uuid.uuid4().hex, "created": self._created_at(), "audio_key": audio_key,
            "body": bytes(body), "audit": audit,
            "source_page_version": source_page_version, "hidden": bool(hidden),
        }
        with self.lock:
            if not hidden:
                previous = self.audio_index.get(audio_key)
                self.audio_index[audio_key] = {
                    "version_id": record["id"], "body": record["body"], "audit": record["audit"]
                }
                record["indexed"] = True
                record["previous"] = previous
            else:
                record["indexed"] = False
        self._submit("audio", record)
        return record["id"]

    def _submit(self, kind, record):
        if self.closed:
            return
        try:
            self.jobs.put_nowait((kind, record))
        except queue.Full:
            # Persistence is intentionally outside the latency-sensitive path.
            threading.Thread(
                target=self.jobs.put, args=((kind, record),), daemon=True,
                name="reader-history-overflow"
            ).start()

    def _writer(self):
        while True:
            item = self.jobs.get()
            try:
                if item is None:
                    return
                kind, record = item
                for attempt in range(3):
                    try:
                        getattr(self, "_persist_" + kind)(record)
                        break
                    except Exception as exc:
                        if attempt == 2:
                            self.errors.append(str(exc))
                            self._drop_failed(kind, record)
                        else:
                            time.sleep(0.05 * (attempt + 1))
            finally:
                self.jobs.task_done()

    def _drop_failed(self, kind, record):
        with self.lock:
            if kind == "page":
                for stage, stage_key, previous in record.get("indexed", []):
                    current = self.page_indexes.get(stage, {}).get(stage_key)
                    if current and current.get("version_id") == record["id"]:
                        if previous is None:
                            self.page_indexes[stage].pop(stage_key, None)
                        else:
                            self.page_indexes[stage][stage_key] = previous
            elif kind == "audio":
                current = self.audio_index.get(record["audio_key"])
                if current and current.get("version_id") == record["id"]:
                    if record.get("previous") is None:
                        self.audio_index.pop(record["audio_key"], None)
                    else:
                        self.audio_index[record["audio_key"]] = record["previous"]

    def _persist_page(self, record):
        json_body = canonical_json(record["sentences"]).encode("utf-8")
        sentences_sha = digest_bytes(json_body)
        self._write_blob("json", sentences_sha, ".json", json_body)
        if record["input_body"] is not None:
            suffix = ".jpg" if record["input_kind"] == "image" else ".txt"
            self._write_blob("input", record["input_sha"], suffix, record["input_body"])
        with self._database() as db:
            db.execute(
                """INSERT INTO page_versions(
                     id,created,input_kind,input_sha,image_key,ocr_key,text_key,
                     sentences_sha,source_version,match_stage,promote,status,hidden
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record["id"], record["created"], record["input_kind"], record["input_sha"],
                 record["image_key"], record["ocr_key"], record["text_key"], sentences_sha,
                 record["source_version"], record["match_stage"], 0, "complete",
                 int(record["hidden"])),
            )
            for stage, stage_key, _previous in record["indexed"]:
                db.execute(
                    """INSERT INTO active_pages(stage,stage_key,version_id) VALUES(?,?,?)
                       ON CONFLICT(stage,stage_key) DO UPDATE SET version_id=excluded.version_id""",
                    (stage, stage_key, record["id"]),
                )

    def _persist_audio(self, record):
        audio_sha = digest_bytes(record["body"])
        audit_body = canonical_json(record["audit"]).encode("utf-8")
        audit_sha = digest_bytes(audit_body)
        self._write_blob("audio", audio_sha, ".wav", record["body"])
        self._write_blob("json", audit_sha, ".json", audit_body)
        with self._database() as db:
            db.execute(
                """INSERT INTO audio_versions(
                     id,created,audio_key,audio_sha,audit_sha,source_page_version,
                     promote,status,hidden
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (record["id"], record["created"], record["audio_key"], audio_sha,
                 audit_sha, record["source_page_version"], 0, "complete",
                 int(record["hidden"])),
            )
            if record["indexed"]:
                db.execute(
                    """INSERT INTO active_audio(audio_key,version_id) VALUES(?,?)
                       ON CONFLICT(audio_key) DO UPDATE SET version_id=excluded.version_id""",
                    (record["audio_key"], record["id"]),
                )
        with self.lock:
            current = self.audio_index.get(record["audio_key"])
            if current and current.get("version_id") == record["id"]:
                self.audio_index[record["audio_key"]] = {
                    "version_id": record["id"], "audio_sha": audio_sha, "audit_sha": audit_sha
                }

    def _persist_run(self, record):
        with self._database() as db:
            db.execute(
                """INSERT INTO runs(
                     id,created,input_kind,input_sha,match_stage,source_version,
                     result_version,reuse,promote,status,hidden
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (record["id"], record["created"], record["input_kind"], record["input_sha"],
                 record["match_stage"], record["source_version"], record["result_version"],
                 int(record["reuse"]), 0, record["status"], int(record["hidden"])),
            )

    def list_versions(self, limit=50):
        self.flush()
        limit = max(1, min(int(limit), 200))
        with self._database() as db:
            db.row_factory = sqlite3.Row
            pages = [dict(row) for row in db.execute(
                """SELECT id,created,input_kind,input_sha,source_version,match_stage,hidden,status
                   FROM page_versions ORDER BY created DESC LIMIT ?""", (limit,)
            )]
            audio = [dict(row) for row in db.execute(
                """SELECT id,created,audio_key,source_page_version,hidden,status
                   FROM audio_versions ORDER BY created DESC LIMIT ?""", (limit,)
            )]
            runs = [dict(row) for row in db.execute(
                "SELECT * FROM runs ORDER BY created DESC LIMIT ?", (limit,)
            )]
        return {
            "pages": pages, "audio": audio, "runs": runs,
            "writer_errors": list(self.errors),
        }

    def flush(self):
        self.jobs.join()

    def close(self):
        if self.closed:
            return
        self.flush()
        self.closed = True
        self.jobs.put(None)
        self.worker.join(timeout=5)
