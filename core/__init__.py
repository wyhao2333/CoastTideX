"""
CoastTideX 核心计算包
包含 FES2022 潮位解算引擎、MDT/EGM2008 基准转换引擎及通用工具函数。
"""

from .datum_engine import DatumTransformer
from .raster_engine import (
    RasterTideEngine, RasterInfo, RasterResultSummary,
    RasterMemoryLimitError, estimate_control_node_memory
)
from .tide_cache import (
    write_tide_cache, read_tide_cache, calculate_inundation_from_tide_cache,
    estimate_tide_cache_size, is_cache_complete
)
from .batch_raster_engine import (
    BatchRasterEngine, BatchManifest,
    JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE
)
from .utils import normalize_longitude, COASTAL_PRESETS

try:
    from .tide_engine import FESTidePredictor, HAS_PYFES
except ImportError:
    FESTidePredictor = None
    HAS_PYFES = False

__version__ = "1.5.0-alpha"

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
    'write_tide_cache',
    'read_tide_cache',
    'calculate_inundation_from_tide_cache',
    'estimate_tide_cache_size',
    'is_cache_complete',
    'BatchRasterEngine',
    'BatchManifest',
    'JOB_MODE_TIDE_ONLY',
    'JOB_MODE_TIDE_AND_INUNDATION',
    'JOB_MODE_INUNDATION_FROM_CACHE',
    'normalize_longitude',
    'COASTAL_PRESETS'
]


