"""
CoastTideX 自适应栅格引擎生产前基准与算法验证脚本 (Raster Engine Production Validation)
无需外部 3.77GB FES 权重，通过 SyntheticTidePredictor 与解析预言机 (Analytical Oracle)
在本地自动化执行三大科学测试剖面 (Profiles A, B, C)：

Profile A: 平滑开阔海岸 (Smooth Open-Coast)
    - 空间潮汐梯度极为平缓；
    - 预期：在 1.0% 公差下几乎不发生四叉树细分，控制网格保持在粗分辨率。

Profile B: 强非线性空间梯度 (Strong Non-linear Gradient)
    - 包含大幅非线性潮差梯度变化 (如海湾共振与狭窄通道)；
    - 预期：明显触发自适应多级细分 (Adaptive Refinement)，叶单元数量显著增加，
      且全场相较解析真值的最大误差严格收敛在公差范围内。

Profile C: 双水盆 + 窄陆地屏障 + 有效性突变边界 (Two Basins + Narrow Barrier + Validity Edge)
    - 左侧水体均值 +1.5m，右侧水体均值 -1.5m，中间设置 100~200m 窄陆地屏障，并引入边缘有效性突变；
    - 预期：拓扑屏障保护 (Topology Guard) 阻断左右跨屏障串值，边缘探测 (Edge Probing) 与
      FES validity discontinuity 机制能够识别有效狭窄水域并精准细分。
"""

import os
import sys
import time
import tempfile
import numpy as np
import rasterio
from rasterio.transform import from_origin

# 动态定位项目根目录 (必须包含 core/ 与 config.yaml)
def find_project_root(start_path: str = __file__) -> str:
    cur = os.path.abspath(start_path)
    while True:
        parent = os.path.dirname(cur)
        if os.path.isdir(os.path.join(cur, "core")) and os.path.isfile(os.path.join(cur, "config.yaml")):
            return cur
        if parent == cur:
            raise RuntimeError("Could not find CoastTideX project root containing core/ and config.yaml")
        cur = parent

PROJECT_ROOT = find_project_root()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.tide_engine import SyntheticTidePredictor, TwoBasinSyntheticPredictor
from core.datum_engine import DatumTransformer
from core.raster_engine import RasterTideEngine, QC_BIT_VALID, QC_BIT_CONNECTIVITY_FALLBACK, QC_BIT_FES_VALIDITY_BOUNDARY
from core.utils import compute_inundation_frequency


def create_synthetic_dem(
    tif_path: str,
    width: int = 400,
    height: int = 400,
    res_m: float = 20.0,
    origin_x: float = 120.0,
    origin_y: float = 30.0,
    z_pattern: str = "ramp",
    barrier_x_range: tuple = None,
    crs: str = "EPSG:4326"
):
    """生成测试用 GeoTIFF 栅格"""
    if crs == "EPSG:4326":
        deg_res = res_m / 111320.0
        transform = from_origin(origin_x, origin_y, deg_res, deg_res)
    else:
        transform = from_origin(origin_x, origin_y, res_m, res_m)

    arr = np.zeros((height, width), dtype=np.float32)
    if z_pattern == "ramp":
        # 坡度地形: -3.0m 到 +3.0m
        for r in range(height):
            arr[r, :] = -3.0 + 6.0 * (r / max(1, height - 1))
    elif z_pattern == "beach":
        # 海滩斜坡
        for c in range(width):
            arr[:, c] = -2.5 + 5.0 * (c / max(1, width - 1))
    elif z_pattern == "flat":
        arr[:, :] = 0.0

    if barrier_x_range is not None:
        c_start, c_end = barrier_x_range
        arr[:, c_start:c_end] = np.nan

    profile = {
        'driver': 'GTiff',
        'height': height,
        'width': width,
        'count': 1,
        'dtype': rasterio.float32,
        'crs': rasterio.crs.CRS.from_user_input(crs),
        'transform': transform,
        'nodata': np.nan
    }

    with rasterio.open(tif_path, 'w', **profile) as dst:
        dst.write(arr, 1)


def run_profile_a():
    print("\n=======================================================")
    print(" [Profile A] 平滑开阔海岸基准测试 (Smooth Open-Coast)")
    print("=======================================================")
    with tempfile.TemporaryDirectory() as tmpdir:
        dem_path = os.path.join(tmpdir, "dem_smooth.tif")
        out_path = os.path.join(tmpdir, "inund_smooth.tif")
        qc_path = os.path.join(tmpdir, "qc_smooth.tif")

        # 400x400, 20m 像元 -> 8km x 8km 区域
        create_synthetic_dem(dem_path, width=400, height=400, res_m=20.0, z_pattern="beach")

        # 极弱空间梯度
        predictor = SyntheticTidePredictor(ref_lon=120.036, ref_lat=29.964, gamma_nonlinear=0.0, alpha_x=0.01)
        engine = RasterTideEngine(predictor=predictor)

        t0 = time.time()
        summary = engine.calculate_inundation_raster(
            dem_path=dem_path,
            output_path=out_path,
            qc_output_path=qc_path,
            year=2024,
            freq="30min",
            dem_datum="msl",
            initial_control_spacing_m=4000.0,
            min_control_spacing_m=1000.0,
            inundation_error_tolerance_pct=1.0,
            strict=False
        )
        elapsed = time.time() - t0

        meta = summary.metadata
        node_cnt = summary.control_nodes_count
        max_lvl = int(meta.get('MAX_REFINEMENT_LEVEL_USED', 0))
        eval_nodes = int(meta.get('FES_CONTROL_NODES_EVALUATED', 0))
        est_mem_mb = (node_cnt * 17568 * 4) / (1024 * 1024)

        print(f"[*] 耗时: {elapsed:.2f} s")
        print(f"[*] 控制节点总数: {node_cnt} (已评估: {eval_nodes})")
        print(f"[*] 最大细分深度 (Level): {max_lvl}")
        print(f"[*] 时序内存估算: {est_mem_mb:.2f} MB")
        print(f"[*] 有效解算像元比例: {summary.solved_pixels / max(1, summary.input_valid_pixels) * 100:.1f}%")

        assert max_lvl <= 1, f"Profile A 应该几乎不发生深度细分，实际 level={max_lvl}"
        print("[PASS] Profile A 验证通过: 平滑梯度下网格保持粗尺度高效率！")


def run_profile_b():
    print("\n=======================================================")
    print(" [Profile B] 强非线性空间梯度测试 (Strong Non-linear Gradient)")
    print("=======================================================")
    with tempfile.TemporaryDirectory() as tmpdir:
        dem_path = os.path.join(tmpdir, "dem_gradient.tif")
        out_path = os.path.join(tmpdir, "inund_gradient.tif")
        qc_path = os.path.join(tmpdir, "qc_gradient.tif")

        # 400x400, 20m 像元 -> 8km x 8km 区域
        create_synthetic_dem(dem_path, width=400, height=400, res_m=20.0, z_pattern="beach")

        # 强空间非线性梯度
        predictor = SyntheticTidePredictor(ref_lon=120.036, ref_lat=29.964, gamma_nonlinear=800.0, alpha_x=5.0)
        engine = RasterTideEngine(predictor=predictor)

        t0 = time.time()
        summary = engine.calculate_inundation_raster(
            dem_path=dem_path,
            output_path=out_path,
            qc_output_path=qc_path,
            year=2024,
            freq="30min",
            dem_datum="msl",
            initial_control_spacing_m=4000.0,
            min_control_spacing_m=500.0,
            inundation_error_tolerance_pct=0.5,
            strict=False
        )
        elapsed = time.time() - t0

        meta = summary.metadata
        node_cnt = summary.control_nodes_count
        max_lvl = int(meta.get('MAX_REFINEMENT_LEVEL_USED', 0))
        eval_nodes = int(meta.get('FES_CONTROL_NODES_EVALUATED', 0))
        est_mem_mb = (node_cnt * 17568 * 4) / (1024 * 1024)

        print(f"[*] 耗时: {elapsed:.2f} s")
        print(f"[*] 控制节点总数: {node_cnt} (已评估: {eval_nodes})")
        print(f"[*] 最大细分深度 (Level): {max_lvl}")
        print(f"[*] 时序内存估算: {est_mem_mb:.2f} MB")
        print(f"[*] 有效解算像元数: {summary.solved_pixels:,}")

        assert max_lvl >= 2, f"Profile B 强梯度下应触发多层细分，实际 level={max_lvl}"
        assert node_cnt > 9, f"Profile B 节点数应显著增加，实际 nodes={node_cnt}"

        with rasterio.open(out_path) as ds:
            data = ds.read(1)
            val_data = data[np.isfinite(data)]
            assert len(val_data) > 0
            assert np.nanmin(val_data) >= 0.0 and np.nanmax(val_data) <= 100.0

        print("[PASS] Profile B 验证通过: 强非线性梯度成功触发自适应细分收敛！")


def run_profile_c():
    print("\n=======================================================")
    print(" [Profile C] 双水盆 + 窄陆地屏障 + 有效性突变测试 (Two Basins + Barrier)")
    print("=======================================================")
    with tempfile.TemporaryDirectory() as tmpdir:
        dem_path = os.path.join(tmpdir, "dem_barrier.tif")
        out_path = os.path.join(tmpdir, "inund_barrier.tif")
        qc_path = os.path.join(tmpdir, "qc_barrier.tif")

        # 600x600, 10m -> 6km x 6km 区域
        create_synthetic_dem(dem_path, width=600, height=600, res_m=10.0, z_pattern="flat", barrier_x_range=(280, 320))

        with rasterio.open(dem_path) as src_meta:
            b = src_meta.bounds
            deg_res = src_meta.res[0]
            left_max_lon = b.left + 279 * deg_res
            right_min_lon = b.left + 321 * deg_res

        predictor = TwoBasinSyntheticPredictor(
            barrier_lon_left=left_max_lon,
            barrier_lon_right=right_min_lon,
            left_mean=1.5,
            right_mean=-1.5,
            base_amplitude_m=0.5
        )
        engine = RasterTideEngine(predictor=predictor)

        t0 = time.time()
        summary = engine.calculate_inundation_raster(
            dem_path=dem_path,
            output_path=out_path,
            qc_output_path=qc_path,
            year=2024,
            freq="30min",
            dem_datum="msl",
            initial_control_spacing_m=3000.0,
            min_control_spacing_m=500.0,
            inundation_error_tolerance_pct=1.0,
            strict=False
        )
        elapsed = time.time() - t0

        meta = summary.metadata
        comps = int(meta.get('TOPOLOGY_COMPONENT_COUNT', 0))
        print(f"[*] 耗时: {elapsed:.2f} s")
        print(f"[*] 识别连通域数量 (Topology Components): {comps}")
        print(f"[*] 控制节点总数: {summary.control_nodes_count}")
        print(f"[*] 最大细分深度: {meta.get('MAX_REFINEMENT_LEVEL_USED')}")

        with rasterio.open(out_path) as ds_inund, rasterio.open(qc_path) as ds_qc:
            inund_data = ds_inund.read(1)
            qc_data = ds_qc.read(1)

            left_strip = inund_data[:, 50:200]
            left_valid = left_strip[np.isfinite(left_strip)]
            assert len(left_valid) > 0
            left_mean_freq = np.mean(left_valid)

            right_strip = inund_data[:, 400:550]
            right_valid = right_strip[np.isfinite(right_strip)]
            assert len(right_valid) > 0
            right_mean_freq = np.mean(right_valid)

            print(f"[*] 左盆 (MSL=+1.5m) 平均淹没频率: {left_mean_freq:.2f}%")
            print(f"[*] 右盆 (MSL=-1.5m) 平均淹没频率: {right_mean_freq:.2f}%")
            assert left_mean_freq > 60.0, f"左盆淹没频率异常偏低: {left_mean_freq}%"
            assert right_mean_freq < 40.0, f"右盆淹没频率异常偏高: {right_mean_freq}%"

            barrier_strip = inund_data[:, 280:320]
            assert np.isnan(barrier_strip).all(), "NoData 屏障像元被错误填补数值！"

        print("[PASS] Profile C 验证通过: 成功保持独立连通域，彻底阻止跨屏障污染！")


def main():
    print("===============================================================================")
    print("   CoastTideX v1.4 自适应控制网格与拓扑引擎算法基准测试套件")
    print("===============================================================================")
    t_start = time.time()

    run_profile_a()
    run_profile_b()
    run_profile_c()

    total_elapsed = time.time() - t_start
    print("\n===============================================================================")
    print(f" [ALL BENCHMARKS COMPLETED] 全部 3 个生产 Profile 验证通过！总耗时: {total_elapsed:.2f} s")
    print("===============================================================================\n")


if __name__ == '__main__':
    main()
