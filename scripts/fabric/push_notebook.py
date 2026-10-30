"""Build create/update request bodies for a Fabric Notebook item from its
local source files, for use with az rest (no Fabric CLI / fab required --
see docs/decisions/0002-fabric-items-via-az-rest.md).

Reads `notebook-content.ipynb` and `.platform` from a
`fabric/<name>.Notebook/` directory (the Git-synced source of truth per
CLAUDE.md's repo structure) and writes two JSON files next to it:
`.push/create_body.json` and `.push/update_body.json`.

Usage:
    uv run python scripts/fabric/push_notebook.py fabric/nb_bronze_bts.Notebook

Then, to create (first time):
    az rest --method POST \\
      --url "https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/notebooks" \\
      --resource "https://api.fabric.microsoft.com" \\
      --headers "Content-Type=application/json" \\
      --body @fabric/nb_bronze_bts.Notebook/.push/create_body.json

Or to update an existing notebook (need its item id, e.g. from
docs/fabric_workspace.md or `az rest ... /items?type=Notebook`):
    az rest --method POST \\
      --url "https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/notebooks/{notebookId}/updateDefinition" \\
      --resource "https://api.fabric.microsoft.com" \\
      --headers "Content-Type=application/json" \\
      --body @fabric/nb_bronze_bts.Notebook/.push/update_body.json

To run it and poll:
    az rest --method POST \\
      --url "https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}/jobs/RunNotebook/instances" \\
      --resource "https://api.fabric.microsoft.com"
    az rest --method GET \\
      --url "https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}/jobs/instances/{jobInstanceId}" \\
      --resource "https://api.fabric.microsoft.com"
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path


def build(notebook_dir: Path) -> None:
    ipynb_path = notebook_dir / "notebook-content.ipynb"
    platform_path = notebook_dir / ".platform"
    if not ipynb_path.exists() or not platform_path.exists():
        raise FileNotFoundError(
            f"expected both {ipynb_path.name} and {platform_path.name} in {notebook_dir}"
        )

    platform = json.loads(platform_path.read_text(encoding="utf-8"))
    display_name = platform["metadata"]["displayName"]
    description = platform["metadata"].get("description", "")

    notebook_b64 = base64.b64encode(ipynb_path.read_bytes()).decode("ascii")
    platform_b64 = base64.b64encode(platform_path.read_bytes()).decode("ascii")

    definition = {
        "format": "ipynb",
        "parts": [
            {"path": "notebook-content.ipynb", "payload": notebook_b64, "payloadType": "InlineBase64"},
            {"path": ".platform", "payload": platform_b64, "payloadType": "InlineBase64"},
        ],
    }

    create_body = {"displayName": display_name, "description": description, "definition": definition}
    update_body = {"definition": definition}

    out_dir = notebook_dir / ".push"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "create_body.json").write_text(json.dumps(create_body), encoding="utf-8")
    (out_dir / "update_body.json").write_text(json.dumps(update_body), encoding="utf-8")

    print(f"displayName: {display_name}")
    print(f"wrote {out_dir / 'create_body.json'}")
    print(f"wrote {out_dir / 'update_body.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notebook_dir", type=Path, help="e.g. fabric/nb_bronze_bts.Notebook")
    args = parser.parse_args()
    build(args.notebook_dir)


if __name__ == "__main__":
    main()
