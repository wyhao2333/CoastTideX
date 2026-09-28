"""
CoastTideX 垂直基准转换引擎 (Datum Transformation Engine)
实现从平均海平面 (MSL) 到 MDT 原始参考面 (GOCO06s / EIGEN-6C4)、EGM2008 大地水准面及 WGS84 空间几何椭球面的严密科学转换。

科学转换原理:
    1. 全球开阔大洋 (GOCO06s 基准):
       H_MDT_REF = Tide_MSL + MDT_CLS22
       H_EGM2008 = H_MDT_REF + (N_GOCO06s - N_EGM2008)
    2. 地中海与黑海 (EIGEN-6C4 基准):
       H_MDT_REF = Tide_MSL + MDT_CMEMS2020
       H_EGM2008 = H_MDT_REF + (N_EIGEN6C4 - N_EGM2008)
    3. WGS84 几何空间三维椭球高:
       h_WGS84   = H_EGM2008 + N_EGM2008
"""

import os
import warnings
import numpy as np
import xarray as xr
import rasterio
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import map_coordinates

warnings.filterwarnings("ignore", category=PendingDeprecationWarning)

from .utils import normalize_longitude, load_app_config, resolve_project_path


class DatumDataError(FileNotFoundError):
    """缺少垂直基准转换所需的关键模型数据文件时抛出的明确科学异常"""
    pass


# 地中海多边形闭合顶点 (严格排除大西洋直布罗陀西侧加的斯湾、比斯开湾、欧洲内陆与红海)
_MED_POLYGON_VERTICES = [
    (-5.6, 35.85),  # 直布罗陀海峡南端 (休达/斯帕特尔角)
    (-5.6, 36.18),  # 直布罗陀海峡北端 (塔里法/直布罗陀)
    (-4.0, 36.8),   # 马拉加沿岸
    (-2.0, 36.8),   # 阿尔梅里亚
    (-0.5, 38.5),   # 阿利坎特
    (0.5, 40.5),    # 卡斯特利翁
    (3.2, 42.4),    # 克雷乌斯角 (西法边界)
    (3.5, 43.5),    # 利翁湾
    (5.5, 43.4),    # 马赛
    (7.5, 43.7),    # 尼斯 / 摩纳哥
    (9.5, 44.4),    # 热那亚湾
    (12.5, 45.6),   # 威尼斯
    (13.8, 45.8),   # 的里雅斯特湾
    (15.0, 44.5),   # 亚得里亚海克罗地亚海岸
    (19.5, 42.0),   # 黑山 / 阿尔巴尼亚
    (20.0, 39.5),   # 爱奥尼亚海
    (23.0, 38.0),   # 科林斯湾 / 雅典
    (24.0, 41.0),   # 爱琴海北部
    (26.5, 41.0),   # 色雷斯
    (29.5, 41.2),   # 博斯普鲁斯海峡南口 (马尔马拉海端)
    (29.0, 40.5),   # 马尔马拉海南岸
    (27.0, 40.0),   # 达达尼尔海峡南口 / 爱琴海东岸
    (28.0, 37.0),   # 土耳其西南岸
    (31.0, 36.8),   # 安塔利亚
    (34.5, 36.8),   # 梅尔辛
    (36.2, 36.0),   # 伊斯肯德伦湾 / 叙利亚拉塔基亚
    (36.2, 33.5),   # 黎巴嫩沿岸
    (35.0, 31.5),   # 加沙 / 以色列沿岸
    (32.5, 31.3),   # 塞得港 / 尼罗河三角洲东岸
    (30.0, 31.3),   # 亚历山大港
    (25.0, 31.6),   # 萨卢姆 (埃利边界)
    (20.0, 30.0),   # 锡德拉湾最南端
    (15.0, 30.2),   # 锡德拉湾西岸
    (11.5, 33.0),   # 突尼斯南部加贝斯湾
    (11.0, 37.5),   # 邦角
    (8.5, 37.0),    # 突阿尔边界
    (3.0, 36.8),    # 阿尔及尔
    (-2.0, 35.2),   # 梅利利亚
    (-5.6, 35.85)   # 闭合于直布罗陀南口
]

# 黑海与亚速海多边形闭合顶点
_BLK_POLYGON_VERTICES = [
    (27.2, 41.5),   # 保加利亚/土耳其沿岸
    (27.5, 42.5),   # 布尔加斯
    (28.5, 44.0),   # 康斯坦察 (罗马尼亚)
    (29.8, 45.3),   # 多瑙河三角洲
    (30.7, 46.5),   # 敖德萨 (乌克兰)
    (31.5, 46.6),   # 奥恰基夫
    (33.5, 46.0),   # 卡尔基尼特湾
    (35.0, 45.4),   # 刻赤海峡西侧
    (35.0, 47.3),   # 亚速海西北
    (39.3, 47.3),   # 塔甘罗格 (亚速海东北)
    (39.0, 45.5),   # 亚速海东南
    (36.6, 45.2),   # 刻赤海峡东侧
    (38.0, 44.5),   # 新罗西斯克
    (40.0, 43.5),   # 索契
    (41.7, 42.0),   # 格鲁吉亚巴统
    (41.3, 41.4),   # 霍帕
    (39.7, 41.0),   # 特拉布宗
    (36.3, 41.3),   # 萨姆松
    (35.0, 42.0),   # 锡诺普
    (32.0, 41.8),   # 宗古尔达克
    (29.1, 41.2),   # 博斯普鲁斯海峡北口
    (28.0, 41.3),   # 基伊柯伊
    (27.2, 41.5)    # 闭合
]

def _points_in_polygon(lons: np.ndarray, lats: np.ndarray, poly_verts: list[tuple[float, float]]) -> np.ndarray:
    """
    纯 NumPy 向量化射线交叉法 (Ray-Casting PNPOLY 算法)。
    判断空间散点是否位于任意闭合多边形内部，零外部库依赖 (不依赖 matplotlib 或 shapely)。
    """
    poly = np.asarray(poly_verts, dtype=float)
    x = np.asarray(lons, dtype=float)
    y = np.asarray(lats, dtype=float)
    inside = np.zeros(x.shape, dtype=bool)
    n_vert = len(poly)
    j = n_vert - 1
    for i in range(n_vert):
        xi, yi = poly[i, 0], poly[i, 1]
        xj, yj = poly[j, 0], poly[j, 1]
        cond_y = (yi > y) != (yj > y)
        if np.any(cond_y):
            intersect_x = (xj - xi) * (y[cond_y] - yi) / (yj - yi) + xi
            inside[cond_y] ^= (x[cond_y] < intersect_x)
        j = i
    return inside


def _broadcast_pointwise_inputs(
    tide_msl_m: float | np.ndarray,
    lons: float | np.ndarray,
    lats: float | np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    严密点位输入广播辅助函数。
    仅允许语义明确的形状组合：
      - 1 / 1 / 1 (全部为标量或单点)
      - N / N / N (全部为长度匹配的对应点)
      - N / 1 / 1 (单点坐标，N 个时间步的潮位时序)
      - 1 / N / N (单时刻潮位，N 个空间位置)
    其它无法明确广播或存在数据截断风险的组合 (如 3 / 2 / 3) 必须严格抛出 ValueError。
    """
    t_arr = np.atleast_1d(np.asarray(tide_msl_m, dtype=float))
    lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
    lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

    len_t = len(t_arr)
    len_lon = len(lons_arr)
    len_lat = len(lats_arr)

    if len_lon != len_lat:
        if len_lon == 1 and len_lat > 1:
            lons_arr = np.full(len_lat, lons_arr[0], dtype=float)
            len_lon = len_lat
        elif len_lat == 1 and len_lon > 1:
            lats_arr = np.full(len_lon, lats_arr[0], dtype=float)
            len_lat = len_lon
        else:
            raise ValueError(f"经纬度坐标长度不匹配且无法广播: len(lon)={len_lon}, len(lat)={len_lat}")

    n_space = len_lon
    if len_t == n_space:
        return t_arr, lons_arr, lats_arr
    elif len_t == 1 and n_space > 1:
        t_arr = np.full(n_space, t_arr[0], dtype=float)
        return t_arr, lons_arr, lats_arr
    elif n_space == 1 and len_t > 1:
        lons_arr = np.full(len_t, lons_arr[0], dtype=float)
        lats_arr = np.full(len_t, lats_arr[0], dtype=float)
        return t_arr, lons_arr, lats_arr
    else:
        raise ValueError(
            f"无法安全广播输入的点位形状: tide={len_t}, lon={len_lon}, lat={len_lat}。"
            "仅允许 1/1/1, N/N/N, N/1/1, 1/N/N 语义对齐输入，严禁静默截断或单元素填充。"
        )


def get_mdt_reference_geoid(
    lon: float | np.ndarray,
    lat: float | np.ndarray,
    source_mask_data: np.ndarray = None,
    source_mask_inv_transform = None,
    source_mask_nodata: float = None,
    source_mask_transformer = None,
    return_qc: bool = False
) -> str | np.ndarray | tuple[str | np.ndarray, str | np.ndarray]:
    """
    严密判定指定空间坐标处 CNES-CLS22 Hybrid MDT 的参考重力场大地水准面基准。
    标准权威分类规范 (Canonical Specification):
      0   = INVALID / UNKNOWN -> 回退多边形检验
      1   = GOCO06s / CNES-CLS22 global ocean -> 'AUTHORITATIVE_MASK'
      2   = CMEMS Mediterranean / EIGEN-6C4 -> 'AUTHORITATIVE_MASK'
      3   = CMEMS Black Sea / EIGEN-6C4 -> 'AUTHORITATIVE_MASK'
      255 = NoData -> 回退多边形检验

    逐像元回退机制 (Pixelwise Fallback):
      - 仅当掩膜像元具有有效明确分类 (1/2/3) 且非 NoData 时，赋予对应权威基准并标记 'AUTHORITATIVE_MASK'；
      - 若掩膜为 0/255/NoData/超出掩膜范围/未知类别，仅对该点独立执行几何多边形回退：
        - 若落在地中海/黑海多边形内：'EIGEN-6C4', 标记 'QC_DATUM_SOURCE_APPROX'
        - 若落在多边形外（大洋）：'GOCO06s', 标记 'QC_DATUM_SOURCE_APPROX'
      - 若未提供掩膜数据：所有有效点执行多边形判定，大洋标 'NORMAL'，地中海/黑海标 'QC_DATUM_SOURCE_APPROX'；
      - 真正非法坐标 (NaN/Inf/越界)：赋值 'INVALID', 标记 'INVALID'。
    """
    is_scalar = np.isscalar(lon) and np.isscalar(lat)
    lons_arr = np.atleast_1d(normalize_longitude(lon, to_360=False))
    lats_arr = np.atleast_1d(np.asarray(lat, dtype=float))

    if len(lons_arr) != len(lats_arr):
        if len(lons_arr) == 1 and len(lats_arr) > 1:
            lons_arr = np.full(len(lats_arr), lons_arr[0])
        elif len(lats_arr) == 1 and len(lons_arr) > 1:
            lats_arr = np.full(len(lons_arr), lats_arr[0])
        else:
            raise ValueError(f"经纬度坐标长度不一致: len(lon)={len(lons_arr)}, len(lat)={len(lats_arr)}")

    n_pts = len(lons_arr)
    invalid_mask = ~np.isfinite(lons_arr) | ~np.isfinite(lats_arr) | (lats_arr < -90.0) | (lats_arr > 90.0)
    valid_mask = ~invalid_mask

    geoid_res = np.full(n_pts, 'INVALID', dtype=object)
    qc_res = np.full(n_pts, 'QC_DATUM_INVALID', dtype=object)

    if source_mask_data is not None and source_mask_inv_transform is not None:
        # 坐标投影转换 (若掩膜为非 WGS84 投影坐标系)
        if source_mask_transformer is not None and np.any(valid_mask):
            xs_mask, ys_mask = source_mask_transformer.transform(lons_arr, lats_arr)
            xs_arr = np.asarray(xs_mask, dtype=float)
            ys_arr = np.asarray(ys_mask, dtype=float)
        else:
            xs_arr = lons_arr
            ys_arr = lats_arr

        cols, rows = _apply_affine_transform(source_mask_inv_transform, xs_arr, ys_arr)
        cols_int = np.round(cols - 0.5).astype(int)
        rows_int = np.round(rows - 0.5).astype(int)
        h, w = source_mask_data.shape
        in_bounds = (rows_int >= 0) & (rows_int < h) & (cols_int >= 0) & (cols_int < w) & valid_mask

        # 逐像元判定权威掩膜分类
        if np.any(in_bounds):
            pix_vals = source_mask_data[rows_int[in_bounds], cols_int[in_bounds]]

            # 判断是否为 NoData
            is_nd = (pix_vals == 255)
            if source_mask_nodata is not None and np.isfinite(source_mask_nodata):
                is_nd |= np.isclose(pix_vals, source_mask_nodata)

            # 类别 1: GOCO06s
            is_goco_auth = (pix_vals == 1) & ~is_nd
            # 类别 2 或 3: EIGEN-6C4
            is_eigen_auth = ((pix_vals == 2) | (pix_vals == 3)) & ~is_nd

            idx_in_bounds = np.where(in_bounds)[0]
            if np.any(is_goco_auth):
                idx_g = idx_in_bounds[is_goco_auth]
                geoid_res[idx_g] = 'GOCO06s'
                qc_res[idx_g] = 'AUTHORITATIVE_MASK'

            if np.any(is_eigen_auth):
                idx_e = idx_in_bounds[is_eigen_auth]
                geoid_res[idx_e] = 'EIGEN-6C4'
                qc_res[idx_e] = 'AUTHORITATIVE_MASK'

        # 对未获得有效权威分类的像元 (越界、0、255、NoData、未定义类别)，执行逐像元独立多边形回退
        fallback_mask = valid_mask & (geoid_res == 'INVALID')
        if np.any(fallback_mask):
            sub_lons = lons_arr[fallback_mask]
            sub_lats = lats_arr[fallback_mask]
            in_med = _points_in_polygon(sub_lons, sub_lats, _MED_POLYGON_VERTICES)
            in_blk = _points_in_polygon(sub_lons, sub_lats, _BLK_POLYGON_VERTICES)
            is_poly_eigen = in_med | in_blk

            idx_fb = np.where(fallback_mask)[0]
            geoid_res[idx_fb] = np.where(is_poly_eigen, 'EIGEN-6C4', 'GOCO06s')
            qc_res[idx_fb] = 'QC_DATUM_SOURCE_APPROX'
    else:
        # 无权威掩膜配置，全部有效点回退为几何多边形判定
        if np.any(valid_mask):
            sub_lons = lons_arr[valid_mask]
            sub_lats = lats_arr[valid_mask]
            in_med = _points_in_polygon(sub_lons, sub_lats, _MED_POLYGON_VERTICES)
            in_blk = _points_in_polygon(sub_lons, sub_lats, _BLK_POLYGON_VERTICES)
            is_poly_eigen = in_med | in_blk

            idx_valid = np.where(valid_mask)[0]
            geoid_res[idx_valid] = np.where(is_poly_eigen, 'EIGEN-6C4', 'GOCO06s')
            qc_res[idx_valid] = np.where(is_poly_eigen, 'QC_DATUM_SOURCE_APPROX', 'NORMAL')

    out_geoid = str(geoid_res[0]) if is_scalar else geoid_res
    out_qc = str(qc_res[0]) if is_scalar else qc_res

    if return_qc:
        return out_geoid, out_qc
    return out_geoid


def is_mediterranean_or_black_sea(lons: float | np.ndarray, lats: float | np.ndarray) -> np.ndarray:
    """
    判断空间坐标是否位于地中海或黑海区域 (CMEMS 区域高分辨率 MDT 覆盖范围)。
    基于严密几何多边形检验，向后完全兼容已有测试接口。
    """
    is_scalar = np.isscalar(lons) and np.isscalar(lats)
    ref_geoids = np.atleast_1d(get_mdt_reference_geoid(lons, lats))
    res = (ref_geoids == 'EIGEN-6C4')
    return bool(res[0]) if is_scalar else res


def _apply_affine_transform(transform, xs, ys):
    """
    针对不同版本 affine 库对 (x, y) 坐标变换的兼容实现。
    显式采用标准仿射变换解析式：
      col = a * x + b * y + c
      row = d * x + e * y + f
    保证在所有 Python/affine/rasterio 版本下均 100% 稳健执行且无弃用告警。
    """
    return transform.a * xs + transform.b * ys + transform.c, transform.d * xs + transform.e * ys + transform.f


class DatumTransformer:
    """
    严密的海洋与大地测量垂直基准转换器。
    全面支持目标感知的基准加载 (Target-aware)、GOCO06s 与 EIGEN-6C4 双水准面差值改正及严格错误防御。
    """

    def __init__(
        self,
        mdt_path: str = None,
        egm2008_path: str = None,
        delta_n_goco_path: str = None,
        delta_n_eigen_path: str = None,
        delta_n_path: str = None,
        source_mask_path: str = None
    ):
        config = load_app_config()
        paths_cfg = config.get('paths', {})

        if mdt_path is None:
            mdt_path = paths_cfg.get('mdt_nc')
        if egm2008_path is None:
            egm2008_path = paths_cfg.get('egm2008_tif')

        # GOCO06s - EGM2008 栅格路径 (优先读取新字段，兼容旧 delta_n_tif 字段)
        if delta_n_goco_path is None:
            delta_n_goco_path = paths_cfg.get('delta_n_goco06s_egm2008_tif')
        if delta_n_goco_path is None and delta_n_path is not None:
            delta_n_goco_path = delta_n_path
        if delta_n_goco_path is None:
            delta_n_goco_path = paths_cfg.get('delta_n_tif', 'data/geoid/delta_n_goco06s_minus_egm2008.tif')

        # EIGEN-6C4 - EGM2008 栅格路径
        if delta_n_eigen_path is None:
            delta_n_eigen_path = paths_cfg.get('delta_n_eigen6c4_egm2008_tif', 'data/geoid/delta_n_eigen6c4_minus_egm2008.tif')

        # Hybrid MDT 权威来源掩膜路径 (可选)
        if source_mask_path is None:
            source_mask_path = paths_cfg.get('hybrid_mdt_source_mask')

        self.mdt_path = resolve_project_path(mdt_path)
        self.egm2008_path = resolve_project_path(egm2008_path, prefer_resource=True)
        self.delta_n_goco_path = resolve_project_path(delta_n_goco_path, prefer_resource=True)
        self.delta_n_eigen_path = resolve_project_path(delta_n_eigen_path, prefer_resource=True)
        self.source_mask_path = resolve_project_path(source_mask_path, prefer_resource=True) if source_mask_path else None
        # 兼容旧属性名称
        self.delta_n_path = self.delta_n_goco_path

        # 懒加载缓存对象
        self._mdt_interpolator = None
        self._egm_data = None
        self._egm_inv_transform = None
        self._delta_n_goco_data = None
        self._delta_n_goco_inv_transform = None
        self._delta_n_eigen_data = None
        self._delta_n_eigen_inv_transform = None
        self._source_mask_data = None
        self._source_mask_crs = None
        self._source_mask_transform = None
        self._source_mask_inv_transform = None
        self._source_mask_nodata = None
        self._source_mask_width = None
        self._source_mask_height = None
        self._source_mask_category_set = None
        self._source_mask_transformer = None

        # 测试用数值夹具覆写机制 (Synthetic Fixtures for Regression Testing)
        self._synthetic_fixtures = None

    def _init_source_mask(self, strict: bool = False):
        """加载 CNES-CLS22 Hybrid MDT 可选来源掩膜 GeoTIFF (若配置)"""
        if self._source_mask_data is None and self.source_mask_path:
            if not os.path.exists(self.source_mask_path):
                msg = f"未找到可选 Hybrid MDT 来源掩膜文件: {self.source_mask_path}。将自动回退为空间多边形近似判定。"
                if strict:
                    raise DatumDataError(msg)
                return
            try:
                with rasterio.open(self.source_mask_path) as src:
                    if src.crs is None:
                        msg = f"Hybrid MDT 来源掩膜文件缺少 CRS 坐标参考系 ({self.source_mask_path})"
                        if strict:
                            raise DatumDataError(msg)
                        else:
                            warnings.warn(msg + "，将回退为空间几何多边形判定。")
                            return

                    self._source_mask_data = src.read(1)
                    self._source_mask_crs = src.crs
                    self._source_mask_transform = src.transform
                    self._source_mask_inv_transform = ~src.transform
                    self._source_mask_nodata = src.nodata
                    self._source_mask_width = src.width
                    self._source_mask_height = src.height
                    self._source_mask_category_set = set(np.unique(self._source_mask_data))

                    if not src.crs.is_geographic:
                        try:
                            from pyproj import Transformer
                            self._source_mask_transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
                        except Exception as te:
                            if strict:
                                raise DatumDataError(f"初始化投影掩膜坐标转换器失败: {te}") from te
                            warnings.warn(f"初始化投影掩膜坐标转换器失败: {te}，回退为多边形判定。")
                            self._source_mask_data = None
                            return
                    else:
                        self._source_mask_transformer = None
            except Exception as e:
                msg = f"打开 Hybrid MDT 来源掩膜失败 ({self.source_mask_path}): {e}"
                if strict:
                    raise DatumDataError(msg) from e
                warnings.warn(msg + "，将回退为空间几何多边形判定。")

    def _get_ref_geoid_and_qc(self, lons_arr: np.ndarray, lats_arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """内部调用，优先查询权威来源掩膜，无掩膜或掩膜点无分类独立回退为几何多边形并标记 QC_DATUM_SOURCE_APPROX"""
        self._init_source_mask(strict=False)
        geoids, qcs = get_mdt_reference_geoid(
            lons_arr,
            lats_arr,
            source_mask_data=self._source_mask_data,
            source_mask_inv_transform=self._source_mask_inv_transform,
            source_mask_nodata=self._source_mask_nodata,
            source_mask_transformer=self._source_mask_transformer,
            return_qc=True
        )
        return np.atleast_1d(geoids), np.atleast_1d(qcs)

    def get_mdt_reference_geoid(self, lon, lat, return_qc: bool = True):
        """外部接口：查询指定坐标点或数组处的 MDT 原始参考水准面 (GOCO06s 或 EIGEN-6C4) 及质量标识"""
        self._init_source_mask(strict=False)
        return get_mdt_reference_geoid(
            lon, lat,
            source_mask_data=self._source_mask_data,
            source_mask_inv_transform=self._source_mask_inv_transform,
            source_mask_nodata=self._source_mask_nodata,
            source_mask_transformer=self._source_mask_transformer,
            return_qc=return_qc
        )

    def set_synthetic_fixture(
        self,
        mdt=None,
        delta_goco=None,
        delta_eigen=None,
        n_egm=None,
        mdt_val=None,
        delta_goco_val=None,
        delta_eigen_val=None,
        n_egm_val=None
    ):
        """设置纯数值回归测试夹具，用于验证多元基准数学闭合与分支选择"""
        self._synthetic_fixtures = {
            'mdt': mdt if mdt is not None else mdt_val,
            'delta_goco': delta_goco if delta_goco is not None else delta_goco_val,
            'delta_eigen': delta_eigen if delta_eigen is not None else delta_eigen_val,
            'n_egm': n_egm if n_egm is not None else n_egm_val
        }

    def _init_mdt(self, strict: bool = False):
        """初始化 MDT 全局向量化插值器"""
        if self._synthetic_fixtures and self._synthetic_fixtures.get('mdt') is not None:
            return
        if self._mdt_interpolator is None:
            if not os.path.exists(self.mdt_path):
                msg = f"未找到 CNES-CLS22 MDT 文件: {self.mdt_path}。若需计算绝对海拔高程，请配置有效 MDT 文件路径。"
                if strict:
                    raise DatumDataError(msg)
                warnings.warn(msg + " MDT 查询将返回 NaN")
                return
            try:
                ds = xr.open_dataset(self.mdt_path)
                lats = ds.latitude.values
                lons = ds.longitude.values
                mdt_grid = ds.mdt.values[0]  # shape: (1440, 2880)
                ds.close()

                self._mdt_interpolator = RegularGridInterpolator(
                    (lats, lons), mdt_grid, bounds_error=False, fill_value=np.nan
                )
            except Exception as e:
                msg = f"打开 MDT NetCDF 失败 ({self.mdt_path}): {e}"
                if strict:
                    raise DatumDataError(msg) from e
                warnings.warn(msg + "，MDT 查询将返回 NaN")

    def _init_egm2008(self, strict: bool = False):
        """加载 EGM2008 GeoTIFF 栅格与仿射变换"""
        if self._synthetic_fixtures and self._synthetic_fixtures.get('n_egm') is not None:
            return
        if self._egm_data is None:
            if not os.path.exists(self.egm2008_path):
                msg = f"未找到 EGM2008 栅格文件: {self.egm2008_path}。若需计算 WGS84 几何椭球高，请确保该文件已下载配置。"
                if strict:
                    raise DatumDataError(msg)
                warnings.warn(msg + " 正高起伏查询将返回 NaN")
                return
            try:
                with rasterio.open(self.egm2008_path) as src:
                    self._egm_data = src.read(1).astype(np.float32)
                    self._egm_inv_transform = ~src.transform
            except Exception as e:
                msg = f"打开 EGM2008 GeoTIFF 失败: {e}"
                if strict:
                    raise DatumDataError(msg) from e
                warnings.warn(msg)

    def _init_delta_n_goco(self, strict: bool = False):
        """加载 GOCO06s 与 EGM2008 差值栅格"""
        if self._synthetic_fixtures and self._synthetic_fixtures.get('delta_goco') is not None:
            return
        if self._delta_n_goco_data is None:
            if not os.path.exists(self.delta_n_goco_path):
                msg = f"未找到全球 GOCO06s 与 EGM2008 差值栅格: {self.delta_n_goco_path}。请通过 scripts/generate_delta_n.py 生成该文件。"
                if strict:
                    raise DatumDataError(msg)
                warnings.warn(msg + " 水准面差值查询将返回 NaN")
                return
            try:
                with rasterio.open(self.delta_n_goco_path) as src:
                    self._delta_n_goco_data = src.read(1).astype(np.float32)
                    self._delta_n_goco_inv_transform = ~src.transform
            except Exception as e:
                msg = f"打开 GOCO06s-EGM2008 GeoTIFF 失败: {e}"
                if strict:
                    raise DatumDataError(msg) from e
                warnings.warn(msg)

    def _init_delta_n_eigen(self, strict: bool = False):
        """加载 EIGEN-6C4 与 EGM2008 差值栅格 (地中海与黑海专用)"""
        if self._synthetic_fixtures and self._synthetic_fixtures.get('delta_eigen') is not None:
            return
        if self._delta_n_eigen_data is None:
            if not os.path.exists(self.delta_n_eigen_path):
                msg = (
                    f"未找到地中海与黑海 EIGEN-6C4 与 EGM2008 差值栅格: {self.delta_n_eigen_path}。"
                    "在 EIGEN-6C4 区域进行严格 EGM2008/WGS84 转换需要该文件，严禁伪造或私自冒用 GOCO06s。"
                    "请使用 scripts/generate_delta_n.py 传入权威 ICGEM EIGEN-6C4 和 EGM2008 栅格生成该文件并在配置中指定。"
                )
                if strict:
                    raise DatumDataError(msg)
                warnings.warn(msg + " EIGEN-6C4 水准面差值查询将返回 NaN")
                return
            try:
                with rasterio.open(self.delta_n_eigen_path) as src:
                    self._delta_n_eigen_data = src.read(1).astype(np.float32)
                    self._delta_n_eigen_inv_transform = ~src.transform
            except Exception as e:
                msg = f"打开 EIGEN6C4-EGM2008 GeoTIFF 失败: {e}"
                if strict:
                    raise DatumDataError(msg) from e
                warnings.warn(msg)

    def get_mdt(self, lons: float | np.ndarray, lats: float | np.ndarray, strict: bool = False) -> float | np.ndarray:
        """
        获取指定经纬度处的 CNES-CLS22 MDT 值 (单位: 米)。
        若落在陆地或无数据区，严格返回 np.nan。
        """
        is_scalar = np.isscalar(lons) and np.isscalar(lats)
        lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

        if self._synthetic_fixtures and self._synthetic_fixtures.get('mdt') is not None:
            val = float(self._synthetic_fixtures['mdt'])
            res = np.full(len(lons_arr), val, dtype=float)
            return float(res[0]) if is_scalar else res

        self._init_mdt(strict=strict)

        if self._mdt_interpolator is None:
            res = np.full(len(lons_arr), np.nan, dtype=float)
            return float(res[0]) if is_scalar else res

        points = np.column_stack([lats_arr, lons_arr])
        res = self._mdt_interpolator(points)
        return float(res[0]) if is_scalar else res

    def get_egm2008_undulation(self, lons: float | np.ndarray, lats: float | np.ndarray, strict: bool = False) -> float | np.ndarray:
        """
        通过双线性插值 (Bilinear Interpolation) 获取 EGM2008 大地水准面起伏 N (单位: 米)。
        采用 map_coordinates(order=1)，并严格扣除 0.5 半像元偏置。
        """
        is_scalar = np.isscalar(lons) and np.isscalar(lats)
        lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

        if self._synthetic_fixtures and self._synthetic_fixtures.get('n_egm') is not None:
            val = float(self._synthetic_fixtures['n_egm'])
            res = np.full(len(lons_arr), val, dtype=float)
            return float(res[0]) if is_scalar else res

        self._init_egm2008(strict=strict)

        if self._egm_data is None:
            res = np.full(len(lons_arr), np.nan, dtype=float)
            return float(res[0]) if is_scalar else res

        cols, rows = _apply_affine_transform(self._egm_inv_transform, lons_arr, lats_arr)
        cols_map = cols - 0.5
        rows_map = rows - 0.5
        vals = map_coordinates(self._egm_data, [rows_map, cols_map], order=1, mode='constant', cval=np.nan)
        return float(vals[0]) if is_scalar else vals

    def get_delta_n(self, lons: float | np.ndarray, lats: float | np.ndarray, strict: bool = False) -> float | np.ndarray:
        """
        根据点位地理位置自适应获取真实大地水准面差值 Delta N = N_MDT_REF - N_EGM2008 (单位: 米)。
        - 全球 GOCO06s 区域: Delta N = N_GOCO06s - N_EGM2008
        - 地中海与黑海 EIGEN-6C4 区域: Delta N = N_EIGEN6C4 - N_EGM2008
        严禁使用 GOCO06s 冒充 EIGEN-6C4。
        """
        is_scalar = np.isscalar(lons) and np.isscalar(lats)
        lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

        if len(lons_arr) != len(lats_arr):
            if len(lons_arr) == 1 and len(lats_arr) > 1:
                lons_arr = np.full(len(lats_arr), lons_arr[0])
            elif len(lats_arr) == 1 and len(lons_arr) > 1:
                lats_arr = np.full(len(lons_arr), lats_arr[0])
            else:
                raise ValueError(f"经纬度坐标长度不一致: len(lon)={len(lons_arr)}, len(lat)={len(lats_arr)}")

        n_pts = len(lons_arr)
        ref_geoids, _ = self._get_ref_geoid_and_qc(lons_arr, lats_arr)
        res = np.full(n_pts, np.nan, dtype=float)

        # 检查是否为测试夹具模式
        if self._synthetic_fixtures:
            syn_goco = self._synthetic_fixtures.get('delta_goco')
            syn_eigen = self._synthetic_fixtures.get('delta_eigen')
            for i in range(n_pts):
                rg = ref_geoids[i]
                if rg == 'GOCO06s' and syn_goco is not None:
                    res[i] = float(syn_goco)
                elif rg == 'EIGEN-6C4' and syn_eigen is not None:
                    res[i] = float(syn_eigen)
            return float(res[0]) if is_scalar else res

        # 分别处理 GOCO06s 与 EIGEN-6C4 区域
        mask_goco = (ref_geoids == 'GOCO06s')
        mask_eigen = (ref_geoids == 'EIGEN-6C4')

        if np.any(mask_goco):
            self._init_delta_n_goco(strict=strict)
            if self._delta_n_goco_data is not None:
                sub_lons = lons_arr[mask_goco]
                sub_lats = lats_arr[mask_goco]
                cols, rows = _apply_affine_transform(self._delta_n_goco_inv_transform, sub_lons, sub_lats)
                cols_map = cols - 0.5
                rows_map = rows - 0.5
                vals = map_coordinates(self._delta_n_goco_data, [rows_map, cols_map], order=1, mode='constant', cval=np.nan)
                res[mask_goco] = vals

        if np.any(mask_eigen):
            self._init_delta_n_eigen(strict=strict)
            if self._delta_n_eigen_data is not None:
                sub_lons = lons_arr[mask_eigen]
                sub_lats = lats_arr[mask_eigen]
                cols, rows = _apply_affine_transform(self._delta_n_eigen_inv_transform, sub_lons, sub_lats)
                cols_map = cols - 0.5
                rows_map = rows - 0.5
                vals = map_coordinates(self._delta_n_eigen_data, [rows_map, cols_map], order=1, mode='constant', cval=np.nan)
                res[mask_eigen] = vals

        return float(res[0]) if is_scalar else res

    def convert_tide_datums(
        self,
        tide_msl_m: float | np.ndarray = None,
        lons: float | np.ndarray = None,
        lats: float | np.ndarray = None,
        datum_target: str = "both",
        strict: bool = False,
        **kwargs
    ) -> dict:
        if tide_msl_m is None and 'tide_msl' in kwargs:
            tide_msl_m = kwargs['tide_msl']
        if tide_msl_m is None:
            raise ValueError("convert_tide_datums 必须提供 tide_msl_m 或 tide_msl 参数。")
        """
        目标感知 (Target-aware) 的四大垂直基准严密转换体系。

        参数:
            tide_msl_m: 相对平均海平面的瞬时潮汐起伏高度 (m)
            lons: 经度 (单值或数组)
            lats: 纬度 (单值或数组)
            datum_target: 'msl', 'mdt_ref' (或 'goco'), 'egm2008' (或 'egm'), 'both', 'wgs84' (或 'wgs'), 'all'
            strict: 若为 True，当请求非 MSL 基准却缺少对应关键数据时立即抛出 DatumDataError；
                    若为 False，发出 warning 并优雅填充 NaN (用于探索性预览)。

        返回字典包含:
            'tide_msl_m': 相对局部平均海平面的纯潮位起伏 (m)
            'mdt_m': 当地平均动态地形 (m)
            'delta_n_m': 真实大地水准面差值改正项 Delta N (m)
            'n_egm2008_m': EGM2008 大地水准面起伏 N (m)
            'h_mdt_ref_m': 相对当地 MDT 原始参考水准面的瞬时海面高 (m) = Tide + MDT
            'h_goco06s_m': 相对 GOCO06s 的海面高 (m) (地中海/黑海严格为 NaN，不冒充)
            'h_egm2008_m': 相对 EGM2008 大地水准面的瞬时海面正高 (m) = H_MDT_REF + Delta_N
            'h_wgs84_m': WGS84 几何空间三维椭球高 (m) = H_EGM2008 + N_EGM2008
            'datum_ref_geoid': 当地 MDT 参考大地水准面 ('GOCO06s' 或 'EIGEN-6C4')
            'qc_warning': 质量状态提示 ('NORMAL' 或 'QC_DATUM_SOURCE_APPROX')
        """
        is_scalar = np.isscalar(tide_msl_m) and np.isscalar(lons) and np.isscalar(lats)
        t_arr, lons_arr, lats_arr = _broadcast_pointwise_inputs(tide_msl_m, lons, lats)
        n_pts = len(t_arr)

        target = str(datum_target).lower()

        ref_geoids, qc_source = self._get_ref_geoid_and_qc(lons_arr, lats_arr)
        qc_warning = qc_source

        # 1. 仅 MSL 模式：绝对零依赖任何外部 MDT/Geoid 栅格文件
        if target == 'msl':
            nan_arr = np.full(n_pts, np.nan, dtype=float)
            res_dict = {
                'tide_msl_m': t_arr,
                'mdt_m': nan_arr,
                'delta_n_m': nan_arr,
                'n_egm2008_m': nan_arr,
                'h_mdt_ref_m': nan_arr,
                'h_goco06s_m': nan_arr,
                'h_egm2008_m': nan_arr,
                'h_wgs84_m': nan_arr,
                'datum_ref_geoid': np.full(n_pts, 'MSL'),
                'qc_warning': np.full(n_pts, 'NORMAL')
            }
            if is_scalar:
                return {
                    k: (float(v[0]) if isinstance(v, np.ndarray) and np.issubdtype(v.dtype, np.number) else (str(v[0]) if isinstance(v, np.ndarray) else v))
                    for k, v in res_dict.items()
                }
            return res_dict

        # 2. mdt_ref 或 goco 模式：需要 MDT，不需要 DeltaN，不需要 EGM undulation
        if target in ['mdt_ref', 'goco', 'goco06s']:
            mdt_vals = np.atleast_1d(self.get_mdt(lons_arr, lats_arr, strict=strict))
            h_mdt_ref = t_arr + mdt_vals
            h_goco06s = np.where(ref_geoids == 'GOCO06s', h_mdt_ref, np.nan)
            nan_arr = np.full(n_pts, np.nan, dtype=float)
            res_dict = {
                'tide_msl_m': t_arr,
                'mdt_m': mdt_vals,
                'delta_n_m': nan_arr,
                'n_egm2008_m': nan_arr,
                'h_mdt_ref_m': h_mdt_ref,
                'h_goco06s_m': h_goco06s,
                'h_egm2008_m': nan_arr,
                'h_wgs84_m': nan_arr,
                'datum_ref_geoid': ref_geoids,
                'qc_warning': qc_warning
            }
            if is_scalar:
                return {
                    k: (float(v[0]) if isinstance(v, np.ndarray) and np.issubdtype(v.dtype, np.number) else (str(v[0]) if isinstance(v, np.ndarray) else v))
                    for k, v in res_dict.items()
                }
            return res_dict

        # 3. egm / egm2008 / both 模式：需要 MDT + 对应 DeltaN，不需要 EGM undulation
        mdt_vals = np.atleast_1d(self.get_mdt(lons_arr, lats_arr, strict=strict))
        delta_n_vals = np.atleast_1d(self.get_delta_n(lons_arr, lats_arr, strict=strict))
        h_mdt_ref = t_arr + mdt_vals
        h_egm2008 = h_mdt_ref + delta_n_vals
        h_goco06s = np.where(ref_geoids == 'GOCO06s', h_mdt_ref, np.nan)

        if target in ['egm', 'egm2008', 'both']:
            # both = MSL + EGM2008，严密解耦：不强制加载 EGM undulation
            if self._egm_data is not None or (self._synthetic_fixtures and self._synthetic_fixtures.get('n_egm') is not None):
                n_egm_vals = np.atleast_1d(self.get_egm2008_undulation(lons_arr, lats_arr, strict=False))
                h_wgs84 = h_egm2008 + n_egm_vals
            else:
                n_egm_vals = np.full(n_pts, np.nan, dtype=float)
                h_wgs84 = np.full(n_pts, np.nan, dtype=float)
        elif target in ['wgs', 'wgs84', 'all']:
            n_egm_vals = np.atleast_1d(self.get_egm2008_undulation(lons_arr, lats_arr, strict=strict))
            h_wgs84 = h_egm2008 + n_egm_vals
        else:
            raise ValueError(
                f"未知的基准目标 datum_target: '{datum_target}'。允许选项: 'msl', 'mdt_ref', 'goco', 'egm2008', 'both', 'wgs84', 'all'。"
            )

        res_dict = {
            'tide_msl_m': t_arr,
            'mdt_m': mdt_vals,
            'delta_n_m': delta_n_vals,
            'n_egm2008_m': n_egm_vals,
            'h_mdt_ref_m': h_mdt_ref,
            'h_goco06s_m': h_goco06s,
            'h_egm2008_m': h_egm2008,
            'h_wgs84_m': h_wgs84,
            'datum_ref_geoid': ref_geoids,
            'qc_warning': qc_warning,
        }

        if is_scalar:
            return {
                k: (float(v[0]) if isinstance(v, np.ndarray) and np.issubdtype(v.dtype, np.number) else (str(v[0]) if isinstance(v, np.ndarray) else v))
                for k, v in res_dict.items()
            }
        return res_dict

    def get_static_datum_offsets(
        self,
        lons: float | np.ndarray,
        lats: float | np.ndarray,
        target: str = "egm2008",
        strict: bool = True
    ) -> dict:
        """
        计算空间坐标处的静态垂直基准偏移量 C(x)。
        使得任意时刻 t 的目标基准高程可直接通过加法得到:
          H_target(x, t) = Tide_MSL(x, t) + C(x)

        参数:
            lons: 经度 (标量或一维数组)
            lats: 纬度 (标量或一维数组)
            target: 目标基准 ('msl', 'mdt_ref', 'goco', 'egm2008', 'both', 'wgs84', 'all')
            strict: 是否执行严格模式 (缺文件时抛出 DatumDataError)

        返回:
            dict 包含:
                'offset_m': 静态高程偏置量 C(x) (np.ndarray)
                'mdt_m': MDT 高程 (np.ndarray)
                'delta_n_m': Delta N 改正值 (np.ndarray)
                'n_egm2008_m': EGM2008 水准面起伏 (np.ndarray)
                'ref_geoids': 参考重力场模型名称 (np.ndarray)
                'qc_warning': 质量控制提示 (np.ndarray)
        """
        is_scalar = np.isscalar(lons) and np.isscalar(lats)
        lons_arr = np.atleast_1d(normalize_longitude(lons, to_360=False))
        lats_arr = np.atleast_1d(np.asarray(lats, dtype=float))

        if len(lons_arr) != len(lats_arr):
            if len(lons_arr) == 1 and len(lats_arr) > 1:
                lons_arr = np.full(len(lats_arr), lons_arr[0])
            elif len(lats_arr) == 1 and len(lons_arr) > 1:
                lats_arr = np.full(len(lons_arr), lats_arr[0])
            else:
                raise ValueError(f"经纬度数组长度不一致: len(lon)={len(lons_arr)}, len(lat)={len(lats_arr)}")

        n_pts = len(lons_arr)
        tgt = str(target).lower()

        ref_geoids, qc_source = self._get_ref_geoid_and_qc(lons_arr, lats_arr)
        qc_warning = qc_source

        if tgt == 'msl':
            res = {
                'offset_m': np.zeros(n_pts, dtype=float),
                'mdt_m': np.full(n_pts, np.nan, dtype=float),
                'delta_n_m': np.full(n_pts, np.nan, dtype=float),
                'n_egm2008_m': np.full(n_pts, np.nan, dtype=float),
                'ref_geoids': np.full(n_pts, 'MSL'),
                'qc_warning': np.full(n_pts, 'NORMAL')
            }
        elif tgt in ['mdt_ref', 'goco', 'goco06s']:
            mdt_vals = np.atleast_1d(self.get_mdt(lons_arr, lats_arr, strict=strict))
            res = {
                'offset_m': mdt_vals,
                'mdt_m': mdt_vals,
                'delta_n_m': np.full(n_pts, np.nan, dtype=float),
                'n_egm2008_m': np.full(n_pts, np.nan, dtype=float),
                'ref_geoids': ref_geoids,
                'qc_warning': qc_warning
            }
        elif tgt in ['egm', 'egm2008', 'both']:
            mdt_vals = np.atleast_1d(self.get_mdt(lons_arr, lats_arr, strict=strict))
            delta_n_vals = np.atleast_1d(self.get_delta_n(lons_arr, lats_arr, strict=strict))
            res = {
                'offset_m': mdt_vals + delta_n_vals,
                'mdt_m': mdt_vals,
                'delta_n_m': delta_n_vals,
                'n_egm2008_m': np.full(n_pts, np.nan, dtype=float),
                'ref_geoids': ref_geoids,
                'qc_warning': qc_warning
            }
        elif tgt in ['wgs', 'wgs84']:
            mdt_vals = np.atleast_1d(self.get_mdt(lons_arr, lats_arr, strict=strict))
            delta_n_vals = np.atleast_1d(self.get_delta_n(lons_arr, lats_arr, strict=strict))
            n_egm_vals = np.atleast_1d(self.get_egm2008_undulation(lons_arr, lats_arr, strict=strict))
            res = {
                'offset_m': mdt_vals + delta_n_vals + n_egm_vals,
                'mdt_m': mdt_vals,
                'delta_n_m': delta_n_vals,
                'n_egm2008_m': n_egm_vals,
                'ref_geoids': ref_geoids,
                'qc_warning': qc_warning
            }
        elif tgt == 'all':
            mdt_vals = np.atleast_1d(self.get_mdt(lons_arr, lats_arr, strict=strict))
            delta_n_vals = np.atleast_1d(self.get_delta_n(lons_arr, lats_arr, strict=strict))
            n_egm_vals = np.atleast_1d(self.get_egm2008_undulation(lons_arr, lats_arr, strict=strict))
            res = {
                'offset_m': mdt_vals + delta_n_vals,
                'mdt_m': mdt_vals,
                'delta_n_m': delta_n_vals,
                'n_egm2008_m': n_egm_vals,
                'ref_geoids': ref_geoids,
                'qc_warning': qc_warning
            }
        else:
            raise ValueError(f"未知的基准目标 target: '{target}'")

        if is_scalar:
            return {
                k: (float(v[0]) if isinstance(v, np.ndarray) and np.issubdtype(v.dtype, np.number) else (str(v[0]) if isinstance(v, np.ndarray) else v))
                for k, v in res.items()
            }
        return res


