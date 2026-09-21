#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# Role: Verifies runtime-input Image Viewer block behavior.
# File Name: F5.27_image_viewer_block.py
# Author: Alexandre EL
# Email: alex@hackinvent.com
# Created Date: 2026-06-05
# -----------------------------------------------------------------------------

"""F5.27 - Image Viewer block.

The test covers the autonomous `image_viewer` block as a pure viewer fed only
by its input port, in centralized and zeromq_active.
"""

# Test cases:
# - FB1/FB3 - Keep Image Viewer source-free in durable config and expose a non-failing empty state without runtime input.
# - FB2/FB5 - Accept path, base64, HTTP(S) URL, and data URI inputs and materialize bounded viewer artifacts.
# - FB4 - Reject invalid, unsupported, inaccessible, missing, non-HTTP(S), and non-loadable URL sources.
# - FB6 - Render thumbnail, inspector, and modal from block-owned templates without persistent display options.

from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import tempfile

from ui_smoke_common import (
    create_run_api,
    data_edge,
    expect,
    graph_payload,
    http_json,
    isolated_server,
    text_node,
    wait_for_run_terminal,
)
from urllib.parse import quote
from block_test_packages import install_test_package, release_key, surface_payload


ROOT = Path(__file__).resolve().parents[3]
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")
PNG_DATA_URI = f"data:image/png;base64,{PNG_BASE64}"


def image_viewer_node(node_id: str, config: dict | None = None) -> dict:
    return {
        "id": node_id,
        "kind": "image_viewer",
        "title": "Image Viewer",
        "position": {"x": 420, "y": 120},
        "inputs": [
            {
                "id": 1,
                "name": "image",
                "title": "Image",
                "accepts": ["image/path", "file/path", "message/*", "text/plain"],
                "multiplicity": "one",
                "required": False,
            }
        ],
        "outputs": [],
        "config": dict(config or {}),
    }


def write_sample_image(root_dir: Path) -> str:
    target = root_dir / "exports" / "images" / "viewer-sample.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(PNG_BYTES)
    return "exports/images/viewer-sample.png"


def write_unsupported_file(root_dir: Path) -> str:
    target = root_dir / "exports" / "images" / "not-image.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not an image", encoding="utf-8")
    return "exports/images/not-image.txt"


def runtime_payload(image_path: str) -> dict:
    return {
        "result": {
            "status": "success",
            "last_message": "Local image ready.",
            "image_viewer": {
                "status": "success",
                "message": "Local image ready.",
                "source_kind": "path",
                "mime_type": "image/png",
                "byte_size": len(PNG_BYTES),
                "viewer_path": image_path,
            },
        }
    }


def render_contract_checks(server, image_path: str) -> None:
    model = json.loads((ROOT / "blocs/image_viewer/model.json").read_text(encoding="utf-8"))
    expect(model.get("config") == {}, "image_viewer must not declare persistent config.")

    card = surface_payload(server, model, image_viewer_node("viewer-card"), "node_card",
                             runtime=runtime_payload(image_path))
    card_html = str(card.get("html") or "")
    card_assets = card.get("assets") or []
    expect("data-image-viewer-node-card" in card_html, "The image_viewer card must be rendered by the block.")
    expect("<h3" not in card_html and "node-type-pill" not in card_html, "The image_viewer card must not show a title or a label.")
    expect("/api/project-image?path=exports/images/viewer-sample.png" in card_html, "The thumbnail must use the runtime image.")

    modal = surface_payload(server, model, image_viewer_node("viewer-modal"), "modal",
                             runtime=runtime_payload(image_path))
    modal_html = str(modal.get("html") or "")
    modal_assets = modal.get("assets") or []
    expect("data-image-viewer-modal-root" in modal_html, "The image_viewer modal must be rendered by the block.")
    expect('data-block-runtime-refresh="autonomous"' in modal_html, "The image_viewer modal must own its runtime refresh.")
    expect("data-image-viewer-source" not in modal_html, "The modal must not expose a persistent source field.")
    expect("data-block-config-field" not in modal_html, "The modal must not expose a persistent option.")
    expect("image_viewer_update_source" not in modal_html, "The modal must not expose a source persistence action.")
    expect("data-image-viewer-zoom-in" in modal_html, "The modal must offer zooming in.")
    expect("data-image-viewer-zoom-out" in modal_html, "The modal must offer zooming out.")
    expect("data-image-viewer-fit" in modal_html, "The modal must offer going back to fit.")
    expect("data-image-viewer-actual" in modal_html, "The modal must offer the 100% view.")

    inspector = surface_payload(server, model, image_viewer_node("viewer-inspector"), "inspector_panel",
                             runtime=runtime_payload(image_path))
    inspector_html = str(inspector.get("html") or "")
    inspector_assets = inspector.get("assets") or []
    expect("data-image-viewer-source" not in inspector_html, "The inspector must not expose a persistent source field.")
    expect("data-block-config-field" not in inspector_html, "The inspector must not expose a persistent option.")
    expect("Ports" in inspector_html, "The inspector must keep the generic Ports tab.")


def run_empty_state_case(server, image_path: str, runtime_mode: str) -> None:
    document = graph_payload(
        f"F5 Image Viewer empty {runtime_mode}",
        [image_viewer_node("viewer-1", config={"source": image_path, "zoom": 4, "fit": "cover"})],
        [],
    )
    created = create_run_api(server, document, runtime_mode=runtime_mode)
    run = wait_for_run_terminal(server, str(created.get("run_id") or ""))
    expect(run.get("status") == "success", f"An empty image_viewer must stay successful in {runtime_mode}.")
    result = run.get("results", {}).get("viewer-1", {})
    viewer = result.get("image_viewer") if isinstance(result.get("image_viewer"), dict) else {}
    expect(viewer.get("status") == "empty", f"The empty state must be explicit in {runtime_mode}: {viewer}")
    expect(not viewer.get("viewer_path"), "A config source must not be used to display an image.")
    expect(image_path not in str(result.get("image_viewer") or {}), "The result must not persist the configured source.")
    expect("zoom" not in str(result.get("image_viewer") or {}), "The result must not persist any display option.")


def run_input_source_case(server, source: str, runtime_mode: str, expected_kind: str) -> dict:
    document = graph_payload(
        f"F5 Image Viewer {expected_kind} {runtime_mode}",
        [
            text_node("text-1", "Source image", source, 80, 120),
            image_viewer_node("viewer-1"),
        ],
        [data_edge("edge-text-viewer", "text-1", 1, "viewer-1", 1)],
    )
    created = create_run_api(server, document, runtime_mode=runtime_mode)
    run = wait_for_run_terminal(server, str(created.get("run_id") or ""))
    expect(run.get("status") == "success", f"image_viewer must succeed in {runtime_mode} with {expected_kind}.")
    result = run.get("results", {}).get("viewer-1", {})
    viewer = result.get("image_viewer") if isinstance(result.get("image_viewer"), dict) else {}
    expect(viewer.get("source_kind") == expected_kind, f"source_kind inattendu en {runtime_mode}: {viewer}")
    expect(result.get("content_type") == "image/path", f"content_type image_viewer incorrect en {runtime_mode}.")
    expect("outputs" in result and result.get("outputs") == {}, "image_viewer must not emit an output.")
    expect(PNG_BASE64 not in str(result), "image_viewer must not duplicate the base64 blob in its result.")
    expect(not any(str(key).startswith("viewer-1:") for key in (run.get("output_values") or {})), "image_viewer must publish no output.")
    if expected_kind in {"base64", "data_uri", "url"}:
        viewer_path = str(viewer.get("viewer_path") or "")
        expect(viewer_path.endswith("viewer-1.png"), f"source not materialized as a runtime path: {viewer}")
    return run


def error_case(server, source: str, expected_fragment: str) -> None:
    document = graph_payload(
        "F5 Image Viewer error",
        [
            text_node("text-1", "Source image", source, 80, 120),
            image_viewer_node("viewer-error"),
        ],
        [data_edge("edge-text-viewer", "text-1", 1, "viewer-error", 1)],
    )
    created = create_run_api(server, document, runtime_mode="centralized")
    run = wait_for_run_terminal(server, str(created.get("run_id") or ""))
    expect(run.get("status") == "failed", f"The invalid source must fail the run: {source[:40]}")
    result = run.get("results", {}).get("viewer-error", {})
    expect(expected_fragment in str(result.get("error") or result.get("last_message") or ""), f"Erreur inattendue: {result}")


def main() -> None:
    with isolated_server() as server:
        # Surfaces are release assets: a bundled kind serves none of them.
        model = install_test_package(server, "image_viewer")
        key = quote(release_key(model), safe="")
        served = lambda payload, suffix: next(
            asset["path"] for asset in payload["assets"] if asset["path"].endswith(suffix))
        image_path = write_sample_image(server.root_dir)
        unsupported_path = write_unsupported_file(server.root_dir)
        image_url = f"{server.base_url}/api/project-image?path={image_path}"
        missing_url = f"{server.base_url}/api/project-image?path=missing.png"

        render_contract_checks(server, image_path)

        for runtime_mode in ("centralized", "zeromq_active"):
            run_empty_state_case(server, image_path, runtime_mode)
            run_input_source_case(server, image_path, runtime_mode, "path")
            run_input_source_case(server, PNG_BASE64, runtime_mode, "base64")
            run_input_source_case(server, image_url, runtime_mode, "url")
            run_input_source_case(server, PNG_DATA_URI, runtime_mode, "data_uri")

        error_case(server, "ftp://example.com/image.png", "schema URL non supporte")
        error_case(server, "https://127.0.0.1:1/image.png", "URL image non chargeable")
        error_case(server, missing_url, "URL image non chargeable")
        error_case(server, "deadbeef" * 8, "hexadecimal")
        error_case(server, "!!!!", "base64 image invalide")
        error_case(server, "not base64 and not a path", "format image non supporte")
        error_case(server, unsupported_path, "format image non supporte")
        error_case(server, "missing.png", "fichier image introuvable")

        outside = Path(tempfile.gettempdir()) / "image-viewer-outside.png"
        outside.write_bytes(PNG_BYTES)
        try:
            error_case(server, str(outside), "outside the workspace")
        finally:
            outside.unlink(missing_ok=True)

        image_block = http_json(
            server.base_url,
            "/api/blocks/image/node-card",
            method="POST",
            payload={"node": {"id": "image-1", "kind": "image", "title": "Image", "config": {}}},
        )
        expect("data-image-node-card" in str(image_block.get("html") or ""), "The existing image block must stay available.")
    print("[ok] F5.27_image_viewer_block")


if __name__ == "__main__":
    sys.exit(main())
