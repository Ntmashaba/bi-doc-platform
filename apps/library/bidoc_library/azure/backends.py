"""Table and Blob access for the Azure backend, with the semantics the store relies on.

Two implementations of each:

- Memory*: in-process, used by the fast contract suite. They model exactly the service
  guarantees the design depends on: per-entity ETags, conditional replace/delete,
  create-if-absent, and atomic transactions *within one partition* (entity group
  transactions, at most 100 operations). Nothing crosses partitions atomically.
- Azure*: azure-data-tables / azure-storage-blob against Azure Storage or Azurite.

Operations in a transaction: ("create", entity), ("replace", entity, etag),
("upsert", entity), ("delete", row_key, etag). A failed precondition anywhere rolls back
the whole transaction and raises Conflict.
"""
from __future__ import annotations

import copy
import threading
import uuid

MAX_BATCH = 100


class Conflict(Exception):
    """A precondition failed (entity exists, ETag changed, or missing); nothing was written."""


class NotFound(Exception):
    pass


# ---- tables ------------------------------------------------------------------------------

class MemoryTables:
    def __init__(self):
        self._rows: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()
        self.calls = 0                        # transactions + reads, for cost estimates

    def get(self, partition: str, row: str):
        with self._lock:
            self.calls += 1
            found = self._rows.get((partition, row))
            return copy.deepcopy(found) if found else None

    def query(self, partition: str, prefix: str = ""):
        with self._lock:
            self.calls += 1
            keys = sorted(k for k in self._rows if k[0] == partition and k[1].startswith(prefix))
            return [copy.deepcopy(self._rows[k]) for k in keys]

    def scan(self):
        """Every entity in every partition (backup only)."""
        with self._lock:
            self.calls += 1
            return [copy.deepcopy(self._rows[k]) for k in sorted(self._rows)]

    def transact(self, partition: str, ops: list) -> None:
        if not 0 < len(ops) <= MAX_BATCH:
            raise ValueError("a transaction holds 1 to 100 operations")
        with self._lock:
            self.calls += 1
            staged = dict(self._rows)
            seen = set()
            for op in ops:
                kind = op[0]
                row = op[1]["RowKey"] if kind != "delete" else op[1]
                if row in seen:
                    raise ValueError("an entity can appear only once in a transaction")
                seen.add(row)
                key = (partition, row)
                current = staged.get(key)
                if kind == "create":
                    if current is not None:
                        raise Conflict(f"{row} exists")
                    staged[key] = self._stamp(partition, op[1])
                elif kind == "replace":
                    if current is None or current["etag"] != op[2]:
                        raise Conflict(f"{row} changed")
                    staged[key] = self._stamp(partition, op[1])
                elif kind == "upsert":
                    staged[key] = self._stamp(partition, op[1])
                elif kind == "delete":
                    if current is None or (op[2] is not None and current["etag"] != op[2]):
                        raise Conflict(f"{row} changed")
                    del staged[key]
                else:
                    raise ValueError(kind)
            self._rows = staged

    @staticmethod
    def _stamp(partition, entity):
        e = {k: v for k, v in copy.deepcopy(entity).items() if k != "etag"}
        e["PartitionKey"] = partition
        e["etag"] = uuid.uuid4().hex
        return e


class AzureTables:
    """One Azure table (or Azurite). Entity values are str, int, float or bool."""

    def __init__(self, table_client):
        from azure.core import MatchConditions  # noqa: PLC0415
        self._client = table_client
        self._match = MatchConditions.IfNotModified
        self.calls = 0

    @classmethod
    def from_connection_string(cls, conn: str, table: str):
        from azure.data.tables import TableServiceClient  # noqa: PLC0415
        service = TableServiceClient.from_connection_string(conn)
        return cls(service.create_table_if_not_exists(table))

    @classmethod
    def from_identity(cls, endpoint: str, table: str, credential):
        from azure.data.tables import TableServiceClient  # noqa: PLC0415
        return cls(TableServiceClient(endpoint=endpoint, credential=credential).create_table_if_not_exists(table))

    @staticmethod
    def _plain(entity):
        out = {k: v for k, v in entity.items() if not k.startswith("odata")}
        out["etag"] = entity.metadata["etag"]
        return out

    def get(self, partition, row):
        from azure.core.exceptions import ResourceNotFoundError  # noqa: PLC0415
        self.calls += 1
        try:
            return self._plain(self._client.get_entity(partition, row))
        except ResourceNotFoundError:
            return None

    def query(self, partition, prefix=""):
        self.calls += 1
        f = "PartitionKey eq @p"
        params = {"p": partition}
        if prefix:
            # RowKey range for the prefix: [prefix, prefix + U+FFFF)
            f += " and RowKey ge @lo and RowKey lt @hi"
            params.update(lo=prefix, hi=prefix + "￿")
        return sorted((self._plain(e) for e in self._client.query_entities(f, parameters=params)),
                      key=lambda e: e["RowKey"])

    def scan(self):
        """Every entity in every partition (backup only)."""
        self.calls += 1
        return sorted((self._plain(e) for e in self._client.list_entities()),
                      key=lambda e: (e["PartitionKey"], e["RowKey"]))

    def transact(self, partition, ops):
        from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceModifiedError  # noqa: PLC0415
        from azure.data.tables import TableTransactionError, UpdateMode  # noqa: PLC0415
        if not 0 < len(ops) <= MAX_BATCH:
            raise ValueError("a transaction holds 1 to 100 operations")
        self.calls += 1
        batch = []
        for op in ops:
            kind = op[0]
            if kind == "delete":
                entity = {"PartitionKey": partition, "RowKey": op[1]}
                kw = {"etag": op[2], "match_condition": self._match} if op[2] else {}
                batch.append(("delete", entity, kw))
                continue
            entity = {k: v for k, v in op[1].items() if k != "etag"}
            entity["PartitionKey"] = partition
            if kind == "create":
                batch.append(("create", entity))
            elif kind == "replace":
                batch.append(("update", entity, {"mode": UpdateMode.REPLACE, "etag": op[2],
                                                 "match_condition": self._match}))
            elif kind == "upsert":
                batch.append(("upsert", entity, {"mode": UpdateMode.REPLACE}))
            else:
                raise ValueError(kind)
        try:
            self._client.submit_transaction(batch)
        except (TableTransactionError, ResourceExistsError, ResourceModifiedError) as exc:
            raise Conflict(str(exc)[:300]) from None
        except HttpResponseError as exc:
            if exc.status_code in (404, 409, 412):
                raise Conflict(str(exc)[:300]) from None
            raise


# ---- blobs -------------------------------------------------------------------------------

class MemoryBlobs:
    def __init__(self):
        self._blobs: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self.calls = 0

    def create(self, key: str, data: bytes) -> bool:
        """Create-if-absent. False when a blob already exists (it is never overwritten)."""
        with self._lock:
            self.calls += 1
            if key in self._blobs:
                return False
            self._blobs[key] = bytes(data)
            return True

    def read(self, key: str) -> bytes:
        with self._lock:
            self.calls += 1
            if key not in self._blobs:
                raise NotFound(key)
            return self._blobs[key]

    def delete(self, key: str) -> None:
        with self._lock:
            self.calls += 1
            self._blobs.pop(key, None)

    def list(self, prefix: str = "") -> list[str]:
        with self._lock:
            self.calls += 1
            return sorted(k for k in self._blobs if k.startswith(prefix))

    def put_stream(self, key: str, stream) -> int:
        """Write (or overwrite) a blob from a file object; returns its size."""
        data = stream.read()
        with self._lock:
            self.calls += 1
            self._blobs[key] = bytes(data)
        return len(data)

    def chunks(self, key: str):
        data = self.read(key)
        return iter([data[i:i + (1 << 20)] for i in range(0, len(data), 1 << 20)] or [b""])

    def corrupt(self, key: str) -> None:            # tests only
        self._blobs[key] = self._blobs[key] + b" "


class AzureBlobs:
    def __init__(self, container_client):
        self._c = container_client
        self.calls = 0

    @classmethod
    def from_connection_string(cls, conn: str, container: str):
        from azure.core.exceptions import ResourceExistsError  # noqa: PLC0415
        from azure.storage.blob import BlobServiceClient  # noqa: PLC0415
        c = BlobServiceClient.from_connection_string(conn).get_container_client(container)
        try:
            c.create_container()
        except ResourceExistsError:
            pass
        return cls(c)

    @classmethod
    def from_identity(cls, endpoint: str, container: str, credential):
        from azure.storage.blob import BlobServiceClient  # noqa: PLC0415
        return cls(BlobServiceClient(account_url=endpoint, credential=credential).get_container_client(container))

    def create(self, key, data) -> bool:
        from azure.core.exceptions import ResourceExistsError  # noqa: PLC0415
        self.calls += 1
        try:
            self._c.upload_blob(key, data, overwrite=False)   # If-None-Match: *
            return True
        except ResourceExistsError:
            return False

    def read(self, key) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError  # noqa: PLC0415
        self.calls += 1
        try:
            return self._c.download_blob(key).readall()
        except ResourceNotFoundError:
            raise NotFound(key) from None

    def delete(self, key) -> None:
        from azure.core.exceptions import ResourceNotFoundError  # noqa: PLC0415
        self.calls += 1
        try:
            self._c.delete_blob(key)
        except ResourceNotFoundError:
            pass

    def list(self, prefix="") -> list[str]:
        self.calls += 1
        return sorted(b.name for b in self._c.list_blobs(name_starts_with=prefix))

    def put_stream(self, key, stream) -> int:
        self.calls += 1
        start = stream.tell()
        size = stream.seek(0, 2) - start
        stream.seek(start)
        self._c.upload_blob(key, stream, length=size, overwrite=True, max_concurrency=2)
        return size

    def chunks(self, key):
        from azure.core.exceptions import ResourceNotFoundError  # noqa: PLC0415
        self.calls += 1
        try:
            return self._c.download_blob(key).chunks()
        except ResourceNotFoundError:
            raise NotFound(key) from None

    def corrupt(self, key) -> None:                 # tests only
        data = self.read(key)
        self._c.upload_blob(key, data + b" ", overwrite=True)
