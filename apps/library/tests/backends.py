"""Run API-level test classes against more than one store backend (B10b).

`add_variants(globals(), classes)` defines, next to each class, a copy that uses AzureStore
over in-memory Table/Blob semantics and, when BIDOC_AZURE_TEST_CONNECTION_STRING is set,
a copy over the real SDKs (Azurite in CI). Copies are defined only when they can run, so
the suites never skip.
"""
import os
import uuid

from bidoc_library.azure import AzureBlobs, AzureStore, AzureTables, MemoryBlobs, MemoryTables

AZURE = os.environ.get("BIDOC_AZURE_TEST_CONNECTION_STRING")


class AzureMemoryStore:
    def make_store(self):
        return AzureStore(MemoryTables(), MemoryBlobs())


class AzuriteStore:
    def make_store(self):
        name = "a" + uuid.uuid4().hex[:20]
        self.addCleanup(drop, name)
        return AzureStore(AzureTables.from_connection_string(AZURE, name), AzureBlobs.from_connection_string(AZURE, name))


def drop(name):
    from azure.data.tables import TableServiceClient
    from azure.storage.blob import BlobServiceClient
    TableServiceClient.from_connection_string(AZURE).delete_table(name)
    BlobServiceClient.from_connection_string(AZURE).delete_container(name)


def add_variants(namespace: dict, classes) -> None:
    mixins = [("AzureMemory", AzureMemoryStore)] + ([("Azurite", AzuriteStore)] if AZURE else [])
    for prefix, mixin in mixins:
        for case in classes:
            name = prefix + case.__name__
            namespace[name] = type(name, (mixin, case), {"__module__": namespace["__name__"]})
