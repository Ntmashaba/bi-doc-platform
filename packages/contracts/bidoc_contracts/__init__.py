"""Publication envelope (artifact contract) v1 shared by the generator and the library."""

from .manifest import (MANIFEST_ID, PLACEHOLDER, content_sha256, embed_manifest, locate_manifest,
                       serialize_manifest)
from .validate import ContractError, Limits, load_schema, validate_artifact, validate_manifest

__version__ = "0.1.0"
SCHEMA_VERSION = 1

__all__ = ["MANIFEST_ID", "PLACEHOLDER", "SCHEMA_VERSION", "ContractError", "Limits", "content_sha256",
           "embed_manifest", "load_schema", "locate_manifest", "serialize_manifest", "validate_artifact",
           "validate_manifest"]
