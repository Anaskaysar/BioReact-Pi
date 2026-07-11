"""AI-based color/visual-change detection for the bioreactor camera feed.

Runs entirely on the Pi (no cloud calls): a small pretrained MobileNetV2
image-classification TFLite model is used as a generic visual feature
extractor (its 1001-way ImageNet output vector is a real, network-learned
descriptor of the frame's visual content — turbidity, texture, and color
together — not just an average pixel color). A baseline frame captured at
the start of a run is embedded once; every subsequent frame is compared to
it via cosine distance. That distance is the anomaly score.

This is deliberately NOT a custom-trained "healthy vs. contaminated"
classifier — there is no labeled dataset of bioreactor images to train one,
and there's no time to build one during a hackathon. Reusing a generic
pretrained model as a feature extractor is a standard, well-established
technique that needs zero training data.

Degrades gracefully through two independent failure modes, in this order:
  1. Model unavailable (no internet to download it, or tflite runtime
     missing) -> falls back to a pure-numpy color-histogram distance. Still
     a real, testable distance metric, just not neural-network-derived.
  2. tflite-runtime itself not installed -> same histogram fallback.
Either way, `ColorAnomalyDetector.score()` always returns a float; it never
raises, so a demo never breaks because of this module specifically.

Usage:
    detector = ColorAnomalyDetector()
    detector.set_baseline(first_frame_bgr)          # once, at startup
    anomaly_score = detector.score(latest_frame_bgr)  # every N seconds
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np

# =============================================================================
# Model source — Google's official, long-stable hosted TFLite example model.
# Small (~3.4MB), quantized (uint8), no training required. If this exact URL
# ever moves, override MODEL_URL via env var rather than editing this file.
# =============================================================================

MODEL_URL = os.getenv(
    "COLOR_AI_MODEL_URL",
    "https://storage.googleapis.com/download.tensorflow.org/models/tflite/mobilenet_v2_1.0_224_quant.tgz",
)
MODEL_CACHE_DIR = Path(os.getenv("COLOR_AI_MODEL_DIR", str(Path.home() / ".cache" / "bioreact_pi")))
MODEL_PATH = MODEL_CACHE_DIR / "mobilenet_v2_1.0_224_quant.tflite"

# Frame index in a "mobilenet_v2_1.0_224_quant.tgz" .tflite file inside the
# downloaded tarball — the archive contains a .tflite plus a labels .txt we
# don't need.
_TFLITE_MEMBER_SUFFIX = ".tflite"


def _download_model() -> bool:
    """Download + extract the model into MODEL_CACHE_DIR. Returns success."""
    import tarfile
    import tempfile

    try:
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".tgz") as tmp:
            urllib.request.urlretrieve(MODEL_URL, tmp.name)  # noqa: S310 - fixed, trusted URL
            with tarfile.open(tmp.name, "r:gz") as tar:
                member = next(
                    (m for m in tar.getmembers() if m.name.endswith(_TFLITE_MEMBER_SUFFIX)),
                    None,
                )
                if member is None:
                    return False
                member.name = MODEL_PATH.name  # flatten path, ignore archive dirs
                tar.extract(member, path=MODEL_CACHE_DIR)
        return MODEL_PATH.is_file()
    except Exception as exc:  # noqa: BLE001 - any failure here just means "no model"
        print(f"[color_ai] model download failed ({exc}); falling back to histogram distance")
        return False


def _ensure_model() -> Optional[Path]:
    if MODEL_PATH.is_file():
        return MODEL_PATH
    return MODEL_PATH if _download_model() else None


class ColorAnomalyDetector:
    """Baseline-vs-current visual anomaly score for one camera feed.

    Call `set_baseline()` once with a known-good starting frame, then
    `score()` on subsequent frames. Frames are numpy arrays in BGR order
    (OpenCV's native format), shape (H, W, 3), dtype uint8.
    """

    def __init__(self) -> None:
        self._interpreter = None
        self._input_detail: dict | None = None
        self._output_detail: dict | None = None
        self._baseline_vec: np.ndarray | None = None
        self.model_available = self._try_load_model()

    # -- model loading -------------------------------------------------------

    def _try_load_model(self) -> bool:
        try:
            # tflite_runtime is the small on-device-only package; fall back to
            # the tflite submodule of full tensorflow if that's what's
            # installed instead. Either is fine, we only need Interpreter.
            try:
                from tflite_runtime.interpreter import Interpreter
            except ImportError:
                from tensorflow.lite import Interpreter  # type: ignore[no-redef]

            model_path = _ensure_model()
            if model_path is None:
                return False

            self._interpreter = Interpreter(model_path=str(model_path))
            self._interpreter.allocate_tensors()
            self._input_detail = self._interpreter.get_input_details()[0]
            self._output_detail = self._interpreter.get_output_details()[0]
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[color_ai] TFLite unavailable ({exc}); falling back to histogram distance")
            return False

    # -- embeddings ------------------------------------------------------

    def _embed_tflite(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Run the frame through MobileNetV2, return its (dequantized) output
        vector as a float embedding. Reads shapes/quantization from the
        model itself rather than hardcoding them, so this keeps working if a
        different MobileNet variant (float, different input size) is swapped
        in later."""
        import cv2

        _, in_h, in_w, _ = self._input_detail["shape"]
        resized = cv2.resize(frame_bgr, (in_w, in_h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

        if self._input_detail["dtype"] == np.uint8:
            input_data = rgb.astype(np.uint8)
        else:
            # Float model variant: MobileNetV2's usual [-1, 1] preprocessing.
            input_data = (rgb.astype(np.float32) / 127.5) - 1.0

        self._interpreter.set_tensor(self._input_detail["index"], input_data[np.newaxis, ...])
        self._interpreter.invoke()
        raw = self._interpreter.get_tensor(self._output_detail["index"])[0]

        scale, zero_point = self._output_detail.get("quantization", (0.0, 0))
        if scale:
            raw = (raw.astype(np.float32) - zero_point) * scale

        vec = raw.astype(np.float32)
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def _embed_histogram(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Fallback: a coarse per-channel color histogram, normalized to a
        unit vector so the same cosine-distance code path works either way.
        No TFLite, no model file, no network — pure numpy."""
        hist = np.concatenate(
            [np.bincount(frame_bgr[:, :, c].ravel(), minlength=256) for c in range(3)]
        ).astype(np.float32)
        norm = np.linalg.norm(hist)
        return hist / norm if norm > 0 else hist

    def _embed(self, frame_bgr: np.ndarray) -> np.ndarray:
        if self.model_available:
            try:
                return self._embed_tflite(frame_bgr)
            except Exception as exc:  # noqa: BLE001 - never let a bad frame kill the loop
                print(f"[color_ai] inference failed on this frame ({exc}); using histogram instead")
        return self._embed_histogram(frame_bgr)

    # -- public API ------------------------------------------------------

    def set_baseline(self, frame_bgr: np.ndarray) -> None:
        """Capture the reference frame a run's anomaly score is measured against."""
        self._baseline_vec = self._embed(frame_bgr)

    def score(self, frame_bgr: np.ndarray) -> float:
        """Cosine distance (0 = identical, larger = more different) between
        the current frame and the stored baseline. Returns 0.0 if no
        baseline has been set yet — never raises."""
        if self._baseline_vec is None:
            return 0.0
        current = self._embed(frame_bgr)
        similarity = float(np.dot(self._baseline_vec, current))
        # Both vectors are unit-normalized, so similarity is already in
        # roughly [-1, 1]; clip defensively against float drift.
        similarity = max(-1.0, min(1.0, similarity))
        return 1.0 - similarity
