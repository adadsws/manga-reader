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


SCHEMA_VERSION = 1


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
                  promote INTEGER NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS active_pages(
                  stage TEXT NOT NULL, stage_key TEXT NOT NULL, version_id TEXT NOT NULL,
                  PRIMARY KEY(stage, stage_key),
                  FOREIGN KEY(version_id) REFERENCES page_versions(id)
                );
                CREATE TABLE IF NOT EXISTS audio_versions(
                  id TEXT PRIMARY KEY, created REAL NOT NULL, audio_key TEXT NOT NULL,
                  audio_sha TEXT NOT NULL, audit_sha TEXT NOT NULL,
                  source_page_version TEXT, promote INTEGER NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS active_audio(
                  audio_key TEXT PRIMARY KEY, version_id TEXT NOT NULL,
                  FOREIGN KEY(version_id) REFERENCES audio_versions(id)
                );
                CREATE TABLE IF NOT EXISTS runs(
                  id TEXT PRIMARY KEY, created REAL NOT NULL, input_kind TEXT NOT NULL,
                  input_sha TEXT, match_stage TEXT NOT NULL, source_version TEXT,
                  result_version TEXT, reuse INTEGER NOT NULL, promote INTEGER NOT NULL,
                  status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS page_created_idx ON page_versions(created DESC);
                CREATE INDEX IF NOT EXISTS audio_created_idx ON audio_versions(created DESC);
                CREATE INDEX IF NOT EXISTS run_created_idx ON runs(created DESC);
                """
            )

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
            rows = db.execute(
                """SELECT a.stage,a.stage_key,p.id,p.sentences_sha
                   FROM active_pages a JOIN page_versions p ON p.id=a.version_id
                   WHERE p.status='complete'"""
            ).fetchall()
            for stage, stage_key, version_id, sentences_sha in rows:
                try:
                    sentences = self._read_json_blob(sentences_sha)
                except Exception as exc:
                    self.errors.append(str(exc))
                    continue
                if not sentences:
                    continue
                self.page_indexes.setdefault(stage, {})[stage_key] = {
                    "version_id": version_id,
                    "sentences": sentences,
                }
            rows = db.execute(
                """SELECT a.audio_key,v.id,v.audio_sha,v.audit_sha
                   FROM active_audio a JOIN audio_versions v ON v.id=a.version_id
                   WHERE v.status='complete'"""
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

    def _activate_page(self, record):
        activated = []
        if not record["sentences"]:
            return activated
        for stage in ("image", "ocr", "text"):
            stage_key = record.get(stage + "_key")
            if not stage_key:
                continue
            index = self.page_indexes[stage]
            if stage_key not in index or record["promote"]:
                index[stage_key] = {
                    "version_id": record["id"], "sentences": record["sentences"]
                }
                activated.append((stage, stage_key))
        return activated

    def register_page(self, *, input_kind, input_sha, image_key, ocr_key, text_key,
                      sentences, source_version=None, match_stage="none", promote=False,
                      input_body=None):
        record = {
            "id": uuid.uuid4().hex,
            "created": time.time(),
            "input_kind": input_kind,
            "input_sha": input_sha,
            "image_key": image_key,
            "ocr_key": ocr_key,
            "text_key": text_key,
            "sentences": sentences,
            "source_version": source_version,
            "match_stage": match_stage,
            "promote": bool(promote),
            "input_body": bytes(input_body) if input_body is not None else None,
        }
        with self.lock:
            record["activated"] = self._activate_page(record)
        self._submit("page", record)
        return record["id"]

    def record_run(self, *, input_kind, input_sha, match_stage, source_version,
                   result_version, reuse, promote, status="complete"):
        self._submit("run", {
            "id": uuid.uuid4().hex, "created": time.time(), "input_kind": input_kind,
            "input_sha": input_sha, "match_stage": match_stage,
            "source_version": source_version, "result_version": result_version,
            "reuse": bool(reuse), "promote": bool(promote), "status": status,
        })

    def register_audio(self, audio_key, body, audit, source_page_version=None, promote=False):
        record = {
            "id": uuid.uuid4().hex, "created": time.time(), "audio_key": audio_key,
            "body": bytes(body), "audit": audit,
            "source_page_version": source_page_version, "promote": bool(promote),
        }
        with self.lock:
            if audio_key not in self.audio_index or promote:
                self.audio_index[audio_key] = {
                    "version_id": record["id"], "body": record["body"], "audit": record["audit"]
                }
                record["activated"] = True
            else:
                record["activated"] = False
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
                for stage, stage_key in record.get("activated", []):
                    current = self.page_indexes.get(stage, {}).get(stage_key)
                    if current and current.get("version_id") == record["id"]:
                        self.page_indexes[stage].pop(stage_key, None)
            elif kind == "audio":
                current = self.audio_index.get(record["audio_key"])
                if current and current.get("version_id") == record["id"]:
                    self.audio_index.pop(record["audio_key"], None)

    def _persist_page(self, record):
        json_body = canonical_json(record["sentences"]).encode("utf-8")
        sentences_sha = digest_bytes(json_body)
        self._write_blob("json", sentences_sha, ".json", json_body)
        if record["input_body"] is not None:
            suffix = ".jpg" if record["input_kind"] == "image" else ".txt"
            self._write_blob("input", record["input_sha"], suffix, record["input_body"])
        with self._database() as db:
            db.execute(
                """INSERT INTO page_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record["id"], record["created"], record["input_kind"], record["input_sha"],
                 record["image_key"], record["ocr_key"], record["text_key"], sentences_sha,
                 record["source_version"], record["match_stage"], int(record["promote"]),
                 "complete"),
            )
            for stage, stage_key in record["activated"]:
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
                "INSERT INTO audio_versions VALUES(?,?,?,?,?,?,?,?)",
                (record["id"], record["created"], record["audio_key"], audio_sha,
                 audit_sha, record["source_page_version"], int(record["promote"]), "complete"),
            )
            if record["activated"]:
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
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (record["id"], record["created"], record["input_kind"], record["input_sha"],
                 record["match_stage"], record["source_version"], record["result_version"],
                 int(record["reuse"]), int(record["promote"]), record["status"]),
            )

    def list_versions(self, limit=50):
        self.flush()
        limit = max(1, min(int(limit), 200))
        with self._database() as db:
            db.row_factory = sqlite3.Row
            pages = [dict(row) for row in db.execute(
                """SELECT id,created,input_kind,input_sha,source_version,match_stage,promote,status
                   FROM page_versions ORDER BY created DESC LIMIT ?""", (limit,)
            )]
            runs = [dict(row) for row in db.execute(
                "SELECT * FROM runs ORDER BY created DESC LIMIT ?", (limit,)
            )]
        return {"pages": pages, "runs": runs, "writer_errors": list(self.errors)}

    def flush(self):
        self.jobs.join()

    def close(self):
        if self.closed:
            return
        self.flush()
        self.closed = True
        self.jobs.put(None)
        self.worker.join(timeout=5)
