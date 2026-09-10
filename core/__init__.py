"""
CoastTideX 核心计算包
包含 FES2022 潮位解算引擎、MDT/EGM2008 基准转换引擎及通用工具函数。
"""

from .tide_engine import FESTidePredictor
from .datum_engine import DatumTransformer
from .utils import normalize_longitude, COASTAL_PRESETS

__all__ = [
    'FESTidePredictor',
    'DatumTransformer',
    'normalize_longitude',
    'COASTAL_PRESETS'
]
