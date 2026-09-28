"""Print the committed OpenAPI document: python -m bidoc_library.openapi > docs/openapi-v1.json"""
import json
import sys
import tempfile
from pathlib import Path

from .api import create_app
from .config import Settings


def generate() -> str:
    with tempfile.TemporaryDirectory() as d:
        app = create_app(Settings(local_data_dir=Path(d)), session_secret="unused")
        return json.dumps(app.openapi(), indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    sys.stdout.write(generate())
