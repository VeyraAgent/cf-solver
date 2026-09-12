"""ONNX predictor for Arkose FunCaptcha challenge images.

Loads models from arkose/models/ using onnxruntime directly.
Thread-limited to 4 threads to avoid CPU spikes.
Also provides CryptoJS-compatible AES encryption for answer submission.
"""
import os

# MUST be set before onnxruntime/numpy import
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")

import base64
import hashlib
import io
import json
import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

log = logging.getLogger("arkose.predict")

_MODELS_DIR = Path(__file__).parent / "models"

# Variant → ONNX model file mapping
_VARIANT_MODELS = {
    "conveyor": "conveyor.onnx",
    "coordinatesmatch": "coordinatesmatch.onnx",
    "3d_rollball_animals": "threed_rollball_animal.onnx",
    "3d_rollball_objects": "3d_rollball_objects_v2.onnx",
    "hopscotch_highsec": "hopscotch_highsec.onnx",
    "train_coordinates": "train_coordinates.onnx",
    "BrokenJigsawbrokenjigsaw_swap": "BrokenJigsawbrokenjigsaw_swap.onnx",
    "shadows": "shadows.onnx",
    "penguins": "penguins.onnx",
    "frankenhead": "frankenhead.onnx",
    "counting": "counting.onnx",
    "knotsCrossesCircle": "knotsCrossesCircle.onnx",
    "hand_number_puzzle": "hand_number_puzzle.onnx",
    "card": "card.onnx",
    "rockstack": "rockstack.onnx",
    "cardistance": "cardistance.onnx",
    "penguins-icon": "penguins-icon.onnx",
    "dicematch": "dicematch.onnx",
    "unbentobjects": "unbentobjects.onnx",
    "dice_pair": "dice_pair.onnx",
    "rockstack_v2": "rockstack_v2.onnx",
    "coordinatesmatch_cv": "coordinatesmatch_cv.onnx",
    "train_coordinates_cv": "train_coordinates_cv.onnx",
    "3d_rollball_objects_cv": "3d_rollball_objects_cv.onnx",
}

# Instruction string → variant name mapping (from Arkose gfct response)
_INSTRUCTION_MAP = {
    "conveyor_belt_V2": "conveyor",
    "3d_rollball_objects_v2": "3d_rollball_objects",
}

# Cache loaded sessions
_sessions: dict[str, ort.InferenceSession] = {}


def _get_session(variant: str) -> ort.InferenceSession | None:
    if variant in _sessions:
        return _sessions[variant]

    model_file = _VARIANT_MODELS.get(variant)
    if not model_file:
        return None

    model_path = _MODELS_DIR / model_file
    if not model_path.exists():
        log.error("model not found: %s", model_path)
        return None

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    opts.inter_op_num_threads = 1
    opts.enable_cpu_mem_arena = False
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

    session = ort.InferenceSession(str(model_path), opts)
    _sessions[variant] = session
    return session


def _resolve_variant(instruction: str) -> str:
    if not instruction:
        return ""
    if instruction in _INSTRUCTION_MAP:
        return _INSTRUCTION_MAP[instruction]
    if instruction in _VARIANT_MODELS:
        return instruction
    lower = instruction.lower()
    if lower in _VARIANT_MODELS:
        return lower
    return instruction


def _preprocess_pair(image: Image.Image, index: tuple, input_shape=(52, 52),
                     grayscale=False) -> np.ndarray:
    """Crop a 200x200 tile from a pair-classifier image and normalize."""
    x, y = index[1] * 200, index[0] * 200
    sub = image.crop((x, y, x + 200, y + 200)).resize(input_shape)
    if grayscale:
        sub = sub.convert("L")
        return np.array(sub)[np.newaxis, np.newaxis, ...] / 255.0
    return np.array(sub).transpose(2, 0, 1)[np.newaxis, ...] / 255.0


def _preprocess_single(image: Image.Image, index: int,
                       input_shape=(52, 52), grayscale=False) -> np.ndarray:
    """Crop a 100x100 tile from a 300x200 image (3x2 grid)."""
    row, col = index // 3, index % 3
    x, y = col * 100, row * 100
    sub = image.crop((x, y, x + 100, y + 100)).resize(input_shape)
    if grayscale:
        sub = sub.convert("L")
        return np.array(sub)[np.newaxis, np.newaxis, ...] / 255.0
    return np.array(sub).transpose(2, 0, 1)[np.newaxis, ...] / 255.0


def _crop_ans(image: Image.Image, input_shape=(52, 52),
              grayscale=False) -> np.ndarray:
    """Crop the answer reference from a pair-classifier image."""
    sub = image.crop((0, 200, 135, 400)).resize(input_shape)
    if grayscale:
        sub = sub.convert("L")
        return np.array(sub)[np.newaxis, np.newaxis, ...] / 255.0
    return np.array(sub).transpose(2, 0, 1)[np.newaxis, ...] / 255.0


def _is_pair_classifier(session: ort.InferenceSession) -> bool:
    """Check if model expects pair input (input_left + input_right)."""
    names = [i.name for i in session.get_inputs()]
    return "input_left" in names and "input_right" in names


def predict(image: Image.Image, instruction: str) -> int | None:
    """Predict answer index for a FunCaptcha challenge image.

    Args:
        image: PIL Image (1200x400 for pair classifiers, 300x200 for single)
        instruction: Arkose instruction_string from gfct response

    Returns:
        Predicted answer index (0-5) or None if unsupported/failed.
    """
    variant = _resolve_variant(instruction)
    session = _get_session(variant)
    if not session:
        log.error("no model for variant=%r instruction=%r", variant, instruction)
        return None

    grayscale = variant in ("conveyor",)
    is_pair = _is_pair_classifier(session)

    try:
        if is_pair:
            # Pair classifier: compare answer ref against each candidate tile
            width = image.width
            left = _crop_ans(image, grayscale=grayscale)
            best_score = float("-inf")
            best_idx = -1
            for i in range(width // 200):
                right = _preprocess_pair(image, (0, i), grayscale=grayscale)
                result = session.run(None, {
                    "input_left": left.astype(np.float32),
                    "input_right": right.astype(np.float32),
                })
                score = result[0][0]
                if score > best_score:
                    best_score = score
                    best_idx = i
            return best_idx
        else:
            # Single classifier: score each of 6 tiles
            best_score = float("-inf")
            best_idx = -1
            for i in range(6):
                tile = _preprocess_single(image, i, grayscale=grayscale)
                result = session.run(None, {"input": tile.astype(np.float32)})
                score = result[0][0]
                if score > best_score:
                    best_score = score
                    best_idx = i
            return best_idx
    except Exception as e:
        log.error("prediction failed: %s", e)
        return None


def cryptojs_encrypt(plaintext: str, passphrase: str) -> str:
    """CryptoJS-compatible AES-CBC encryption.

    Arkose /fc/ca/ expects a JSON object (NOT the U2FsdGVkX1 base64 format):
    {"ct": "<base64 ciphertext>", "iv": "<hex IV>", "s": "<hex salt>"}

    Key derivation: EVP_BytesToKey with MD5, 1 iteration, 32-byte key + 16-byte IV.
    Encryption: AES-256-CBC with PKCS7 padding.
    """
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    salt = os.urandom(8)

    # EVP_BytesToKey: MD5, 1 iteration
    data = passphrase.encode("utf-8")
    d = b""
    key_iv = b""
    while len(key_iv) < 48:
        h = hashlib.md5()
        h.update(d + data + salt)
        d = h.digest()
        key_iv += d
    key = key_iv[:32]
    iv = key_iv[32:48]

    # AES-256-CBC with PKCS7 padding
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ct = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))

    # Return as JSON object (Negt-dev format)
    return json.dumps({
        "ct": base64.b64encode(ct).decode(),
        "iv": iv.hex(),
        "s": salt.hex(),
    }, separators=(",", ":"))
