"""ocr_engine.py

High-Accuracy OCR Engine powered by ddddocr ONNX model (common.onnx & common.json).

Exact Pipeline (matching extension logic):
1. Load ONNX model (models/common.onnx) & Character Dictionary (models/common.json).
2. Image Preprocessing:
   - Resize to height 64, width proportional to aspect ratio.
   - Convert to Grayscale (0.299*R + 0.587*G + 0.114*B).
   - Normalize pixel values to [-1.0, 1.0]: (pixel / 255.0 - 0.5) / 0.5.
   - Input shape: (1, 1, 64, target_width).
3. ONNX Inference:
   - Input tensor: 'input1'.
   - Runs model through onnxruntime.
4. CTC Decoding:
   - Argmax across logits.
   - Remove consecutive repeated indices and blank token (index 0).
   - Lookup characters in charset dictionary.
"""

import os
import json
import base64
import cv2
import numpy as np
import onnxruntime as ort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "models", "common.onnx")
CHARSET_PATH = os.path.join(BASE_DIR, "models", "common.json")

class OCREngine:
    def __init__(self, model_path: str = MODEL_PATH, charset_path: str = CHARSET_PATH):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"ONNX model missing at: {model_path}")
        if not os.path.exists(charset_path):
            raise FileNotFoundError(f"Charset JSON missing at: {charset_path}")

        # Load character dictionary
        with open(charset_path, "r", encoding="utf-8") as f:
            self.charset = json.load(f)

        # Load ONNX session (suppress warnings matching extension configuration)
        so = ort.SessionOptions()
        so.log_severity_level = 4
        self.session = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name  # 'input1'

    def preprocess(self, img: np.ndarray) -> tuple[np.ndarray, int, int]:
        """Matches exact extension preprocessing: height 64, dynamic width, [-1, 1] normalization."""
        if len(img.shape) == 2:
            # Already grayscale
            gray = img.astype(np.float32)
        else:
            # BGR / RGB to Grayscale using standard luminance weights
            b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]
            gray = 0.299 * r + 0.587 * g + 0.114 * b

        h, w = gray.shape[:2]
        target_h = 64
        target_w = max(1, int(w * (target_h / h)))

        # Resize to target (64, target_w)
        resized = cv2.resize(gray, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

        # Normalize to [-1.0, 1.0]
        norm = (resized / 255.0 - 0.5) / 0.5

        # Format input tensor to shape [1, 1, 64, target_w]
        tensor = norm.astype(np.float32)
        tensor = np.expand_dims(np.expand_dims(tensor, axis=0), axis=0)
        return tensor, target_h, target_w

    def ctc_decode(self, outputs: np.ndarray) -> str:
        """Greedy CTC Decoder matching ddddocr logic."""
        # Output shape: [seq_len, batch, num_classes] or [1, seq_len, num_classes]
        if len(outputs.shape) == 3 and outputs.shape[0] == 1:
            logits = outputs[0]
        else:
            logits = outputs

        # Argmax along character class axis
        pred_indices = np.argmax(logits, axis=-1)
        if len(pred_indices.shape) > 1:
            pred_indices = pred_indices.flatten()

        # CTC decode: collapse consecutive duplicates and ignore blank (index 0)
        result = []
        last_idx = 0
        for idx in pred_indices:
            idx = int(idx)
            if idx == last_idx:
                continue
            last_idx = idx
            if idx != 0 and idx < len(self.charset):
                result.append(self.charset[idx])

        return "".join(result)

    def predict_image(self, img: np.ndarray) -> str:
        tensor, _, _ = self.preprocess(img)
        outputs = self.session.run(None, {self.input_name: tensor})
        return self.ctc_decode(outputs[0])

    def predict_bytes(self, image_bytes: bytes) -> str:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Invalid image bytes - failed to decode image.")
        return self.predict_image(img)

    def predict_b64(self, b64_str: str) -> str:
        """Decodes base64 string (with or without data URI prefix) and solves it."""
        clean_b64 = b64_str.strip()
        if "," in clean_b64:
            clean_b64 = clean_b64.split(",", 1)[1]
        image_bytes = base64.b64decode(clean_b64)
        return self.predict_bytes(image_bytes)

    def predict_file(self, image_path: str) -> str:
        abs_path = os.path.abspath(image_path)
        if not os.path.isfile(abs_path):
            raise FileNotFoundError(f"Image file does not exist: {abs_path}")
        
        # Read file as byte array to bypass OpenCV's Windows Unicode path limitations
        try:
            image_bytes = np.fromfile(abs_path, dtype=np.uint8)
            img = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
        except Exception as e:
            raise ValueError(f"Failed to read image file at {abs_path}: {e}")

        if img is None:
            raise ValueError(f"Image file is damaged or unreadable format: {abs_path}")
            
        return self.predict_image(img)

    def predict_auto(self, input_data: str | bytes) -> str:
        """Automatically detects whether input is bytes, a file path, or a Base64 string."""
        if isinstance(input_data, bytes):
            return self.predict_bytes(input_data)
        
        if isinstance(input_data, str):
            # Check if it is an existing local file
            if os.path.isfile(input_data):
                return self.predict_file(input_data)
            
            # Try parsing as Base64 string
            try:
                return self.predict_b64(input_data)
            except Exception as e:
                raise ValueError(f"Input is neither a valid file path nor a valid Base64 image string: {e}")

        raise TypeError(f"Unsupported input type: {type(input_data)}")
