"""
CoastTideX 核心计算包
包含 FES2022 潮位解算引擎、MDT/EGM2008 基准转换引擎及通用工具函数。
"""

from .datum_engine import DatumTransformer
from .raster_engine import (
    RasterTideEngine, RasterInfo, RasterResultSummary,
    RasterMemoryLimitError, estimate_control_node_memory
)
from .utils import normalize_longitude, COASTAL_PRESETS

try:
    from .tide_engine import FESTidePredictor, HAS_PYFES
except ImportError:
    FESTidePredictor = None
    HAS_PYFES = False

__version__ = "1.4.0"

__all__ = [
    '__version__',
    'FESTidePredictor',
    'HAS_PYFES',
    'DatumTransformer',
    'RasterTideEngine',
    'RasterInfo',
    'RasterResultSummary',
    'RasterMemoryLimitError',
    'estimate_control_node_memory',
    'normalize_longitude',
    'COASTAL_PRESETS'
]

