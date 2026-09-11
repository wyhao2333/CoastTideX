"""
CoastTideX 核心计算包
包含 FES2022 潮位解算引擎、MDT/EGM2008 基准转换引擎及通用工具函数。
"""

from .datum_engine import DatumTransformer
from .utils import normalize_longitude, COASTAL_PRESETS

try:
    from .tide_engine import FESTidePredictor, HAS_PYFES
except ImportError:
    FESTidePredictor = None
    HAS_PYFES = False

__all__ = [
    'FESTidePredictor',
    'HAS_PYFES',
    'DatumTransformer',
    'normalize_longitude',
    'COASTAL_PRESETS'
]
