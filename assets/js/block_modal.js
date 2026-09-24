import { withProperties } from "./properties.js";

/**
 * Clamp a numeric value between inclusive bounds.
 *
 * @param {number} value - Candidate value.
 * @param {number} min - Minimum accepted value.
 * @param {number} max - Maximum accepted value.
 * @returns {number} Clamped value.
 */
function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

/**
 * Apply the current temporary transform state to the modal image.
 *
 * @param {HTMLImageElement} image - Image controlled by the viewer.
 * @param {object} state - Zoom and pan state local to the mounted modal.
 * @returns {void}
 */
function applyTransform(image, state) {
  image.style.transform = `translate(${state.x}px, ${state.y}px) scale(${state.scale})`;
}

/**
 * Reset the image to the fitted view for the current modal session.
 *
 * @param {HTMLImageElement} image - Image controlled by the viewer.
 * @param {object} state - Mutable transform state.
 * @returns {void}
 */
function fit(image, state) {
  state.scale = 1;
  state.x = 0;
  state.y = 0;
  applyTransform(image, state);
}

/**
 * Change zoom while bounding pan to useful values.
 *
 * @param {HTMLImageElement} image - Image controlled by the viewer.
 * @param {object} state - Mutable transform state.
 * @param {number} nextScale - Requested zoom scale.
 * @returns {void}
 */
function setScale(image, state, nextScale) {
  state.scale = clamp(nextScale, 0.25, 8);
  if (state.scale <= 1) {
    state.x = 0;
    state.y = 0;
  }
  applyTransform(image, state);
}

/**
 * Bind zoom buttons and pointer drag behavior for one modal instance.
 *
 * @param {HTMLElement} root - Mounted Image Viewer modal root.
 */
function mountModal(root) {
  const stage = root.querySelector("[data-image-viewer-stage]");
  const image = root.querySelector("[data-image-viewer-image]");
  if (!(stage instanceof HTMLElement) || !(image instanceof HTMLImageElement) || !image.getAttribute("src")) {
    return;
  }

  const state = { scale: 1, x: 0, y: 0, drag: null };
  const zoomIn = root.querySelector("[data-image-viewer-zoom-in]");
  const zoomOut = root.querySelector("[data-image-viewer-zoom-out]");
  const fitButton = root.querySelector("[data-image-viewer-fit]");
  const actualButton = root.querySelector("[data-image-viewer-actual]");

  zoomIn?.addEventListener("click", () => setScale(image, state, state.scale * 1.25));
  zoomOut?.addEventListener("click", () => setScale(image, state, state.scale / 1.25));
  fitButton?.addEventListener("click", () => fit(image, state));
  actualButton?.addEventListener("click", () => setScale(image, state, 1));

  stage.addEventListener("pointerdown", (event) => {
    if (state.scale <= 1) {
      return;
    }
    event.preventDefault();
    stage.setPointerCapture?.(event.pointerId);
    stage.classList.add("is-dragging");
    state.drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: state.x,
      originY: state.y,
    };
  });

  stage.addEventListener("pointermove", (event) => {
    if (!state.drag || state.drag.pointerId !== event.pointerId) {
      return;
    }
    const maxX = Math.max(0, (stage.clientWidth * (state.scale - 1)) / 2);
    const maxY = Math.max(0, (stage.clientHeight * (state.scale - 1)) / 2);
    state.x = clamp(state.drag.originX + event.clientX - state.drag.startX, -maxX, maxX);
    state.y = clamp(state.drag.originY + event.clientY - state.drag.startY, -maxY, maxY);
    applyTransform(image, state);
  });

  const endDrag = (event) => {
    if (!state.drag || state.drag.pointerId !== event.pointerId) {
      return;
    }
    state.drag = null;
    stage.classList.remove("is-dragging");
    stage.releasePointerCapture?.(event.pointerId);
  };

  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);
  image.addEventListener("load", () => fit(image, state), { once: true });
  fit(image, state);
}

/**
 * Mount temporary image zoom and pan controls.
 *
 * @param {HTMLElement} root - Mounted modal root.
 */
function mountOwned(root) {
  mountModal(root);
}

/** Keep the block behavior and add properties-only accessibility. */
export function mount(root, ...args) {
  return withProperties(mountOwned).call(this, root, ...args);
}
