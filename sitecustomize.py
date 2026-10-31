"""
Compatibility layer for third-party weights (such as yolov8n-face.pt)
trained with older versions of Ultralytics expecting 'ultralytics.yolo'.
"""
import sys
import types

def setup_ultralytics_compat():
    if "ultralytics.yolo" not in sys.modules:
        yolo_mod = types.ModuleType("ultralytics.yolo")
        yolo_mod.v8 = types.ModuleType("ultralytics.yolo.v8")
        yolo_mod.v8.detect = types.ModuleType("ultralytics.yolo.v8.detect")
        yolo_mod.data = types.ModuleType("ultralytics.yolo.data")
        yolo_mod.utils = types.ModuleType("ultralytics.yolo.utils")
        sys.modules["ultralytics.yolo"] = yolo_mod
        sys.modules["ultralytics.yolo.v8"] = yolo_mod.v8
        sys.modules["ultralytics.yolo.v8.detect"] = yolo_mod.v8.detect
        sys.modules["ultralytics.yolo.data"] = yolo_mod.data
        sys.modules["ultralytics.yolo.utils"] = yolo_mod.utils

setup_ultralytics_compat()
