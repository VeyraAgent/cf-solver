"""Vendored CaptchaFox protocol solver (sitekey -> token).

Origin: "captcha fox BY @Real_zezomod" zip, shared via Telegram.
No license attached. Kept as a separate module so the image-mode solver
in solve.py stays independent and can be deleted without touching this.
Static audit: no subprocess/eval/exec, pyc digest matched source,
only outbound hosts are api/cdn/mam-api.captchafox.com.
"""
#!/usr/bin/env python3
"""
Target: CaptchaFox Bot Defense Engine (One-Click & Slider Alignment Solver)
Protocol Specification & Wire Reversal:
  Standard CDN:      https://cdn.captchafox.com
  Standard API:      https://api.captchafox.com
  Enterprise API:    https://mam-api.captchafox.com (e.g., auth.web.de)
  Default Pulses:    b980cb3acc96a6cb (Standard), 2bd77e6f8a17bc0e (Enterprise)

Wire Protocol Envelope Layout:
  [0]     - Version Header (0x01)
  [1]     - Sub-version / Type Header (0x04)
  [2:]    - XOR Encrypted Stream:
            C[i] = GZIP_COMPRESS(JSON_PAYLOAD)[i] ^ ((i + 4) & 0xFF)

Verification Handshake Sequence:
  1. GET  /captcha/{sitekey}/config?site={origin+path}
     -> Yields challenge session hash 'h' and initial Proof-of-Work challenge 'm'.
  2. SHA-256 Proof-of-Work Engine:
     -> Prefix: m[1], Target: int(m[2], 2) leading hex zeros.
     -> Nonce: Incremented until SHA256(prefix + str(nonce)).startswith('0' * target).
  3. POST /captcha/{sitekey}/challenge (Wire Encrypted)
     -> One-Click check: If sitekey / IP reputation is clean, returns 'solved': true and 'token'.
     -> Interactive: If 'solved': false and type == 'slide', returns puzzle 'challenge' (bg + player) and secondary PoW 'j'.
  4. Slider Alignment & Trajectory:
     -> Computer vision / alpha contour analysis between background cutout and player sprite.
     -> Trajectory synthesis: Natural human drag with micro-tremors [clientX_0, sliderX_0, ...].
  5. POST /captcha/verify (Wire Encrypted)
     -> Submits {sk, mv, t, p, h, cs, k, type, host} -> Yields verified token.
"""

import os
import sys
import time
import math
import json
import gzip
import random
import hashlib
import argparse
import base64
import urllib.request
import urllib.error
import urllib.parse
from io import BytesIO
from typing import Dict, List, Tuple, Optional, Any

try:
    from PIL import Image
    import numpy as np
except ImportError:
    Image = None
    np = None

try:
    import cv2
except ImportError:
    cv2 = None


# Named configuration entries for protocol parameters
DEFAULT_CONFIG: Dict[str, Any] = {
    "API_BASE": "https://api.captchafox.com",
    "ENTERPRISE_API_BASE": "https://mam-api.captchafox.com",
    "CDN_BASE": "https://cdn.captchafox.com",
    "PULSE_STANDARD": "b980cb3acc96a6cb",
    "PULSE_ENTERPRISE": "2bd77e6f8a17bc0e",
    "SPARK_SDK": "rj-1.13.0",
    "DEFAULT_SITEKEY": "sk_bo3q016TDv4Jey6fibTVChVS9z-cYdCO",
    "DEFAULT_HOST": "captchafox.com",
    "DEFAULT_SITE_URL": "https://captchafox.com/",
    "DEFAULT_LANG": "en",
    "USER_AGENT": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
    ),
    "CANVAS_WIDTH": 250,
    "SLIDER_WIDTH": 250,
    "SLIDER_HEIGHT": 60,
    "PLAYER_SIZE": 50,
}


def format_proxy(proxy_str: Optional[str]) -> Optional[str]:
    """
    Standardizes proxy strings into a valid URL for urllib.
    Supports formats:
      - host:port:user:pass -> http://user:pass@host:port
      - host:port           -> http://host:port
      - user:pass@host:port -> http://user:pass@host:port
      - http://... / https://... / socks5://...
    If empty or None, returns None (direct connection without proxy).
    """
    if not proxy_str or not str(proxy_str).strip():
        return None
    proxy_str = str(proxy_str).strip()

    if "://" in proxy_str:
        return proxy_str

    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    elif len(parts) == 2:
        return f"http://{proxy_str}"
    elif "@" in proxy_str:
        return f"http://{proxy_str}"

    return f"http://{proxy_str}"


def encode_wire_payload(payload: dict) -> bytes:
    """
    Encodes JSON object into CaptchaFox obfuscated wire buffer:
      1. Serialize JSON without whitespace.
      2. Compress UTF-8 bytes with standard GZIP.
      3. XOR every compressed byte at index i with (i + 4) & 0xFF.
      4. Prepend magic header: 0x01, 0x04.
    """
    json_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    compressed = gzip.compress(json_bytes)
    wire = bytearray([0x01, 0x04])
    for i, byte_val in enumerate(compressed):
        wire.append(byte_val ^ ((i + 4) & 0xFF))
    return bytes(wire)


def decode_wire_payload(raw_bytes: bytes) -> dict:
    """
    Decodes CaptchaFox obfuscated wire buffer back into Python dict.
    """
    if len(raw_bytes) < 2 or raw_bytes[0] != 0x01 or raw_bytes[1] != 0x04:
        raise ValueError("Invalid wire header: expected magic [0x01, 0x04]")
    decrypted = bytearray()
    for i, byte_val in enumerate(raw_bytes[2:]):
        decrypted.append(byte_val ^ ((i + 4) & 0xFF))
    decompressed = gzip.decompress(bytes(decrypted))
    return json.loads(decompressed.decode('utf-8'))


def solve_pow(prefix: str, difficulty_spec: Any) -> int:
    """
    Solves SHA-256 Proof-of-Work challenge required by CaptchaFox engine.
    Difficulty is either binary string (e.g. '100' -> 4 zeros, '11' -> 3 zeros) or integer count.
    """
    if isinstance(difficulty_spec, str):
        target_zeros = int(difficulty_spec, 2)
    else:
        target_zeros = int(difficulty_spec)

    target_prefix = "0" * target_zeros
    nonce = 0
    while True:
        candidate = f"{prefix}{nonce}".encode("ascii")
        digest = hashlib.sha256(candidate).hexdigest()
        if digest.startswith(target_prefix):
            return nonce
        nonce += 1
        if nonce > 20_000_000:
            raise RuntimeError(f"PoW iteration limit exceeded for target {target_zeros}")


def generate_client_telemetry(host: str, user_agent: str) -> Dict[str, Any]:
    """
    Synthesizes authentic CF0100-CF0148 browser fingerprint telemetry
    conforming to the complete 49-key sensor specification extracted from HAR captures.
    """
    now_ms = int(time.time() * 1000)

    window_globals = [
        "Object", "Function", "Array", "Number", "parseFloat", "parseInt", "Infinity",
        "NaN", "undefined", "Boolean", "String", "Symbol", "Date", "Promise", "RegExp",
        "Error", "AggregateError", "EvalError", "RangeError", "ReferenceError", "SyntaxError",
        "TypeError", "URIError", "SuppressedError", "globalThis", "JSON", "Math", "Intl",
        "ArrayBuffer", "Atomics", "Uint8Array", "Int8Array", "Uint16Array", "Int16Array",
        "Uint32Array", "Int32Array", "BigUint64Array", "BigInt64Array", "Uint8ClampedArray",
        "Float32Array", "Float64Array", "Float16Array", "DataView", "Map", "BigInt", "Set",
        "Iterator", "WeakMap", "WeakSet", "Proxy", "Reflect", "FinalizationRegistry",
        "WeakRef", "DisposableStack", "AsyncDisposableStack", "decodeURI", "decodeURIComponent",
        "encodeURI", "encodeURIComponent", "escape", "unescape", "eval", "isFinite", "isNaN",
        "console", "Option", "Image", "Audio", "webkitURL", "webkitRTCPeerConnection",
        "webkitMediaStream", "WebKitMutationObserver", "WebKitCSSMatrix", "FeaturePolicy",
        "XPathResult", "XPathExpression", "XPathEvaluator", "XMLSerializer",
        "XMLHttpRequestUpload", "XMLHttpRequestEventTarget", "XMLHttpRequest", "XMLDocument",
        "WritableStreamDefaultWriter", "WritableStreamDefaultController", "WritableStream",
        "Worker", "WindowControlsOverlayGeometryChangeEvent", "WindowControlsOverlay",
        "Window", "WheelEvent", "WebSocket", "WebGLVertexArrayObject",
        "WebGLUniformLocation", "WebGLTransformFeedback", "WebGLTexture", "WebGLSync",
        "WebGLShaderPrecisionFormat", "WebGLShader", "WebGLSampler", "WebGLRenderingContext",
        "WebGLRenderbuffer", "WebGLProgram", "WebGLFramebuffer", "WebGLContextEvent",
        "WebGLBuffer", "WebGL2RenderingContext", "WaveShaperNode", "VisualViewport",
        "VisibilityStateEntry", "VirtualKeyboardGeometryChangeEvent", "VirtualKeyboard",
        "ViewTransitionTypeSet", "ViewTransition", "VideoPlaybackQuality",
        "VideoFrame", "VideoColorSpace", "ValidityState", "VTTCue", "UserActivation",
        "URLSearchParams", "URLPattern", "URL", "UIEvent", "TreeWalker", "TransitionEvent",
        "TransformStreamDefaultController", "TransformStream", "TrackEvent", "TouchList",
        "TouchEvent", "Touch", "ToggleEvent", "TimeRanges", "TextUpdateEvent",
        "TextTrackList", "TextTrackCueList", "TextTrackCue", "TextTrack", "TextMetrics",
        "TextFormatUpdateEvent", "TextFormat", "TextEvent", "TextEncoderStream",
        "TextEncoder", "TextDecoderStream", "TextDecoder", "Text", "TaskSignal",
        "TaskPriorityChangeEvent", "TaskController", "TaskAttributionTiming", "SyncManager",
        "SubmitEvent", "StyleSheetList", "StyleSheet", "StylePropertyMapReadOnly",
        "StylePropertyMap", "StorageEvent", "Storage", "StereoPannerNode", "StaticRange",
        "SourceBufferList", "SourceBuffer", "ShadowRoot", "Selection", "SecurityPolicyViolationEvent",
        "ScriptProcessorNode", "ScreenOrientation", "Screen", "Scheduling", "Scheduler",
        "SVGViewElement", "SVGUseElement", "SVGUnitTypes", "SVGTransformList", "SVGTransform",
        "SVGTitleElement", "SVGTextPositioningElement", "SVGTextPathElement", "SVGTextElement",
        "SVGTextContentElement", "SVGTSpanElement", "SVGSymbolElement", "SVGSwitchElement",
        "SVGStyleElement", "SVGStringList", "SVGStopElement", "SVGSetElement",
        "SVGScriptElement", "SVGSVGElement", "SVGRectElement", "SVGRadialGradientElement",
        "SVGPreserveAspectRatio", "SVGPolylineElement", "SVGPolygonElement", "SVGPointList",
        "SVGPoint", "SVGPatternElement", "SVGPathElement", "SVGNumberList", "SVGNumber",
        "SVGMetadataElement", "SVGMatrix", "SVGMaskElement", "SVGMarkerElement",
        "SVGLinearGradientElement", "SVGLineElement", "SVGLengthList", "SVGLength",
        "SVGImageElement", "SVGGElement", "SVGForeignObjectElement", "SVGFETurbulenceElement",
        "SVGFETileElement", "SVGFESpotLightElement", "SVGFESpecularLightingElement",
        "SVGFEPointLightElement", "SVGFEOffsetElement", "SVGFEMorphologyElement",
        "SVGFEMergeNodeElement", "SVGFEMergeElement", "SVGFEImageElement",
        "SVGFEGaussianBlurElement", "SVGFEFloodElement", "SVGFEDisplacementMapElement",
        "SVGFEDiffuseLightingElement", "SVGFEConvolveMatrixElement",
        "SVGFECompositeElement", "SVGFEComponentTransferElement", "SVGFEColorMatrixElement",
        "SVGFEBlendElement", "SVGEllipseElement", "SVGElement", "SVGDescElement",
        "SVGDefsElement", "SVGComponentTransferFunctionElement", "SVGClipPathElement",
        "SVGCircleElement", "SVGAnimationElement", "SVGAnimatedTransformList",
        "SVGAnimatedString", "SVGAnimatedRect", "SVGAnimatedPreserveAspectRatio",
        "SVGAnimatedNumberList", "SVGAnimatedNumber", "SVGAnimatedLengthList",
        "SVGAnimatedLength", "SVGAnimatedInteger", "SVGAnimatedEnumeration",
        "SVGAnimatedBoolean", "SVGAnimatedAngle", "SVGAnimateTransformElement",
        "SVGAnimateMotionElement", "SVGAnimateElement", "SVGAngle", "SVGAElement",
        "Response", "ResizeObserverSize", "ResizeObserverEntry", "ResizeObserver",
        "Request", "ReportingObserver", "RemotePlayback", "Range", "RadioNodeList",
        "RTCTrackEvent", "RTCStatsReport", "RTCSctpTransport", "RTCRtpTransceiver",
        "RTCRtpSender", "RTCRtpReceiver", "RTCPeerConnectionIceErrorEvent",
        "RTCPeerConnectionIceEvent", "RTCPeerConnection", "RTCIceTransport",
        "RTCIceCandidate", "RTCErrorEvent", "RTCError", "RTCEncodedVideoFrame",
        "RTCEncodedAudioFrame", "RTCDtlsTransport", "RTCDataChannelEvent",
        "RTCDataChannel", "RTCCertificate", "PromiseRejectionEvent", "ProgressEvent",
        "ProcessingInstruction", "PointerEvent", "PluginArray", "Plugin",
        "PeriodicWave", "PerformanceTiming", "PerformanceServerTiming",
        "PerformanceResourceTiming", "PerformancePaintTiming", "PerformanceObserverEntryList",
        "PerformanceObserver", "PerformanceNavigationTiming", "PerformanceNavigation",
        "PerformanceMeasure", "PerformanceMark", "PerformanceLongTaskTiming",
        "PerformanceLongAnimationFrameTiming", "PerformanceEventTiming",
        "PerformanceEntry", "PerformanceElementTiming", "Performance", "Path2D",
        "PannerNode", "PageTransitionEvent", "OverconstrainedError", "OscillatorNode",
        "OffscreenCanvasRenderingContext2D", "OffscreenCanvas", "OfflineAudioContext",
        "OfflineAudioCompletionEvent", "NodeList", "NodeIterator", "Node",
        "Navigator", "NavigationTransition", "NavigationHistoryEntry",
        "NavigationDestination", "NavigationCurrentEntryChangeEvent", "Navigation",
        "NavigateEvent", "NamedNodeMap", "MutationRecord", "MutationObserver",
        "MutationEvent", "MouseEvent", "MimeTypeArray", "MimeType", "MediaStreamTrackVideoStats",
        "MediaStreamTrackProcessor", "MediaStreamTrackGenerator", "MediaStreamTrackEvent",
        "MediaStreamTrackAudioStats", "MediaStreamTrack", "MediaStreamEvent",
        "MediaStreamAudioSourceNode", "MediaStreamAudioDestinationNode", "MediaStream",
        "MediaSourceHandle", "MediaSource", "MediaRecorder", "MediaQueryListEvent",
        "MediaQueryList", "MediaList", "MediaError", "MediaEncryptedEvent",
        "MediaElementAudioSourceNode", "MediaCapabilities", "MathMLElement",
        "Location", "LayoutShiftAttribution", "LayoutShift", "LargestContentfulPaint",
        "KeyframeEffect", "KeyboardEvent", "IntersectionObserverEntry",
        "IntersectionObserver", "InputEvent", "InputDeviceInfo", "IdleDeadline",
        "IIRFilterNode", "IDBVersionChangeEvent", "IDBTransaction", "IDBRequest",
        "IDBOpenDBRequest", "IDBObjectStore", "IDBKeyRange", "IDBIndex",
        "IDBFactory", "IDBDatabase", "IDBCursorWithValue", "IDBCursor",
        "History", "Headers", "HashChangeEvent", "HTMLVideoElement", "HTMLUnknownElement",
        "HTMLUListElement", "HTMLTrackElement", "HTMLTitleElement", "HTMLTimeElement",
        "HTMLTextAreaElement", "HTMLTemplateElement", "HTMLTableSectionElement",
        "HTMLTableRowElement", "HTMLTableElement", "HTMLTableColElement",
        "HTMLTableCellElement", "HTMLTableCaptionElement", "HTMLStyleElement",
        "HTMLSpanElement", "HTMLSourceElement", "HTMLSlotElement", "HTMLSelectElement",
        "HTMLScriptElement", "HTMLQuoteElement", "HTMLProgressElement", "HTMLPreElement",
        "HTMLPictureElement", "HTMLParamElement", "HTMLParagraphElement",
        "HTMLOutputElement", "HTMLOptionElement", "HTMLOptGroupElement", "HTMLObjectElement",
        "HTMLOListElement", "HTMLModElement", "HTMLMeterElement", "HTMLMetaElement",
        "HTMLMenuElement", "HTMLMediaElement", "HTMLMarqueeElement", "HTMLMapElement",
        "HTMLLinkElement", "HTMLLegendElement", "HTMLLabelElement", "HTMLLIElement",
        "HTMLInputElement", "HTMLImageElement", "HTMLIFrameElement", "HTMLHtmlElement",
        "HTMLHeadingElement", "HTMLHeadElement", "HTMLHRElement", "HTMLFrameSetElement",
        "HTMLFrameElement", "HTMLFormElement", "HTMLFormControlsCollection",
        "HTMLFontElement", "HTMLFieldSetElement", "HTMLEmbedElement", "HTMLElement",
        "HTMLDocument", "HTMLDivElement", "HTMLDirectoryElement", "HTMLDialogElement",
        "HTMLDetailsElement", "HTMLDataListElement", "HTMLDataElement", "HTMLDListElement",
        "HTMLCanvasElement", "HTMLButtonElement", "HTMLBodyElement", "HTMLBaseElement",
        "HTMLBRElement", "HTMLAudioElement", "HTMLAreaElement", "HTMLAnchorElement",
        "HTMLAllCollection", "GeolocationPositionError", "GeolocationPosition",
        "GeolocationCoordinates", "Geolocation", "GamepadHapticActuator", "GamepadEvent",
        "GamepadButton", "Gamepad", "GainNode", "FormDataEvent", "FormData",
        "FontFaceSetLoadEvent", "FontFace", "FocusEvent", "FileReader", "FileList",
        "File", "External", "EventTarget", "EventSource", "EventCounts", "Event",
        "ErrorEvent", "DynamicsCompressorNode", "DragEvent", "DocumentType",
        "DocumentFragment", "Document", "DelayNode", "DecompressionStream",
        "DataTransferItemList", "DataTransferItem", "DataTransfer", "DOMTokenList",
        "DOMStringMap", "DOMStringList", "DOMRectReadOnly", "DOMRectList",
        "DOMRect", "DOMPointReadOnly", "DOMPoint", "DOMParser", "DOMMatrixReadOnly",
        "DOMMatrix", "DOMImplementation", "DOMException", "DOMError", "CustomEvent",
        "CustomElementRegistry", "Crypto", "CountQueuingStrategy", "ConvolverNode",
        "ConstantSourceNode", "CompressionStream", "CompositionEvent", "CloseEvent",
        "ClipboardEvent", "ChannelSplitterNode", "ChannelMergerNode", "CanvasRenderingContext2D",
        "CanvasPattern", "CanvasGradient", "CanvasCaptureMediaStreamTrack", "CSSViewTimeline",
        "CSSVariableReferenceValue", "CSSUnparsedValue", "CSSUnitValue", "CSSTransition",
        "CSSTransformValue", "CSSTransformComponent", "CSSSupportsRule", "CSSStyleValue",
        "CSSStyleSheet", "CSSStyleRule", "CSSStyleDeclaration", "CSSStartingStyleRule",
        "CSSSkewY", "CSSSkewX", "CSSSkew", "CSSScale", "CSSRuleList", "CSSRule",
        "CSSRotate", "CSSPropertyRule", "CSSPositionValue", "CSSPerspective",
        "CSSPageRule", "CSSNumericValue", "CSSNumericArray", "CSSNamespaceRule",
        "CSSMediaRule", "CSSMatrixComponent", "CSSMathValue", "CSSMathSum",
        "CSSMathProduct", "CSSMathNegate", "CSSMathMin", "CSSMathMax", "CSSMathInvert",
        "CSSMathClamp", "CSSLayerStatementRule", "CSSLayerBlockRule", "CSSKeywordValue",
        "CSSKeyframesRule", "CSSKeyframeRule", "CSSImportRule", "CSSImageValue",
        "CSSGroupingRule", "CSSFontFaceRule", "CSSFontFeatureValuesRule",
        "CSSCounterStyleRule", "CSSContainerRule", "CSSConditionRule", "CSSColorValue",
        "CSSAnimation", "CSS", "CDATASection", "ByteLengthQueuingStrategy",
        "BroadcastChannel", "BlobEvent", "Blob", "BiquadFilterNode", "BeforeUnloadEvent",
        "BatteryManager", "BaseAudioContext", "BarProp", "AudioWorkletNode",
        "AudioSinkInfo", "AudioScheduledSourceNode", "AudioProcessingEvent",
        "AudioParamMap", "AudioParam", "AudioNode", "AudioListener", "AudioDestinationNode",
        "AudioContext", "AudioBufferSourceNode", "AudioBuffer", "Attr", "AnimationTimeline",
        "AnimationPlaybackEvent", "AnimationEvent", "AnimationEffect", "Animation",
        "AnalyserNode", "AbortSignal", "AbortController", "window", "self", "document",
        "name", "location", "customElements", "history", "navigation", "locationbar",
        "menubar", "personalbar", "scrollbars", "statusbar", "toolbar", "status",
        "closed", "frames", "length", "top", "opener", "parent", "frameElement",
        "navigator", "origin", "external", "screen", "innerWidth", "innerHeight",
        "scrollX", "pageXOffset", "scrollY", "pageYOffset", "visualViewport",
        "screenX", "screenY", "outerWidth", "outerHeight", "devicePixelRatio",
        "clientInformation", "screenLeft", "screenTop", "styleMedia", "onsearch",
        "isSecureContext", "crossOriginIsolated", "performance", "onappinstalled",
        "onbeforeinstallprompt", "crypto", "indexedDB", "sessionStorage", "localStorage",
        "onbeforexrselect", "onabort", "onbeforeinput", "onbeforematch", "onbeforetoggle",
        "onblur", "oncancel", "oncanplay", "oncanplaythrough", "onchange", "onclick",
        "onclose", "oncontentvisibilityautostatechange", "oncontextlost", "oncontextmenu",
        "oncontextrestored", "oncuechange", "ondblclick", "ondrag", "ondragend",
        "ondragenter", "ondragleave", "ondragover", "ondragstart", "ondrop",
        "ondurationchange", "onemptied", "onended", "onerror", "onfocus",
        "onformdata", "oninput", "oninvalid", "onkeydown", "onkeypress", "onkeyup",
        "onload", "onloadeddata", "onloadedmetadata", "onloadstart", "onmousedown",
        "onmouseenter", "onmouseleave", "onmousemove", "onmouseout", "onmouseover",
        "onmouseup", "onmousewheel", "onpause", "onplay", "onplaying", "onprogress",
        "onratechange", "onreset", "onresize", "onscroll", "onsecuritypolicyviolation",
        "onseeked", "onseeking", "onselect", "onslotchange", "onstalled", "onsubmit",
        "onsuspend", "ontimeupdate", "ontoggle", "onvolumechange", "onwaiting",
        "onwebkitanimationend", "onwebkitanimationiteration", "onwebkitanimationstart",
        "onwebkittransitionend", "onwheel", "onauxclick", "ongotpointercapture",
        "onlostpointercapture", "onpointerdown", "onpointermove", "onpointerrawupdate",
        "onpointerup", "onpointercancel", "onpointerover", "onpointerout", "onpointerenter",
        "onpointerleave", "onselectstart", "onselectionchange", "onanimationend",
        "onanimationiteration", "onanimationstart", "ontransitionrun", "ontransitionstart",
        "ontransitionend", "ontransitioncancel", "onafterprint", "onbeforeprint",
        "onbeforeunload", "onhashchange", "onlanguagechange", "onmessage",
        "onmessageerror", "onoffline", "ononline", "onpagehide", "onpageshow",
        "onpopstate", "onrejectionhandled", "onstorage", "onunhandledrejection",
        "onunload", "oncopy", "oncut", "onpaste", "originAgentCluster", "speechSynthesis"
    ]

    doc_globals = [
        "location", "constructor", "hasChildNodes", "insertBefore", "appendChild",
        "removeChild", "replaceChild", "nodeType", "nodeName", "baseURI", "isConnected",
        "ownerDocument", "parentNode", "parentElement", "childNodes", "firstChild",
        "lastChild", "previousSibling", "nextSibling", "nodeValue", "textContent",
        "ELEMENT_NODE", "ATTRIBUTE_NODE", "TEXT_NODE", "CDATA_SECTION_NODE",
        "ENTITY_REFERENCE_NODE", "ENTITY_NODE", "PROCESSING_INSTRUCTION_NODE",
        "COMMENT_NODE", "DOCUMENT_NODE", "DOCUMENT_TYPE_NODE", "DOCUMENT_FRAGMENT_NODE",
        "NOTATION_NODE", "DOCUMENT_POSITION_DISCONNECTED", "DOCUMENT_POSITION_PRECEDING",
        "DOCUMENT_POSITION_FOLLOWING", "DOCUMENT_POSITION_CONTAINS",
        "DOCUMENT_POSITION_CONTAINED_BY", "DOCUMENT_POSITION_IMPLEMENTATION_SPECIFIC",
        "addEventListener", "dispatchEvent", "removeEventListener", "implementation",
        "URL", "documentURI", "compatMode", "characterSet", "charset", "inputEncoding",
        "contentType", "doctype", "documentElement", "xmlEncoding", "xmlVersion",
        "xmlStandalone", "createElement", "createElementNS", "createDocumentFragment",
        "createTextNode", "createCDATASection", "createComment", "createProcessingInstruction",
        "importNode", "adoptNode", "createAttribute", "createAttributeNS",
        "createEvent", "createRange", "createNodeIterator", "createTreeWalker",
        "getElementsByTagName", "getElementsByTagNameNS", "getElementsByClassName",
        "getElementById", "children", "firstElementChild", "lastElementChild",
        "childElementCount", "prepend", "append", "replaceChildren", "querySelector",
        "querySelectorAll", "elementsFromPoint", "elementFromPoint", "caretRangeFromPoint",
        "getSelection", "hasFocus", "title", "dir", "body", "head", "images", "embeds",
        "plugins", "links", "forms", "scripts", "defaultView", "designMode", "onreadystatechange",
        "cookie", "lastModified", "referrer", "domain", "hidden", "visibilityState",
        "fullscreenEnabled", "fullscreenElement", "onfullscreenchange", "onfullscreenerror",
        "webkitIsFullScreen", "webkitCurrentFullScreenElement", "webkitFullscreenEnabled",
        "webkitFullscreenElement", "onwebkitfullscreenchange", "onwebkitfullscreenerror",
        "rootElement", "pictureInPictureEnabled", "pictureInPictureElement", "fonts"
    ]

    nav_globals = [
        "vendorSub", "productSub", "vendor", "maxTouchPoints", "scheduling",
        "userActivation", "geolocation", "doNotTrack", "webkitTemporaryStorage",
        "webkitPersistentStorage", "windowControlsOverlay", "hardwareConcurrency",
        "cookieEnabled", "appCodeName", "appName", "appVersion", "platform",
        "product", "userAgent", "language", "languages", "onLine", "webdriver",
        "plugins", "mimeTypes", "pdfViewerEnabled", "connection", "getGamepads",
        "javaEnabled", "sendBeacon", "vibrate", "constructor", "cpuPerformance",
        "bluetooth", "clipboard", "credentials", "keyboard", "managed",
        "mediaDevices", "serviceWorker", "virtualKeyboard", "wakeLock",
        "deviceMemory", "userAgentData", "locks", "storage", "gpu", "login",
        "ink", "mediaCapabilities", "permissions", "devicePosture", "hid",
        "mediaSession", "presentation", "serial", "usb", "xr", "storageBuckets",
        "canShare", "share", "clearAppBadge", "getBattery", "getUserMedia",
        "requestMIDIAccess", "requestMediaKeySystemAccess", "setAppBadge",
        "webkitGetUserMedia", "getInstalledRelatedApps", "registerProtocolHandler",
        "unregisterProtocolHandler"
    ]

    return {
        "CF0100": False,
        "CF0101": False,
        "CF0102": {
            "depth": random.randint(4500, 14000),
            "msg": "Maximum call stack size exceeded",
            "name": "RangeError",
            "length": 944
        },
        "CF0103": True,
        "CF0104": False,
        "CF0105": 4,
        "CF0106": now_ms,
        "CF0107": False,
        "CF0108": -120,
        "CF0109": False,
        "CF0110": {
            "state": "prompt",
            "permission": "default"
        },
        "CF0111": ["en-US", "en", "ar", "hy"],
        "CF0112": False,
        "CF0113": False,
        "CF0114": [
            "Google Inc. (AMD)",
            "ANGLE (AMD, AMD Radeon(TM) R5 240 (0x00006611) Direct3D11 vs_5_0 ps_5_0, D3D11)",
            "WebKit",
            "WebKit WebGL",
            "WebGL 1.0 (OpenGL ES 2.0 Chromium)"
        ],
        "CF0115": user_agent,
        "CF0116": {
            "brands": [
                {"brand": "Google Chrome", "version": "155"},
                {"brand": "Chromium", "version": "155"},
                {"brand": "Not(A:Brand", "version": "24"}
            ],
            "mobile": False,
            "platform": "Windows"
        },
        "CF0117": "Google Inc.",
        "CF0118": "Win32",
        "CF0119": "20030107",
        "CF0120": {
            "width": 1280,
            "height": 1024,
            "availW": 1280,
            "availH": 1024,
            "clrDepth": 24,
            "pxDepth": 24,
            "pxRatio": 1,
            "outerW": 1280,
            "outerH": 984
        },
        "CF0121": [
            "Google Inc. (AMD)",
            "ANGLE (AMD, AMD Radeon(TM) R5 240 (0x00006611) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ],
        "CF0122": {
            "ogg": "",
            "h264": "probably",
            "webm": "probably"
        },
        "CF0123": True,
        "CF0124": [
            "PDF Viewer:Portable Document Format:internal-pdf-viewer",
            "Chrome PDF Viewer:Portable Document Format:internal-pdf-viewer",
            "Chromium PDF Viewer:Portable Document Format:internal-pdf-viewer",
            "Microsoft Edge PDF Viewer:Portable Document Format:internal-pdf-viewer",
            "WebKit built-in PDF:Portable Document Format:internal-pdf-viewer"
        ],
        "CF0125": True,
        "CF0126": [
            "Portable Document Format~application/pdf~pdf",
            "Portable Document Format~text/pdf~pdf"
        ],
        "CF0127": True,
        "CF0128": "function close() { [native code] }",
        "CF0129": "[object External]",
        "CF0130": window_globals,
        "CF0131": doc_globals,
        "CF0132": nav_globals,
        "CF0133": ["lang", "class", "data-js-focus-visible"],
        "CF0134": "object",
        "CF0135": False,
        "CF0136": 33,
        "CF0137": False,
        "CF0138": False,
        "CF0139": False,
        "CF0140": False,
        "CF0141": False,
        "CF0142": True,
        "CF0143": True,
        "CF0144": [True, True],
        "CF0145": user_agent,
        "CF0146": False,
        "CF0147": False,
        "CF0148": f"https://{host}/"
    }


def generate_mouse_trail(target_x: int, duration_sec: float = 1.1) -> List[int]:
    """
    Synthesizes authentic human slider drag coordinates matching the HAR trace:
    The slide widget pushes: [clientX, sliderX, clientX, sliderX, ...]
    Pairs of movement coordinates sampled across 6 to 10 progressive steps.
    """
    trail: List[int] = []
    num_steps = random.randint(5, 8)
    origin_x = random.randint(10, 25)

    for i in range(1, num_steps + 1):
        frac = i / float(num_steps)
        ease = math.sin(frac * math.pi / 2.0)
        cur_x = int(round(ease * target_x))
        cur_x = max(0, min(DEFAULT_CONFIG["CANVAS_WIDTH"], cur_x))

        client_x = origin_x + random.randint(0, 5)
        trail.append(client_x)
        trail.append(cur_x)

    if len(trail) >= 2:
        trail[-1] = target_x

    return trail[:80]


def solve_slider_alignment(bg_b64_or_url: str, user_agent: str, proxy: Optional[str] = None) -> int:
    """
    Detects the horizontal alignment offset X of the target object in the background image.
    The background image is 600px wide (or native) rendered onto a 250px canvas.
    Computes alpha channel / silhouette bounding box and scales to canvas coordinate X.
    """
    if bg_b64_or_url.startswith("data:image"):
        b64_data = bg_b64_or_url.split(",", 1)[1]
        bg_bytes = base64.b64decode(b64_data)
    elif bg_b64_or_url.startswith("http"):
        req = urllib.request.Request(bg_b64_or_url, headers={"User-Agent": user_agent})
        opener = create_opener(proxy)
        with opener.open(req, timeout=15) as resp:
            bg_bytes = resp.read()
    else:
        bg_bytes = base64.b64decode(bg_b64_or_url)

    if Image is not None and np is not None:
        img = Image.open(BytesIO(bg_bytes))
        arr = np.array(img)
        w, h = img.size

        if arr.shape[2] >= 4:
            alpha = arr[:, :, 3]
            y_idx, x_idx = np.where(alpha > 10)
            if len(x_idx) > 0:
                x_min, x_max = x_idx.min(), x_idx.max()
                scale = float(DEFAULT_CONFIG["CANVAS_WIDTH"]) / float(w)
                center_x = ((x_min + x_max) / 2.0) * scale
                return int(round(center_x))

        if cv2 is not None:
            gray = cv2.cvtColor(arr, cv2.COLOR_RGBA2GRAY if arr.shape[2] >= 4 else cv2.COLOR_RGB2GRAY)
            edges = cv2.Canny(gray, 40, 120)
            col_sum = np.sum(edges, axis=0)
            peak_x = int(np.argmax(col_sum[30:-30])) + 30
            scale = float(DEFAULT_CONFIG["CANVAS_WIDTH"]) / float(w)
            return int(round(peak_x * scale))

    return 140


def create_opener(proxy_str: Optional[str] = None) -> urllib.request.OpenerDirector:
    """Builds a urllib opener with proxy support if specified."""
    formatted = format_proxy(proxy_str)
    if formatted:
        handlers = [
            urllib.request.ProxyHandler({"http": formatted, "https": formatted})
        ]
        return urllib.request.build_opener(*handlers)
    return urllib.request.build_opener()


class CaptchaFoxSolver:
    """
    CaptchaFox Protocol Reverse-Engineered Solver.
    Supports One-Click bypass, interactive Slider alignment, and optional proxy.
    """

    def __init__(
        self,
        sitekey: str = "",
        site_url: str = "",
        captcha_type: str = "auto",
        api_base: Optional[str] = None,
        pulse: Optional[str] = None,
        proxy: Optional[str] = None,
        user_agent: Optional[str] = None
    ):
        self.sitekey = sitekey or DEFAULT_CONFIG["DEFAULT_SITEKEY"]
        self.site_url = site_url or DEFAULT_CONFIG["DEFAULT_SITE_URL"]
        self.captcha_type = (captcha_type or "auto").lower()
        self.proxy = format_proxy(proxy)
        self.user_agent = user_agent or DEFAULT_CONFIG["USER_AGENT"]
        self.opener = create_opener(self.proxy)

        # Parse host and pathname
        parsed = urllib.parse.urlparse(self.site_url)
        self.host = parsed.netloc or DEFAULT_CONFIG["DEFAULT_HOST"]
        self.origin = f"{parsed.scheme}://{self.host}"
        self.pathname = parsed.path or "/"

        # Auto-configure enterprise domains
        if api_base:
            self.api_base = api_base
        elif "web.de" in self.host or "1und1" in self.host or "gmx" in self.host:
            self.api_base = DEFAULT_CONFIG["ENTERPRISE_API_BASE"]
        else:
            self.api_base = DEFAULT_CONFIG["API_BASE"]

        # Pulse hash selection
        if pulse:
            self.pulse = pulse
        elif self.api_base == DEFAULT_CONFIG["ENTERPRISE_API_BASE"]:
            self.pulse = DEFAULT_CONFIG["PULSE_ENTERPRISE"]
        else:
            self.pulse = DEFAULT_CONFIG["PULSE_STANDARD"]

        self.session_hash: Optional[str] = None
        self.pow_param: Optional[List[Any]] = None
        self.theme: Optional[dict] = None

    def _request(self, method: str, url: str, body: Optional[bytes] = None) -> dict:
        """Dispatches HTTP request carrying genuine browser and pulse headers."""
        headers = {
            "X-Pulse": self.pulse,
            "X-Spark": DEFAULT_CONFIG["SPARK_SDK"],
            "User-Agent": self.user_agent,
            "Origin": self.origin,
            "Referer": f"{self.origin}/"
        }
        if body is not None:
            headers["Content-Type"] = "text/plain"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with self.opener.open(req, timeout=20) as resp:
                resp_bytes = resp.read()
                return json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"CaptchaFox HTTP {e.code} on {url}: {err_msg}")

    def fetch_config(self) -> dict:
        """Retrieves session hash 'h' and initial PoW challenge 'm'."""
        endpoint = f"{self.api_base}/captcha/{self.sitekey}/config?site={self.origin}{self.pathname}"
        res = self._request("GET", endpoint)
        self.session_hash = res.get("h")
        self.pow_param = res.get("m")
        self.theme = res.get("theme")
        return res

    def solve(self) -> Tuple[str, str]:
        """
        Executes bypass workflow.
        Returns Tuple[token: str, resolved_type: str]
        """
        self.fetch_config()
        if not self.session_hash:
            raise ValueError("Failed to obtain session hash 'h' from config endpoint")

        pow_nonce = 0
        if self.pow_param and len(self.pow_param) >= 3:
            pow_nonce = solve_pow(self.pow_param[1], self.pow_param[2])

        telemetry = generate_client_telemetry(self.host, self.user_agent)
        lang = "de" if "web.de" in self.host or "gmx" in self.host else DEFAULT_CONFIG["DEFAULT_LANG"]
        challenge_payload = {
            "lng": lang,
            "h": self.session_hash,
            "cs": telemetry,
            "host": self.host,
            "k": pow_nonce,
            "type": "slide"
        }

        challenge_wire = encode_wire_payload(challenge_payload)
        challenge_url = f"{self.api_base}/captcha/{self.sitekey}/challenge"
        challenge_res = self._request("POST", challenge_url, body=challenge_wire)

        # Check for immediate One-Click clearance
        if challenge_res.get("solved") is True:
            token = challenge_res["token"]
            return token, "normal"

        if self.captcha_type in ("normal", "click", "checkbox"):
            raise RuntimeError(
                f"Requested 'normal' captcha type, but server served an interactive slider challenge: {challenge_res.get('type')}"
            )

        # Step 3: Handle Interactive Slider Challenge
        if challenge_res.get("failed"):
            raise RuntimeError("CaptchaFox challenge rejected initial submission")

        challenge_data = challenge_res.get("challenge")
        if not challenge_data or "bg" not in challenge_data:
            raise ValueError(f"Unrecognized challenge response structure: {challenge_res}")

        bg_url_or_b64 = challenge_data["bg"]
        active_h = challenge_res.get("h", self.session_hash)

        # Calculate exact slider alignment position
        solution_x = solve_slider_alignment(bg_url_or_b64, self.user_agent, self.proxy)
        duration = round(random.uniform(0.95, 1.45), 2)
        trail = generate_mouse_trail(solution_x, duration)

        # Solve secondary PoW 'j' if attached
        sec_pow = challenge_res.get("j")
        verify_k = 0
        if sec_pow and len(sec_pow) >= 3:
            verify_k = solve_pow(sec_pow[1], sec_pow[2])

        # Step 4: Submit Verification Payload
        verify_payload = {
            "sk": self.sitekey,
            "mv": trail,
            "t": duration,
            "p": solution_x,
            "h": active_h,
            "cs": telemetry,
            "k": verify_k,
            "type": "slide",
            "host": self.host
        }

        verify_wire = encode_wire_payload(verify_payload)
        verify_url = f"{self.api_base}/captcha/verify"
        verify_res = self._request("POST", verify_url, body=verify_wire)

        if verify_res.get("solved") is True:
            return verify_res["token"], "slider"

        raise RuntimeError(f"Slider verification failed: {verify_res}")


def main():
    parser = argparse.ArgumentParser(
        description="CaptchaFox Reverse-Engineered Protocol Solver (One-Click & Slider)"
    )
    parser.add_argument("--sitekey", "-k", default=DEFAULT_CONFIG["DEFAULT_SITEKEY"], help="Target sitekey")
    parser.add_argument("--url", "-u", default=DEFAULT_CONFIG["DEFAULT_SITE_URL"], help="Target page URL")
    parser.add_argument("--type", "-t", default="auto", choices=["auto", "normal", "slider", "click", "slide"], help="Captcha type: normal or slider")
    parser.add_argument("--api", "-a", default=None, help="Custom API base (e.g. https://mam-api.captchafox.com)")
    parser.add_argument("--pulse", default=None, help="Custom pulse hash (e.g. 2bd77e6f8a17bc0e)")
    parser.add_argument("--proxy", "-p", default=None, help="Proxy in host:port:user:pass or http:// URL format (optional)")
    parser.add_argument("--out", "-o", default=None, help="Output file to append token")
    args = parser.parse_args()

    solver = CaptchaFoxSolver(
        sitekey=args.sitekey,
        site_url=args.url,
        captcha_type=args.type,
        api_base=args.api,
        pulse=args.pulse,
        proxy=args.proxy
    )
    token, mode = solver.solve()
    print(token)

    if args.out:
        with open(args.out, "a", encoding="utf-8") as f:
            f.write(f"{token}\n")


if __name__ == "__main__":
    main()
