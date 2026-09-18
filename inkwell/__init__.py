"""Inkwell — 原生 Markdown 阅读器。"""

import os
import sys
import traceback

__version__ = "1.4.3"
APP_NAME = "Inkwell"

DEBUG = os.environ.get("INKWELL_DEBUG") == "1"


def log_exception():
    """DEBUG 模式下把当前异常堆栈打到 stderr，供排查；不改变异常的传播/吞没方式。"""
    if DEBUG:
        traceback.print_exc(file=sys.stderr)
