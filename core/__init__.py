"""
CoastTideX 核心计算包 / Core Computation Package (v1.6 Beta)
包含全球潮汐模型解算 (FES2022b)、垂直高程基准转换 (MDT/EGM2008/WGS84)、
空间栅格解算调度 (Raster Engine)、Tide Cache 缓存管理体系、
潜在天文潮淹没频率反演 (Inundation)、潜在天文潮露出时间域全要素分析 (Exposure)
以及文件夹级自动化批处理调度引擎 (Batch Raster Engine)。

Provides core computational components for global ocean tide modeling (FES2022b),
vertical datum transformations (MDT/EGM2008/WGS84), spatial raster simulation,
Tide Cache management, potential astronomical tide inundation frequency calculation,
potential tidal exposure duration and events analysis, and batch processing.
"""

from .datum_engine import DatumTransformer
from .raster_engine import (
    RasterTideEngine, RasterInfo, RasterResultSummary,
    RasterMemoryLimitError, estimate_control_node_memory, LeafCellSpatialIndex,
    ExistingOutputError
)
from .exposure_engine import ExposureProductPaths
from .tide_cache import (
    write_tide_cache, read_tide_cache, calculate_inundation_from_tide_cache,
    calculate_exposure_from_tide_cache,
    estimate_tide_cache_size, is_cache_complete,
    validate_tide_cache_structure, inspect_tide_cache_metadata,
    TideCacheIntegrityError, TideCacheCompatibilityError
)
from .batch_raster_engine import (
    BatchRasterEngine, BatchManifest,
    JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE,
    JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_EXPOSURE_FROM_CACHE, JOB_MODE_ALL
)
from .utils import normalize_longitude, COASTAL_PRESETS

try:
    from .tide_engine import FESTidePredictor, HAS_PYFES
except ImportError:
    FESTidePredictor = None
    HAS_PYFES = False

__version__ = "1.6.0-beta"

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
    'LeafCellSpatialIndex',
    'ExistingOutputError',
    'ExposureProductPaths',
    'write_tide_cache',
    'read_tide_cache',
    'calculate_inundation_from_tide_cache',
    'calculate_exposure_from_tide_cache',
    'estimate_tide_cache_size',
    'is_cache_complete',
    'validate_tide_cache_structure',
    'inspect_tide_cache_metadata',
    'TideCacheIntegrityError',
    'TideCacheCompatibilityError',
    'BatchRasterEngine',
    'BatchManifest',
    'JOB_MODE_TIDE_ONLY',
    'JOB_MODE_TIDE_AND_INUNDATION',
    'JOB_MODE_INUNDATION_FROM_CACHE',
    'JOB_MODE_TIDE_AND_EXPOSURE',
    'JOB_MODE_EXPOSURE_FROM_CACHE',
    'JOB_MODE_ALL',
    'normalize_longitude',
    'COASTAL_PRESETS'
]


