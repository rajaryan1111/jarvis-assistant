"""Optional local face & hand utilities (OpenCV + MediaPipe).

This is a fun prototype, NOT authentication: the face "match" is a grayscale
pixel distance and is easy to fool. Never use it to protect anything important.

Everything here is optional:
- Face functions need OpenCV (``cv2``).
- Hand gestures use the supported MediaPipe **Tasks** API (``HandLandmarker``),
  which needs a model file (``hand_landmarker.task``) whose path is given by
  ``ULTRON_HAND_LANDMARKER_MODEL``. If an older MediaPipe that still ships the
  legacy ``mp.solutions`` API is installed, that is used as a fallback.
- When a dependency is missing the assistant keeps running and these
  functions report "unavailable" instead of crashing at import time.

Install: ``uv sync --extra vision`` (or ``pip install -e '.[vision]'``).
"""

import os
import re
import time
from pathlib import Path

from ultron.security.paths import PathPolicyError, atomic_replace, child_path, resolve_directory

# OSError covers native-library failures (e.g. OpenCV needing libGL on a headless Linux box).
try:  # optional dependency
    import cv2
except (ImportError, OSError):  # pragma: no cover - depends on the environment
    cv2 = None

try:  # optional dependency
    import mediapipe as mp
except (ImportError, OSError):  # pragma: no cover - depends on the environment
    mp = None

# Anchored to the project directory (the launcher already runs from there, so existing
# faces/raj.jpg files keep working) instead of whatever the current directory happens to be.
FACE_DIR = os.getenv("ULTRON_FACE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "faces")
_FACE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
HAND_MODEL_ENV = "ULTRON_HAND_LANDMARKER_MODEL"
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)

# Finger tip & PIP landmark indices (index, middle, ring, pinky) — same for both MediaPipe APIs.
FINGER_TIPS = (8, 12, 16, 20)
FINGER_PIPS = (6, 10, 14, 18)


# ========= AVAILABILITY =========

def face_available() -> bool:
    return cv2 is not None


def _hand_model_path():
    path = os.getenv(HAND_MODEL_ENV, "").strip()
    return path if path and os.path.isfile(path) else None


def hands_backend():
    """Return "tasks", "legacy" or None depending on what is installed/configured."""
    if mp is None or cv2 is None:
        return None
    if _hand_model_path() and hasattr(mp, "tasks") and hasattr(mp, "Image"):
        return "tasks"
    if hasattr(mp, "solutions") and hasattr(mp.solutions, "hands"):
        return "legacy"
    return None


def status() -> dict:
    return {
        "opencv": cv2 is not None,
        "mediapipe": None if mp is None else getattr(mp, "__version__", "installed"),
        "hands_backend": hands_backend(),
        "hand_model_configured": _hand_model_path() is not None,
    }


def unavailable_message(hands: bool = False) -> str:
    if cv2 is None:
        return ("Vision features are not installed, sir. Install them with: uv sync --extra vision "
                "(or pip install -e '.[vision]').")
    if hands:
        return ("Hand gestures need the MediaPipe hand model, sir. Download hand_landmarker.task "
                f"({HAND_MODEL_URL}) and set {HAND_MODEL_ENV} to its path.")
    return "Vision features are unavailable right now, sir."


# ========= CAMERA HELPERS =========

def _capture_frame_from_camera(timeout_sec: float = 5.0):
    """Capture a single frame from default webcam. Returns frame (BGR) or None."""
    if cv2 is None:
        return None
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[vision] Cannot access webcam.")
        return None

    start = time.time()
    frame = None

    # Give camera some time to adjust exposure and actually return frames
    while time.time() - start < timeout_sec:
        ret, frame = cap.read()
        if ret and frame is not None:
            break

    cap.release()

    if frame is None:
        print("[vision] Failed to capture frame from webcam.")
    return frame


def _load_face_cascade():
    """Load Haar cascade for face detection."""
    if cv2 is None:
        return None
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    if not os.path.exists(cascade_path):
        print("[vision] Haar cascade not found at:", cascade_path)
        return None

    face_cascade = cv2.CascadeClassifier(cascade_path)
    if face_cascade.empty():
        print("[vision] Failed to load Haar cascade.")
        return None
    return face_cascade


def _crop_face(frame):
    """
    Detect the largest face in the frame and return the cropped region.
    If no face is found, return the original frame.
    """
    face_cascade = _load_face_cascade()
    if face_cascade is None:
        print("[vision] No cascade, returning full frame for face.")
        return frame

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    if len(faces) == 0:
        print("[vision] No face found to crop, using full frame.")
        return frame

    # pick the largest face (by area)
    x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
    face_roi = frame[y:y+h, x:x+w]
    print(f"[vision] Cropped face region: x={x}, y={y}, w={w}, h={h}")
    return face_roi


# ========= FACE "REGISTRATION" & MATCHING =========

def _face_path(name: str, create_dir: bool = False) -> Path:
    """Path of the stored face for ``name``: a plain name, inside FACE_DIR, never via a symlink."""
    if not isinstance(name, str) or not _FACE_NAME_RE.match(name):
        raise PathPolicyError("invalid face name")
    directory = resolve_directory(FACE_DIR, create=create_dir, allow_symlink=False)
    return child_path(directory, f"{name}.jpg")


def register_face(name: str = "owner") -> bool:
    """
    Capture one frame from webcam and save as this user's face image.
    Detects face and crops to that region.
    """
    if cv2 is None:
        return False
    frame = _capture_frame_from_camera()
    if frame is None:
        print("[vision] register_face: no frame captured.")
        return False

    try:
        path = _face_path(name, create_dir=True)
        face_region = _crop_face(frame)
        face_img = cv2.resize(face_region, (200, 200))

        def _write(tmp_name: str) -> None:
            if not cv2.imwrite(tmp_name, face_img):
                raise OSError("could not encode the face image")

        # Atomic: a failed capture/encode leaves the previous reference face untouched.
        atomic_replace(path, _write)
        print(f"[vision] Saved face image for {name}")
        return True
    except Exception as e:
        print("[vision] Error while saving face:", type(e).__name__, e if isinstance(e, PathPolicyError) else "")
        return False


def recognize_face(name: str = "owner", threshold: float = 20000.0):
    """
    Capture a new frame and compare with stored reference face.
    Uses a simple L2 difference of grayscale images.
    threshold: lower means stricter match.
    Returns (is_same_person: bool, score: float or None)
    """
    if cv2 is None:
        return False, None
    try:
        ref_path = _face_path(name)
    except PathPolicyError as exc:
        print(f"[vision] No usable stored face for {name}: {exc}.")
        return False, None
    if not ref_path.is_file():
        print(f"[vision] No stored face for {name}.")
        return False, None

    ref_img = cv2.imread(str(ref_path))
    if ref_img is None:
        print(f"[vision] Could not read stored face for {name}.")
        return False, None

    frame = _capture_frame_from_camera()
    if frame is None:
        print("[vision] recognize_face: no frame captured.")
        return False, None

    try:
        # Crop faces in both images
        ref_face = _crop_face(ref_img)
        cur_face = _crop_face(frame)

        ref_gray = cv2.cvtColor(cv2.resize(ref_face, (200, 200)), cv2.COLOR_BGR2GRAY)
        cur_gray = cv2.cvtColor(cv2.resize(cur_face, (200, 200)), cv2.COLOR_BGR2GRAY)

        # Compute simple L2 distance
        diff = cv2.norm(ref_gray, cur_gray, cv2.NORM_L2)
        print(f"[vision] Face diff score: {diff}")

        is_same = diff < threshold
        return is_same, diff
    except Exception as e:
        print("[vision] Error during face compare:", e)
        return False, None


# ========= HAND GESTURE (OPEN PALM vs UNKNOWN) =========

def classify_hand(landmarks) -> str:
    """Classify one hand from 21 normalized landmarks (objects with ``.y``).

    In image coordinates a smaller y is higher, so a fingertip above its PIP
    joint counts as extended. Three or more extended fingers = open palm.
    """
    extended = sum(1 for tip, pip in zip(FINGER_TIPS, FINGER_PIPS) if landmarks[tip].y < landmarks[pip].y)
    print(f"[vision] Extended fingers: {extended}")
    return "open_palm" if extended >= 3 else "unknown"


def _hands_from_tasks(max_attempts: int):
    """Yield the first hand's landmarks per frame using the MediaPipe Tasks HandLandmarker."""
    vision_tasks = mp.tasks.vision
    options = vision_tasks.HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=_hand_model_path()),
        num_hands=1,
        min_hand_detection_confidence=0.5,
    )
    with vision_tasks.HandLandmarker.create_from_options(options) as landmarker:
        for _ in range(max_attempts):
            frame = _capture_frame_from_camera()
            if frame is None:
                yield None
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            yield result.hand_landmarks[0] if result.hand_landmarks else None


def _hands_from_legacy(max_attempts: int):
    """Same, using the legacy ``mp.solutions.hands`` API (older MediaPipe releases only)."""
    with mp.solutions.hands.Hands(static_image_mode=True, max_num_hands=1, min_detection_confidence=0.5) as hands:
        for _ in range(max_attempts):
            frame = _capture_frame_from_camera()
            if frame is None:
                yield None
                continue
            results = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            yield results.multi_hand_landmarks[0].landmark if results.multi_hand_landmarks else None


def detect_hand_gesture(max_attempts: int = 10):
    """
    Try multiple frames from webcam, look for one hand, detect if it's an 'open palm'.
    Returns: "open_palm", "no_hand", "unknown" or "unavailable".
    """
    backend = hands_backend()
    if backend is None:
        return "unavailable"
    frames = _hands_from_tasks(max_attempts) if backend == "tasks" else _hands_from_legacy(max_attempts)
    last_result = "no_hand"
    try:
        for landmarks in frames:
            if landmarks is None:
                last_result = "no_hand"
                continue
            if classify_hand(landmarks) == "open_palm":
                return "open_palm"
            last_result = "unknown"
    except Exception as e:
        print("[vision] Error in hand detection:", e)
        return "unknown"
    return last_result


# ========= SIMPLE "OBJECT" VIEW: DO I SEE A PERSON? =========

def see_any_person():
    """
    Use a basic Haar cascade to check whether a face is visible.
    Returns True if at least one face is detected.
    """
    face_cascade = _load_face_cascade()
    if face_cascade is None:
        return False

    frame = _capture_frame_from_camera()
    if frame is None:
        print("[vision] see_any_person: no frame captured.")
        return False

    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)
        print(f"[vision] Faces detected: {len(faces)}")
        return len(faces) > 0
    except Exception as e:
        print("[vision] Error in face detection:", e)
        return False
