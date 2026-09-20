# -----------------------------------------------------------------------------
# Role: Implements the Image Viewer block runtime and UI contract.
# File Name: block.py
# Author: Alexandre EL
# Email: alex@hackinvent.com
# Created Date: 2026-06-05
# -----------------------------------------------------------------------------

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from html import escape
import mimetypes
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from bloxsmith_app.block_api import (
    BlockDefinition,
    BlockRuntimeContext,
    BlockRuntimeResult,
    configured_runs_dir,
    IMAGE_PATH,
    render_inspector_template,
    render_node_card_template,
    TEXT_PLAIN,
)


DEFAULT_MAX_BYTES = 2 * 1024 * 1024
URL_TIMEOUT_SEC = 8.0
SUPPORTED_MIME_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
DATA_URI_RE = re.compile(
    r"^data:(image/[A-Za-z0-9.+-]+)(?:;[^,]*)?;base64,(.*)$",
    re.IGNORECASE | re.DOTALL,
)
HEX_BLOB_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{32,}$")


class ImageViewerSourceError(ValueError):
    """Raised when an Image Viewer input source is unsafe or unsupported."""


@dataclass(frozen=True, slots=True)
class ImageViewerState:
    """Resolved image state used by runtime and block-owned UI renderers."""

    status: str
    message: str
    source_kind: str = ""
    mime_type: str = ""
    byte_size: int = 0
    display_path: str = ""
    image_src: str = ""


# Functional behavior:
# FB1 - Consume only the runtime input image source; no durable source config is used.
# FB2 - Accept workspace-local image paths, raw base64 images, HTTP(S) URLs, and data URIs.
# FB3 - Expose an empty viewer state when no input is received, without failing the workflow.
# FB4 - Convert invalid, unreadable, inaccessible, oversized, or unsupported inputs into explicit errors.
# FB5 - Materialize inline and URL images as bounded run artifacts for UI rendering.
# FB6 - Render block-owned node card, inspector, and modal with temporary non-persisted zoom controls.
class ImageViewerBlock(BlockDefinition):
    """Autonomous visual sink block for runtime-provided image sources."""

    kind = "image_viewer"

    def execute_runtime(self, context: BlockRuntimeContext) -> BlockRuntimeResult:
        """Resolve the runtime-provided image source into viewer metadata.

        Args:
            context: Runtime context populated by centralized or ZeroMQ active execution.

        Returns:
            Runtime result with no outputs. Successful image results expose a
            workspace-relative viewer path in metadata. Missing input returns a
            successful empty state so the workflow can continue.
        """

        source = self._runtime_source(context)
        if not source:
            state = ImageViewerState(status="empty", message="Aucune image recue.")
            return BlockRuntimeResult(
                status="success",
                outputs=[],
                logs=[f"[image-viewer] {context.node_id}: aucune entree image recue."],
                last_message=state.message,
                content_type=TEXT_PLAIN,
                worker_received=state.message,
                metadata={"image_viewer": self._state_metadata(state)},
            )

        try:
            state = self._resolve_source_value(
                source,
                root_dir=context.root_dir,
                run_dir=context.run_dir,
                node_id=context.node_id,
            )
        except ImageViewerSourceError as exc:
            message = str(exc)
            return BlockRuntimeResult(
                status="failed",
                outputs=[],
                logs=[f"[image-viewer-error] {context.node_id}: {message}"],
                error=message,
                exit_code=1,
                last_message=message,
                content_type=TEXT_PLAIN,
                worker_received=message,
                metadata={"image_viewer": {"status": "failed", "message": message}},
            )

        return BlockRuntimeResult(
            status="success",
            outputs=[],
            logs=[
                f"[image-viewer] {context.node_id}: {state.source_kind} {state.mime_type} "
                f"({state.byte_size} octet(s))."
            ],
            last_message=state.message,
            content_type=IMAGE_PATH,
            worker_received=state.message,
            metadata={"image_viewer": self._state_metadata(state)},
        )

    def render_node_card(self, *, node: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Render the Image Viewer canvas card from block-owned runtime state.

        Args:
            node: Serialized Image Viewer node.
            payload: Optional runtime UI payload supplied by the editor.

        Returns:
            Block-owned node card payload for the generic canvas shell.
        """

        state = self._resolve_ui_state(node=node, payload=payload or {})
        return render_node_card_template(
            block=self,
            node=node,
            node_classes=["image-viewer-node"],
            replacements={
                "image_src": state.image_src,
                "image_hidden": "hidden" if not state.image_src else "",
                "state_hidden": "hidden" if state.image_src else "",
                "state_text": state.message,
                "state_class": "has-image" if state.image_src else "has-state",
            },
        )

    def render_inspector_panel(self, *, node: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Render the Image Viewer inspector without persistent source controls.

        Args:
            node: Serialized graph node handled by the block.
            payload: Optional UI or runtime payload provided by the framework.
        """

        state = self._resolve_ui_state(node=node, payload=payload or {})
        template = (self.directory / "inspector_panel.html").read_text(encoding="utf-8")
        html = render_inspector_template(
            template=(
                template.replace("{{ image_src }}", escape(state.image_src, quote=True))
                .replace("{{ image_hidden }}", "hidden" if not state.image_src else "")
                .replace("{{ state_hidden }}", "hidden" if state.image_src else "")
                .replace("{{ state_text }}", escape(state.message))
                .replace("{{ source_kind }}", escape(state.source_kind or "-"))
                .replace("{{ mime_type }}", escape(state.mime_type or "-"))
                .replace("{{ byte_size }}", escape(str(state.byte_size) if state.byte_size else "-"))
            ),
            node={**node, "type": self.kind, "kind": self.kind},
            payload=payload,
            show_duplicate=False,
        )
        return {"html": html, "context": {"node_id": str(node.get("id") or ""), "full_panel": True}}

    def render_modal(self, *, node: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Render the Image Viewer modal with temporary zoom controls.

        Args:
            node: Serialized graph node handled by the block.
            payload: Optional runtime payload provided by the framework.

        Returns:
            Block-owned modal HTML. Zoom and pan state live only in the modal JS
            instance and are never returned as node patches or config fields.
        """

        state = self._resolve_ui_state(node=node, payload=payload or {})
        template = (self.directory / "block_modal.html").read_text(encoding="utf-8")
        html = (
            template.replace("{{ title }}", escape(str(node.get("title") or self.default_title())))
            .replace("{{ image_src }}", escape(state.image_src, quote=True))
            .replace("{{ image_hidden }}", "hidden" if not state.image_src else "")
            .replace("{{ state_hidden }}", "hidden" if state.image_src else "")
            .replace("{{ state_text }}", escape(state.message))
            .replace("{{ controls_disabled }}", "" if state.image_src else "disabled")
            .replace("{{ source_kind }}", escape(state.source_kind or "-"))
            .replace("{{ mime_type }}", escape(state.mime_type or "-"))
            .replace("{{ byte_size }}", escape(str(state.byte_size) if state.byte_size else "-"))
        )
        return {
            "html": html,
            "context": {
                "node_id": str(node.get("id") or ""),
                "has_image": bool(state.image_src),
                "source_kind": state.source_kind,
            },
        }

    def _runtime_source(self, context: BlockRuntimeContext) -> str:
        """Return the source received on the runtime input port, if any."""

        for key in ("image", "1"):
            value = str(context.input_value(key) or "").strip()
            if value:
                return value
        message = str(context.input_message or "").strip()
        if message:
            return message
        return str(context.first_input_value(default="") or "").strip()

    def _resolve_source_value(
        self,
        source: str,
        *,
        root_dir: Path,
        run_dir: Path | None,
        node_id: str,
    ) -> ImageViewerState:
        """Resolve one runtime image source into a UI-safe viewer state.

        Args:
            source: Raw path, URL, base64 value, or data URI received at runtime.
            root_dir: Workspace root used to constrain local paths and artifacts.
            run_dir: Optional run directory used for temporary image artifacts.
            node_id: Node id used in materialized filenames.

        Returns:
            Resolved image state.

        Raises:
            ImageViewerSourceError: If the source cannot be accepted.
        """

        cleaned = str(source or "").strip()
        if not cleaned:
            raise ImageViewerSourceError("source image absente.")
        lowered = cleaned.lower()
        parsed = urlparse(cleaned)
        if parsed.scheme and parsed.scheme.lower() not in {"http", "https", "data"}:
            raise ImageViewerSourceError("schema URL non supporte.")
        if lowered.startswith(("http://", "https://")):
            return self._resolve_http_url(cleaned, root_dir=root_dir, run_dir=run_dir, node_id=node_id)
        if lowered.startswith("data:"):
            return self._resolve_data_uri(cleaned, root_dir=root_dir, run_dir=run_dir, node_id=node_id)
        if HEX_BLOB_RE.fullmatch(cleaned):
            raise ImageViewerSourceError("blob hexadecimal brut non supporte.")
        path = self._resolve_local_path(cleaned, root_dir=root_dir)
        if path is not None:
            return self._state_from_path(path, root_dir=root_dir)
        if self._looks_like_local_path(cleaned):
            raise ImageViewerSourceError("fichier image introuvable.")
        return self._resolve_base64(cleaned, root_dir=root_dir, run_dir=run_dir, node_id=node_id)

    def _resolve_http_url(
        self,
        source: str,
        *,
        root_dir: Path,
        run_dir: Path | None,
        node_id: str,
    ) -> ImageViewerState:
        """Download, validate, and materialize an HTTP(S) image source."""

        request = Request(source, headers={"Accept": "image/*", "User-Agent": "BloxSmith-ImageViewer/1"})
        try:
            with urlopen(request, timeout=URL_TIMEOUT_SEC) as response:
                status = int(getattr(response, "status", 200) or 200)
                if status >= 400:
                    raise ImageViewerSourceError("URL image non chargeable.")
                image_bytes = response.read(DEFAULT_MAX_BYTES + 1)
        except ImageViewerSourceError:
            raise
        except HTTPError as exc:
            raise ImageViewerSourceError("URL image non chargeable.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ImageViewerSourceError("URL image non chargeable.") from exc

        if not image_bytes:
            raise ImageViewerSourceError("URL image vide.")
        if len(image_bytes) > DEFAULT_MAX_BYTES:
            raise ImageViewerSourceError("URL image trop volumineuse.")
        mime_type = self._detect_mime_from_bytes(image_bytes)
        return self._state_from_inline_bytes(
            image_bytes,
            mime_type=mime_type,
            root_dir=root_dir,
            run_dir=run_dir,
            node_id=node_id,
            source_kind="url",
        )

    def _resolve_data_uri(
        self,
        source: str,
        *,
        root_dir: Path,
        run_dir: Path | None,
        node_id: str,
    ) -> ImageViewerState:
        """Decode, validate, and materialize a base64 data URI image source."""

        match = DATA_URI_RE.match(source)
        if not match:
            raise ImageViewerSourceError("data URI image invalide.")
        self._normalize_mime_type(match.group(1))
        image_bytes = self._decode_base64_bytes(match.group(2), label="data URI")
        mime_type = self._detect_mime_from_bytes(image_bytes)
        return self._state_from_inline_bytes(
            image_bytes,
            mime_type=mime_type,
            root_dir=root_dir,
            run_dir=run_dir,
            node_id=node_id,
            source_kind="data_uri",
        )

    def _resolve_base64(
        self,
        source: str,
        *,
        root_dir: Path,
        run_dir: Path | None,
        node_id: str,
    ) -> ImageViewerState:
        """Decode, validate, and materialize a raw base64 image source."""

        image_bytes = self._decode_base64_bytes(source, label="base64")
        mime_type = self._detect_mime_from_bytes(image_bytes)
        return self._state_from_inline_bytes(
            image_bytes,
            mime_type=mime_type,
            root_dir=root_dir,
            run_dir=run_dir,
            node_id=node_id,
            source_kind="base64",
        )

    def _state_from_inline_bytes(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        root_dir: Path,
        run_dir: Path | None,
        node_id: str,
        source_kind: str,
    ) -> ImageViewerState:
        """Build state for decoded image bytes materialized under the workspace."""

        target = self._materialize_inline_image(
            image_bytes,
            mime_type=mime_type,
            root_dir=root_dir,
            run_dir=run_dir or configured_runs_dir(root_dir),
            node_id=node_id,
        )
        display_path = self._relative_path(root_dir, target)
        byte_size = len(image_bytes)
        return ImageViewerState(
            status="success",
            message=f"Image {source_kind} prete ({byte_size} octet(s)).",
            source_kind=source_kind,
            mime_type=mime_type,
            byte_size=byte_size,
            display_path=display_path,
            image_src=self._project_image_url(display_path),
        )

    def _state_from_path(self, path: Path, *, root_dir: Path) -> ImageViewerState:
        """Validate a workspace-local image path and build display state."""

        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ImageViewerSourceError(f"image illisible: {path}") from exc
        if size <= 0:
            raise ImageViewerSourceError("image vide non supportee.")
        if size > DEFAULT_MAX_BYTES:
            raise ImageViewerSourceError("image trop volumineuse.")
        try:
            with path.open("rb") as handle:
                head = handle.read(1024)
        except OSError as exc:
            raise ImageViewerSourceError(f"image illisible: {path}") from exc
        guessed = mimetypes.guess_type(str(path))[0] or ""
        mime_type = self._detect_mime_from_bytes(head)
        if guessed:
            self._normalize_mime_type(guessed)
        display_path = self._relative_path(root_dir, path)
        return ImageViewerState(
            status="success",
            message=f"Image locale prete ({size} octet(s)).",
            source_kind="path",
            mime_type=mime_type,
            byte_size=size,
            display_path=display_path,
            image_src=self._project_image_url(display_path),
        )

    def _resolve_local_path(self, source: str, *, root_dir: Path) -> Path | None:
        """Return a safe workspace-local file path, or None when not path-like."""

        if "\n" in source or "\r" in source:
            return None
        if re.search(r"\s", source) and not Path(source).exists():
            return None
        candidate = Path(source).expanduser()
        path = candidate if candidate.is_absolute() else root_dir / candidate
        try:
            resolved = path.resolve()
            root = root_dir.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            if candidate.is_absolute():
                raise ImageViewerSourceError("local path outside the workspace is not allowed.") from exc
            return None
        if not resolved.exists():
            return None
        if not resolved.is_file():
            raise ImageViewerSourceError("local source is not a file.")
        return resolved

    def _looks_like_local_path(self, source: str) -> bool:
        """Return whether a non-existing value should be reported as a missing file."""

        if "\n" in source or "\r" in source:
            return False
        suffix = Path(source).suffix.lower()
        return suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

    def _decode_base64_bytes(self, source: str, *, label: str) -> bytes:
        """Decode base64 image bytes with strict validation and size limits.

        Args:
            source: Raw base64 payload to decode.
            label: Human-readable source label used in error messages.
        """

        try:
            image_bytes = base64.b64decode("".join(str(source or "").split()), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageViewerSourceError(f"{label} image invalide.") from exc
        if not image_bytes:
            raise ImageViewerSourceError(f"image {label} vide.")
        if len(image_bytes) > DEFAULT_MAX_BYTES:
            raise ImageViewerSourceError(f"image {label} trop volumineuse.")
        return image_bytes

    def _detect_mime_from_bytes(self, image_bytes: bytes) -> str:
        """Infer a supported image MIME type from decoded bytes."""

        head = image_bytes[:64]
        stripped = image_bytes[:1024].lstrip()
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if head.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if head.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image/webp"
        if stripped.startswith(b"<svg") or stripped.startswith(b"<?xml"):
            text_head = stripped[:1024].decode("utf-8", errors="ignore").lower()
            if "<svg" in text_head:
                return "image/svg+xml"
        raise ImageViewerSourceError("format image non supporte.")

    def _normalize_mime_type(self, mime_type: str) -> str:
        """Return a supported canonical image MIME type or raise an error."""

        normalized = str(mime_type or "").strip().lower()
        if normalized == "image/jpg":
            normalized = "image/jpeg"
        if normalized not in SUPPORTED_MIME_TYPES:
            raise ImageViewerSourceError("type MIME image non supporte.")
        return normalized

    def _materialize_inline_image(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        root_dir: Path,
        run_dir: Path,
        node_id: str,
    ) -> Path:
        """Write runtime image bytes to a run-local artifact under the workspace."""

        run_root = (run_dir / "image_viewer").resolve()
        root = root_dir.resolve()
        try:
            run_root.relative_to(root)
        except ValueError:
            run_root = (root / "user" / "runs" / "image_viewer").resolve()
        run_root.mkdir(parents=True, exist_ok=True)
        safe_node_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(node_id or self.kind)).strip("_") or self.kind
        target = run_root / f"{safe_node_id}{SUPPORTED_MIME_TYPES[mime_type]}"
        target.write_bytes(image_bytes)
        return target.resolve()

    def _resolve_ui_state(self, *, node: dict[str, Any], payload: dict[str, Any]) -> ImageViewerState:
        """Resolve the image state shown by node cards, inspector, and modal."""

        runtime_state = self._runtime_viewer_state(payload, node)
        if runtime_state is not None:
            return runtime_state
        return ImageViewerState(status="empty", message="Aucune image recue.")

    def _runtime_viewer_state(self, payload: dict[str, Any], node: dict[str, Any]) -> ImageViewerState | None:
        """Return image state from generic runtime metadata when available."""

        runtime = payload.get("runtime") if isinstance(payload, dict) else None
        if not isinstance(runtime, dict):
            runtime = node.get("runtimeUi") if isinstance(node.get("runtimeUi"), dict) else None
        result = runtime.get("result") if isinstance(runtime, dict) else None
        if not isinstance(result, dict):
            return None
        viewer = result.get("image_viewer")
        if not isinstance(viewer, dict):
            return None
        status = str(viewer.get("status") or "").strip() or "success"
        message = str(viewer.get("message") or result.get("last_message") or "").strip()
        display_path = str(viewer.get("viewer_path") or "").strip()
        image_src = self._project_image_url(display_path) if display_path and status == "success" else ""
        return ImageViewerState(
            status=status,
            message=message or ("Image prete." if image_src else "Aucune image recue."),
            source_kind=str(viewer.get("source_kind") or ""),
            mime_type=str(viewer.get("mime_type") or ""),
            byte_size=int(viewer.get("byte_size") or 0),
            display_path=display_path,
            image_src=image_src,
        )

    def _state_metadata(self, state: ImageViewerState) -> dict[str, Any]:
        """Return bounded metadata safe for run-state persistence."""

        return {
            "status": state.status,
            "message": state.message,
            "source_kind": state.source_kind,
            "mime_type": state.mime_type,
            "byte_size": state.byte_size,
            "viewer_path": state.display_path,
        }

    def _project_image_url(self, path: str) -> str:
        """Build the existing project image endpoint URL for a workspace path."""

        cleaned = str(path or "").strip()
        return f"/api/project-image?path={quote(cleaned)}" if cleaned else ""

    def _relative_path(self, root_dir: Path, path: Path) -> str:
        """Return a workspace-relative POSIX path when possible."""

        try:
            return path.resolve().relative_to(root_dir.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()
