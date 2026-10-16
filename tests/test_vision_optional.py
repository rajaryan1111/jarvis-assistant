"""vision.py backend selection and gesture classification, using stand-in modules (no camera/ML)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import vision


class P:
    def __init__(self, y: float) -> None:
        self.y = y


def _hand(extended: int):
    landmarks = [P(0.5) for _ in range(21)]
    for i, (tip, pip) in enumerate(zip(vision.FINGER_TIPS, vision.FINGER_PIPS, strict=True)):
        landmarks[tip], landmarks[pip] = (P(0.2), P(0.4)) if i < extended else (P(0.6), P(0.4))
    return landmarks


def test_classify_hand():
    assert vision.classify_hand(_hand(4)) == "open_palm"
    assert vision.classify_hand(_hand(3)) == "open_palm"
    assert vision.classify_hand(_hand(1)) == "unknown"


def test_no_backend_without_dependencies(monkeypatch):
    monkeypatch.setattr(vision, "cv2", None)
    monkeypatch.setattr(vision, "mp", None)
    assert vision.hands_backend() is None
    assert vision.detect_hand_gesture() == "unavailable"
    assert not vision.face_available()
    assert vision.register_face("x") is False
    assert vision.recognize_face("x") == (False, None)
    assert "uv sync --extra vision" in vision.unavailable_message()


def test_tasks_backend_requires_model_file(monkeypatch, tmp_path):
    """MediaPipe >= 0.10.30 has no mp.solutions; the Tasks API needs hand_landmarker.task."""
    monkeypatch.setattr(vision, "cv2", SimpleNamespace())
    monkeypatch.setattr(vision, "mp", SimpleNamespace(tasks=SimpleNamespace(), Image=object))
    monkeypatch.delenv(vision.HAND_MODEL_ENV, raising=False)
    assert vision.hands_backend() is None
    assert vision.HAND_MODEL_ENV in vision.unavailable_message(hands=True)
    monkeypatch.setenv(vision.HAND_MODEL_ENV, str(tmp_path / "missing.task"))
    assert vision.hands_backend() is None
    model = tmp_path / "hand_landmarker.task"
    model.write_bytes(b"model")
    monkeypatch.setenv(vision.HAND_MODEL_ENV, str(model))
    assert vision.hands_backend() == "tasks"
    assert vision.status()["hand_model_configured"] is True


def test_legacy_backend_used_only_when_old_mediapipe_provides_it(monkeypatch):
    monkeypatch.setattr(vision, "cv2", SimpleNamespace())
    monkeypatch.setattr(vision, "mp", SimpleNamespace(solutions=SimpleNamespace(hands=object())))
    monkeypatch.delenv(vision.HAND_MODEL_ENV, raising=False)
    assert vision.hands_backend() == "legacy"


def test_gesture_detection_contains_backend_errors(monkeypatch, tmp_path):
    model = tmp_path / "hand_landmarker.task"
    model.write_bytes(b"model")
    monkeypatch.setenv(vision.HAND_MODEL_ENV, str(model))
    monkeypatch.setattr(vision, "cv2", SimpleNamespace())
    monkeypatch.setattr(vision, "mp", SimpleNamespace(tasks=SimpleNamespace(), Image=object))

    def broken(max_attempts):
        raise OSError("native library missing")
        yield  # pragma: no cover

    monkeypatch.setattr(vision, "_hands_from_tasks", broken)
    assert vision.detect_hand_gesture() == "unknown"


def test_gesture_detection_uses_first_open_palm(monkeypatch, tmp_path):
    model = tmp_path / "hand_landmarker.task"
    model.write_bytes(b"model")
    monkeypatch.setenv(vision.HAND_MODEL_ENV, str(model))
    monkeypatch.setattr(vision, "cv2", SimpleNamespace())
    monkeypatch.setattr(vision, "mp", SimpleNamespace(tasks=SimpleNamespace(), Image=object))
    monkeypatch.setattr(vision, "_hands_from_tasks", lambda n: iter([None, _hand(1), _hand(4)]))
    assert vision.detect_hand_gesture() == "open_palm"
    monkeypatch.setattr(vision, "_hands_from_tasks", lambda n: iter([None, None]))
    assert vision.detect_hand_gesture() == "no_hand"


def test_importing_vision_creates_no_directories(tmp_path, monkeypatch):
    import importlib

    monkeypatch.chdir(tmp_path)
    importlib.reload(vision)
    assert not (tmp_path / "faces").exists()


# ============================================================ camera capture
class _FakeCap:
    """Stand-in webcam: records release() and can be told to fail mid-read."""

    def __init__(self, frames=(), fail_on_read=False):
        self._frames, self._fail = list(frames), fail_on_read
        self.opened, self.released = True, 0

    def isOpened(self):
        return self.opened

    def read(self):
        if self._fail:
            raise RuntimeError("camera unplugged")
        return (True, self._frames.pop(0)) if self._frames else (False, None)

    def release(self):
        self.released += 1
        self.opened = False


def _install_camera(monkeypatch, cap):
    monkeypatch.setattr(vision, "cv2", SimpleNamespace(VideoCapture=lambda index: cap))
    return cap


def test_camera_frame_is_captured_and_the_handle_released(monkeypatch):
    cap = _install_camera(monkeypatch, _FakeCap(frames=["frame"]))
    assert vision._capture_frame_from_camera(timeout_sec=0.1) == "frame"
    assert cap.released == 1


def test_camera_handle_is_released_even_when_a_read_raises(monkeypatch):
    """A webcam that raises mid-read must not leak the capture handle.

    The loop used to call cap.release() only on the normal path, so an exception
    from read() skipped it and left the device open.
    """
    cap = _install_camera(monkeypatch, _FakeCap(fail_on_read=True))
    with pytest.raises(RuntimeError):
        vision._capture_frame_from_camera(timeout_sec=0.1)
    assert cap.released == 1, "the camera handle must be released on the failure path too"


def test_camera_that_never_opens_is_reported_not_retried(monkeypatch):
    class Closed(_FakeCap):
        def isOpened(self):
            return False

    cap = _install_camera(monkeypatch, Closed())
    assert vision._capture_frame_from_camera(timeout_sec=0.1) is None
    assert cap.released == 0  # nothing was opened, so nothing to release
