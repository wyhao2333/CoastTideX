"""
CoastTideX 批量潮间带栅格解算调度引擎 (Batch Intertidal Raster Engine v1.6 Beta Hardened)
支持文件夹级多 GeoTIFF 自动化发现、轻量级元数据检查、确定性排序、
Tide Cache 序列化与二阶段淹没频率解算、统一 ExistingOutputPolicy 策略调度、
全流程断点恢复 (Resume)、单文件失败隔离与任务清单 (Manifest) 管理。
"""

import os
import csv
import json
import time
import traceback
from enum import Enum
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable, Union
from collections import OrderedDict

import numpy as np
import rasterio

from .raster_engine import (
    RasterTideEngine, RasterCalculationCancelled, ExistingOutputError
)
from .tide_cache import (
    write_tide_cache, read_tide_cache, is_cache_complete,
    calculate_inundation_from_tide_cache, estimate_tide_cache_size, build_expected_cache_spec,
    inspect_tide_cache_metadata, validate_tide_cache_compatibility,
    TideCacheCompatibilityError
)

# 任务运行模式
JOB_MODE_TIDE_ONLY = "tide"                                 # 仅解算控制节点潮位并生成 Tide Cache (*_tide.nc)
JOB_MODE_TIDE_AND_INUNDATION = "tide-inundation"            # 先生成 Tide Cache，再解算淹没频率 (默认完整两阶段流程)
JOB_MODE_INUNDATION_FROM_CACHE = "inundation-from-cache"    # 从已有 Tide Cache 直接解算淹没频率 (零 FES 调用)
JOB_MODE_EXPOSURE_FROM_CACHE = "exposure-from-cache"        # 从已有 Tide Cache 直接解算潜在天文潮露出时间域产品 (零 FES 调用)
JOB_MODE_TIDE_AND_EXPOSURE = "tide-exposure"                # 先生成 Tide Cache，再解算潜在天文潮露出时间域产品
JOB_MODE_ALL = "all"                                        # 全要素产品包: Tide Cache + 淹没频率 + 潜在天文潮露出时间域产品

VALID_JOB_MODES = {
    JOB_MODE_TIDE_ONLY,
    JOB_MODE_TIDE_AND_INUNDATION,
    JOB_MODE_INUNDATION_FROM_CACHE,
    JOB_MODE_TIDE_AND_EXPOSURE,
    JOB_MODE_EXPOSURE_FROM_CACHE,
    JOB_MODE_ALL
}

# 现有输出处理策略 (ExistingOutputPolicy)
class ScanSnapshotStaleError(ValueError):
    """扫描快照已失效 (文件已被移动、删除或修改) 异常"""
    pass


class ExistingOutputPolicy(str, Enum):
    RESUME = "resume"                      # 断点恢复: 完整产物安全跳过，未完工瓦片接续
    ERROR_IF_EXISTS = "error_if_exists"    # 冲突报错: 目标产物已存在时显式报错拒绝覆写
    OVERWRITE = "overwrite"                # 强制覆盖: 允许重新计算并安全原子替换


def normalize_existing_output_policy(
    existing_policy: Optional[Union[str, ExistingOutputPolicy]] = None,
    resume: Optional[bool] = None,
    overwrite: Optional[bool] = None
) -> ExistingOutputPolicy:
    """
    归一化现有输出策略，彻底消除 resume 与 overwrite 互相冲突的不一致状态。
    """
    if resume is True and overwrite is True:
        raise ValueError("参数冲突: resume 与 overwrite 不能同时为 True！请指定单一确定的策略。")

    if existing_policy is not None:
        if isinstance(existing_policy, ExistingOutputPolicy):
            return existing_policy
        p_str = str(existing_policy).strip().lower()
        if p_str in ("resume", "resuming"):
            return ExistingOutputPolicy.RESUME
        elif p_str in ("error_if_exists", "error", "safe"):
            return ExistingOutputPolicy.ERROR_IF_EXISTS
        elif p_str in ("overwrite", "force"):
            return ExistingOutputPolicy.OVERWRITE
        else:
            raise ValueError(f"未知的输出策略: '{existing_policy}'。必须为 'resume', 'error_if_exists', 或 'overwrite'。")

    if overwrite is True:
        return ExistingOutputPolicy.OVERWRITE
    if resume is False and overwrite is False:
        return ExistingOutputPolicy.ERROR_IF_EXISTS
    # 默认兜底策略为 RESUME
    return ExistingOutputPolicy.RESUME


# 单文件执行状态
STATUS_PENDING = "PENDING"
STATUS_VALIDATING = "VALIDATING"
STATUS_TIDE_RUNNING = "TIDE_RUNNING"
STATUS_TIDE_READY = "TIDE_READY"
STATUS_FREQUENCY_RUNNING = "FREQUENCY_RUNNING"
STATUS_EXPOSURE_RUNNING = "EXPOSURE_RUNNING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"
STATUS_SKIPPED = "SKIPPED"


class BatchManifest:
    """
    批量任务清单管理器 (Batch Manifest Manager)。
    负责以 JSON 与 CSV 双格式原子持久化批量任务的每个 GeoTIFF 瓦片运行状态、耗时与错误报告。
    """

    FIELDS = [
        "input_path",
        "input_name",
        "tide_cache_path",
        "frequency_path",
        "qc_path",
        "exposure_output_dir",
        "exposure_products_complete",
        "exposure_fraction_path",
        "exposure_duration_h_path",
        "exposure_max_continuous_h_path",
        "exposure_mean_event_h_path",
        "exposure_event_count_path",
        "exposure_valid_time_fraction_path",
        "exposure_qc_path",
        "status",
        "run_action",
        "time_start",
        "time_end",
        "time_step",
        "timezone",
        "time_samples",
        "dem_datum",
        "target_mode",
        "cache_signature",
        "crs",
        "width",
        "height",
        "nodata",
        "valid_pixel_count",
        "control_node_count",
        "elapsed_tide_seconds",
        "elapsed_frequency_seconds",
        "elapsed_exposure_seconds",
        "error_message"
    ]

    def __init__(self, output_folder: str):
        self.output_folder = Path(output_folder)
        self.json_path = self.output_folder / "batch_manifest.json"
        self.csv_path = self.output_folder / "batch_manifest.csv"
        self.records: Dict[str, Dict[str, Any]] = OrderedDict()

    def load(self) -> None:
        """从已存在的 batch_manifest.json 加载既有记录，具备向前向后字段兼容性"""
        if self.json_path.exists():
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.records = OrderedDict()
                for item in data:
                    in_path = item.get("input_path", "")
                    if in_path:
                        rec = {k: "" for k in self.FIELDS}
                        rec.update(item)
                        self.records[in_path] = rec
            except Exception:
                self.records = OrderedDict()

    def get_entry(self, input_path: str) -> Optional[Dict[str, Any]]:
        if not self.records and self.json_path.exists():
            self.load()
        return self.records.get(input_path)

    def get_status(self, input_path: str) -> Optional[str]:
        item = self.get_entry(input_path)
        return item.get("status") if item else None

    def upsert(self, input_path: str, **kwargs) -> None:
        if input_path not in self.records:
            self.records[input_path] = {k: "" for k in self.FIELDS}
            self.records[input_path]["input_path"] = input_path
            self.records[input_path]["input_name"] = os.path.basename(input_path)
            self.records[input_path]["status"] = STATUS_PENDING
            self.records[input_path]["run_action"] = "PENDING"
        self.records[input_path].update(kwargs)

    def save(self) -> None:
        """原子级持久化 JSON 与 CSV 清单文件"""
        self.output_folder.mkdir(parents=True, exist_ok=True)
        items = list(self.records.values())

        # 写入临时 JSON
        tmp_json = self.json_path.with_suffix(".tmp.json")
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        os.replace(tmp_json, self.json_path)

        # 写入临时 CSV
        tmp_csv = self.csv_path.with_suffix(".tmp.csv")
        with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writeheader()
            for it in items:
                row = {k: it.get(k, "") for k in self.FIELDS}
                writer.writerow(row)
        os.replace(tmp_csv, self.csv_path)


def _verify_exposure_artifacts(
    dem_info,
    exp_paths,
    expected_cache_sig: Optional[str] = None
) -> bool:
    """验证已有潜在天文潮露出栅格全部 7 大产物的尺寸、坐标系、仿射变换、数据类型、NoData 与缓存签名一致性"""
    product_specs = [
        (exp_paths.exposure_fraction_path, 'float32', True, None),
        (exp_paths.exposure_duration_h_path, 'float32', True, None),
        (exp_paths.exposure_max_continuous_h_path, 'float32', True, None),
        (exp_paths.exposure_mean_event_h_path, 'float32', True, None),
        (exp_paths.exposure_event_count_path, 'uint32', False, 4294967295),
        (exp_paths.exposure_valid_time_fraction_path, 'float32', True, None),
        (exp_paths.exposure_qc_path, 'uint16', False, 65535)
    ]
    for p, expected_dtype, is_float, expected_nodata in product_specs:
        if not os.path.exists(p):
            return False
        try:
            with rasterio.open(p) as src:
                if src.width != dem_info.width or src.height != dem_info.height:
                    return False
                if str(src.crs) != str(dem_info.crs):
                    return False
                if not np.allclose(src.transform, dem_info.transform, atol=1e-5):
                    return False
                if src.dtypes[0] != expected_dtype:
                    return False
                if is_float:
                    if src.nodata is None:
                        return False
                    if not (np.isnan(src.nodata) or np.isclose(src.nodata, -9999.0, atol=1e-3)):
                        return False
                else:
                    if src.nodata != expected_nodata:
                        return False
                if expected_cache_sig:
                    tags = src.tags()
                    sig = tags.get("CACHE_SIGNATURE", "")
                    if not sig or sig != expected_cache_sig:
                        return False
        except Exception:
            return False
    return True


def _verify_raster_artifacts(
    dem_info,
    frequency_path: str,
    qc_path: str,
    expected_cache_sig: Optional[str] = None
) -> bool:
    """验证已有淹没频率和 QC 栅格产物的尺寸、坐标系、仿射变换、数据类型与签名一致性"""
    if not os.path.exists(frequency_path) or not os.path.exists(qc_path):
        return False
    try:
        with rasterio.open(frequency_path) as src_f:
            if src_f.width != dem_info.width or src_f.height != dem_info.height:
                return False
            if str(src_f.crs) != str(dem_info.crs):
                return False
            if not np.allclose(src_f.transform, dem_info.transform, atol=1e-5):
                return False
            if src_f.dtypes[0] != 'float32':
                return False
            if expected_cache_sig:
                tags = src_f.tags()
                sig = tags.get("CACHE_SIGNATURE", "")
                if not sig or sig != expected_cache_sig:
                    return False
        with rasterio.open(qc_path) as src_qc:
            if src_qc.width != dem_info.width or src_qc.height != dem_info.height:
                return False
            if str(src_qc.crs) != str(dem_info.crs):
                return False
            if not np.allclose(src_qc.transform, dem_info.transform, atol=1e-5):
                return False
            if src_qc.dtypes[0] != 'uint16':
                return False
        return True
    except Exception:
        return False


class BatchRasterEngine:
    """
    CoastTideX 批量潮间带栅格解算核心调度引擎。
    """

    def __init__(self, raster_engine: Optional[RasterTideEngine] = None):
        self.raster_engine = raster_engine or RasterTideEngine()

    @staticmethod
    def discover_rasters(
        input_folder: str,
        recursive: bool = False,
        output_folder: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        扫描指定目录下的 GeoTIFF 影像，仅读取轻量元数据 (不扫描全像元)，并自动排除输出与临时文件。

        过滤规则:
            1. 支持扩展名: .tif, .tiff, .TIF, .TIFF；
            2. 严格排除: *_tide.nc, *_inundation.tif, *_inundation_qc.tif, *.tmp.*, *_snapshot*；
            3. 若 output_folder 位于 input_folder 内部，递归排除该输出子目录；
            4. 字典序确定性排序 (Deterministic Ordering)。
        """
        in_p = Path(input_folder)
        if not in_p.exists():
            raise FileNotFoundError(f"未找到输入目录: {input_folder}")

        out_p = Path(output_folder).resolve() if output_folder else None

        candidates: List[Path] = []
        pattern = "**/*" if recursive else "*"
        for f in in_p.glob(pattern):
            if not f.is_file():
                continue
            # 扩展名检查
            ext = f.suffix.lower()
            if ext not in [".tif", ".tiff"]:
                continue
            # 排除输出目录内部文件
            if out_p is not None:
                try:
                    f.resolve().relative_to(out_p)
                    continue
                except ValueError:
                    pass

            name_lower = f.name.lower()
            if (
                name_lower.endswith("_inundation.tif") or
                name_lower.endswith("_inundation.tiff") or
                name_lower.endswith("_inundation_qc.tif") or
                name_lower.endswith("_inundation_qc.tiff") or
                "_exposure_" in name_lower or
                name_lower.endswith("_exposure.tif") or
                name_lower.endswith("_qc.tif") or
                name_lower.endswith("_qc.tiff") or
                name_lower.endswith("_tide.nc") or
                name_lower.endswith(".tmp.tif") or
                name_lower.endswith(".tmp.nc") or
                "_snapshot" in name_lower
            ):
                continue

            candidates.append(f)

        # 确定性排序
        candidates.sort(key=lambda x: str(x.relative_to(in_p)).lower())

        results: List[Dict[str, Any]] = []
        for f in candidates:
            rel_path = str(f.relative_to(in_p))
            st = f.stat()
            size_bytes = st.st_size
            mtime_ns = st.st_mtime_ns
            size_mb = size_bytes / (1024.0 * 1024.0)

            # 轻量读取头文件元数据 (不读像元)
            meta: Dict[str, Any] = {
                "input_path": str(f.resolve()),
                "relative_path": rel_path,
                "filename": f.name,
                "file_size_bytes": size_bytes,
                "mtime_ns": mtime_ns,
                "file_size_mb": round(size_mb, 2),
                "width": 0,
                "height": 0,
                "crs": "Unknown",
                "is_projected": False,
                "resolution": (0.0, 0.0),
                "nodata": None,
                "dtype": "",
                "valid": False,
                "error": ""
            }

            try:
                with rasterio.open(f) as src:
                    meta["width"] = src.width
                    meta["height"] = src.height
                    meta["crs"] = str(src.crs) if src.crs else "Unknown"
                    meta["is_projected"] = bool(src.crs and src.crs.is_projected)
                    meta["resolution"] = src.res
                    meta["nodata"] = float(src.nodata) if src.nodata is not None else None
                    meta["dtype"] = str(src.dtypes[0])

                    # 严格校验：必须具有有效 CRS、尺寸大于 0 且通道数大于 0
                    if src.crs is None or not str(src.crs).strip() or str(src.crs).lower() == "unknown":
                        meta["valid"] = False
                        meta["error"] = "缺少坐标参考系 (CRS is None or Unknown)"
                    elif src.width <= 0 or src.height <= 0 or src.count < 1:
                        meta["valid"] = False
                        meta["error"] = f"无效的影像网格尺寸 ({src.width}x{src.height}, count={src.count})"
                    else:
                        meta["valid"] = True
            except Exception as ex:
                meta["valid"] = False
                meta["error"] = str(ex)

            results.append(meta)

        return results

    def run_batch(
        self,
        input_folder: str,
        output_folder: Optional[str] = None,
        job_mode: str = JOB_MODE_TIDE_AND_INUNDATION,
        year: int = 2024,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        freq: str = "30min",
        dem_datum: str = "egm2008",
        constituents: str | list = "all",
        target_mode: str = "intertidal",
        initial_control_spacing_m: Optional[float] = None,
        min_control_spacing_m: Optional[float] = None,
        inundation_error_tolerance_pct: Optional[float] = None,
        block_size: Optional[int] = 512,
        strict: bool = True,
        recursive: bool = False,
        existing_policy: Optional[Union[str, ExistingOutputPolicy]] = None,
        resume: Optional[bool] = None,
        overwrite: Optional[bool] = None,
        progress_callback: Optional[Callable[[int, int, str, str, Dict[str, int]], None]] = None,
        cancel_event = None,
        discovered_files: Optional[List[Dict[str, Any]]] = None,
        inclusive: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        顺序执行批量潮间带栅格解算任务 (max_parallel_tiles = 1)。

        特性与安全保障:
            1. 严格二阶段执行: Tide Cache 完成 (*_tide.nc) -> 淹没频率 (*_inundation.tif)；
            2. 单文件失败隔离 (Failure Isolation): 某瓦片异常不导致整体中断，记录 FAILED 并继续；
            3. 统一 ExistingOutputPolicy 策略调度: 彻底避免 resume 与 overwrite 互相打架；
            4. 断点恢复 (Resume): 已完成 DONE 瓦片安全跳过并维持 DONE 状态，TIDE_READY 瓦片直接计算频率；
            5. Mode 3 (inundation-from-cache) 下 Tide Cache 绝对只读保护；
            6. 递归同名文件子路径镜像输出 (Subdirectory Path Mirroring)；
            7. 任务取消安全保护: 取消时仅清理当前瓦片的临时文件，已完成瓦片完好无损。
        """
        # 归一化策略与任务模式
        policy = normalize_existing_output_policy(
            existing_policy=existing_policy,
            resume=resume,
            overwrite=overwrite
        )
        orig_job_mode = str(job_mode).strip().lower()
        if orig_job_mode in ("tide", "tide_only"):
            job_mode = JOB_MODE_TIDE_ONLY
        elif orig_job_mode in ("exposure", "tide_exposure", "tide-exposure"):
            job_mode = JOB_MODE_TIDE_AND_EXPOSURE
        elif orig_job_mode in ("exposure_duration", "exposure-from-cache", "exposure_from_cache"):
            job_mode = JOB_MODE_EXPOSURE_FROM_CACHE
        elif orig_job_mode in ("tide_inundation", "tide-inundation", "inundation"):
            job_mode = JOB_MODE_TIDE_AND_INUNDATION
        elif orig_job_mode in ("inundation_from_cache", "inundation-from-cache"):
            job_mode = JOB_MODE_INUNDATION_FROM_CACHE
        elif orig_job_mode in ("all", "full", "all_products"):
            job_mode = JOB_MODE_ALL
        else:
            job_mode = orig_job_mode

        if job_mode not in VALID_JOB_MODES:
            raise ValueError(
                f"未知的批量解算模式: '{orig_job_mode}'。合法模式包括: {sorted(list(VALID_JOB_MODES))}"
            )

        in_p = Path(input_folder)
        if not in_p.exists():
            raise FileNotFoundError(f"未找到输入目录: {input_folder}")

        if output_folder is None:
            output_folder = str(in_p / "CoastTideX_output")
        out_p = Path(output_folder)
        out_p.mkdir(parents=True, exist_ok=True)

        manifest = BatchManifest(str(out_p))
        if policy in (ExistingOutputPolicy.RESUME, ExistingOutputPolicy.ERROR_IF_EXISTS):
            manifest.load()

        if discovered_files is None:
            # 命令行或自动化脚本调用：自动执行一次完整扫描
            discovered = self.discover_rasters(input_folder, recursive=recursive, output_folder=str(out_p))
        else:
            # GUI 或上层显式传入扫描快照：严密校验快照未发生失效并直接复用
            discovered = []
            in_resolved = in_p.resolve()
            for item in discovered_files:
                item_copy = dict(item)
                p = Path(item_copy["input_path"])
                if not p.exists():
                    raise ScanSnapshotStaleError(f"扫描快照中的影像文件已不存在: {p}，请重新扫描！")
                try:
                    p.resolve().relative_to(in_resolved)
                except ValueError:
                    raise ScanSnapshotStaleError(f"扫描快照中的文件不属于输入目录: {p}，请重新扫描！")
                st = p.stat()
                if "file_size_bytes" in item_copy and item_copy["file_size_bytes"] is not None:
                    if st.st_size != item_copy["file_size_bytes"]:
                        raise ScanSnapshotStaleError(f"扫描快照中文件大小已发生变化: {p}，请重新扫描！")
                if "mtime_ns" in item_copy and item_copy["mtime_ns"] is not None:
                    if st.st_mtime_ns != item_copy["mtime_ns"]:
                        raise ScanSnapshotStaleError(f"扫描快照中文件修改时间已发生变化: {p}，请重新扫描！")
                discovered.append(item_copy)
        total_files = len(discovered)

        summary_counts = {
            "total": total_files,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "cancelled": 0
        }

        if progress_callback:
            progress_callback(0, 0, "", f"批量扫描就绪 (策略: {policy.value})", summary_counts)

        # 逐个文件顺序解算 (max_parallel_tiles = 1)
        for idx, item in enumerate(discovered):
            if cancel_event is not None and cancel_event.is_set():
                summary_counts["cancelled"] += (total_files - idx)
                if progress_callback:
                    progress_callback(100, 0, "", "任务已取消", summary_counts)
                break

            input_path = item["input_path"]
            filename = item["filename"]
            rel_file_path = Path(item["relative_path"])

            # 递归同名文件子路径镜像保护 (Subdirectory Path Mirroring)
            if recursive and len(rel_file_path.parts) > 1:
                tile_out_dir = (out_p / rel_file_path.parent).resolve()
            else:
                tile_out_dir = out_p

            tile_out_dir.mkdir(parents=True, exist_ok=True)
            stem = rel_file_path.stem

            from .exposure_engine import ExposureProductPaths
            from .tide_cache import calculate_exposure_from_tide_cache

            tide_cache_path = str(tile_out_dir / f"{stem}_tide.nc")
            frequency_path = str(tile_out_dir / f"{stem}_inundation.tif")
            qc_path = str(tile_out_dir / f"{stem}_inundation_qc.tif")

            exp_paths = ExposureProductPaths(
                exposure_fraction_path=str(tile_out_dir / f"{stem}_exposure_fraction.tif"),
                exposure_duration_h_path=str(tile_out_dir / f"{stem}_exposure_duration_h.tif"),
                exposure_max_continuous_h_path=str(tile_out_dir / f"{stem}_exposure_max_continuous_h.tif"),
                exposure_mean_event_h_path=str(tile_out_dir / f"{stem}_exposure_mean_event_h.tif"),
                exposure_event_count_path=str(tile_out_dir / f"{stem}_exposure_event_count.tif"),
                exposure_valid_time_fraction_path=str(tile_out_dir / f"{stem}_exposure_valid_time_fraction.tif"),
                exposure_qc_path=str(tile_out_dir / f"{stem}_exposure_qc.tif")
            )

            manifest.upsert(
                input_path,
                input_name=filename,
                tide_cache_path=tide_cache_path,
                frequency_path=frequency_path,
                qc_path=qc_path,
                exposure_output_dir=str(tile_out_dir),
                exposure_products_complete=False,
                exposure_fraction_path=exp_paths.exposure_fraction_path,
                exposure_duration_h_path=exp_paths.exposure_duration_h_path,
                exposure_max_continuous_h_path=exp_paths.exposure_max_continuous_h_path,
                exposure_mean_event_h_path=exp_paths.exposure_mean_event_h_path,
                exposure_event_count_path=exp_paths.exposure_event_count_path,
                exposure_valid_time_fraction_path=exp_paths.exposure_valid_time_fraction_path,
                exposure_qc_path=exp_paths.exposure_qc_path,
                crs=item["crs"],
                width=item["width"],
                height=item["height"],
                nodata=item["nodata"],
                time_step=freq,
                timezone="UTC",
                dem_datum=str(dem_datum).lower() if dem_datum is not None else "",
                target_mode=str(target_mode).lower() if target_mode is not None else ""
            )

            # 检查文件基本有效性
            if not item["valid"]:
                manifest.upsert(
                    input_path,
                    status=STATUS_FAILED,
                    run_action="FAILED",
                    error_message=f"无效的 GeoTIFF 文件: {item.get('error', '')}"
                )
                manifest.save()
                summary_counts["failed"] += 1
                continue

            prev_status = manifest.get_status(input_path)

            # 确定本次任务各产物阶段需求
            need_tide = (job_mode in [JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_ALL])
            need_freq = (job_mode in [JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE, JOB_MODE_ALL])
            need_exp = (job_mode in [JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_EXPOSURE_FROM_CACHE, JOB_MODE_ALL])
            is_from_cache = (job_mode in (JOB_MODE_INUNDATION_FROM_CACHE, JOB_MODE_EXPOSURE_FROM_CACHE))

            dem_info = self.raster_engine.inspect_raster(input_path, compute_valid_count=False)

            # ---------------- FROM_CACHE 模式前置检查与权威缓存元数据提取 ----------------
            cache_ok = False
            actual_cache_sig = ""

            eff_init_sp = initial_control_spacing_m if initial_control_spacing_m is not None else getattr(self.raster_engine, 'initial_control_spacing_m', 4000.0)
            eff_min_sp = min_control_spacing_m if min_control_spacing_m is not None else getattr(self.raster_engine, 'min_control_spacing_m', 500.0)
            eff_tol = inundation_error_tolerance_pct if inundation_error_tolerance_pct is not None else getattr(self.raster_engine, 'inundation_error_tolerance_pct', 1.0)
            eff_inclusive = inclusive if inclusive is not None else "left"

            if is_from_cache:
                # 1. 检查 Tide Cache 是否存在
                if not os.path.exists(tide_cache_path):
                    manifest.upsert(
                        input_path,
                        status=STATUS_FAILED,
                        run_action="FAILED",
                        error_message=f"FROM_CACHE 模式前置 Tide Cache 不存在: {tide_cache_path} (绝不回退至 FES 计算)"
                    )
                    manifest.save()
                    summary_counts["failed"] += 1
                    continue

                # 2. 检查 Tide Cache 完整性标记
                if not is_cache_complete(tide_cache_path):
                    manifest.upsert(
                        input_path,
                        status=STATUS_FAILED,
                        run_action="FAILED",
                        error_message=f"FROM_CACHE 模式前置 Tide Cache 不完整 (未包含 CACHE_COMPLETE 标记): {tide_cache_path}"
                    )
                    manifest.save()
                    summary_counts["failed"] += 1
                    continue

                # 3. 检查元数据与节点/单元数
                try:
                    cache_meta_info = inspect_tide_cache_metadata(tide_cache_path)
                except Exception as ex:
                    manifest.upsert(
                        input_path,
                        status=STATUS_FAILED,
                        run_action="FAILED",
                        error_message=f"读取 Tide Cache 元数据失败: {ex}"
                    )
                    manifest.save()
                    summary_counts["failed"] += 1
                    continue

                if cache_meta_info.get("num_nodes", 0) <= 0 or cache_meta_info.get("num_cells", 0) <= 0:
                    manifest.upsert(
                        input_path,
                        status=STATUS_FAILED,
                        run_action="FAILED",
                        error_message=f"Tide Cache 节点或网格单元为空 (nodes={cache_meta_info.get('num_nodes')}, cells={cache_meta_info.get('num_cells')}): {tide_cache_path}"
                    )
                    manifest.save()
                    summary_counts["failed"] += 1
                    continue

                # 4. 严格校验 DEM 几何/标识兼容性 (只校验 DEM 几何与文件身份，不比对 GUI/CLI 传入的潮位时空参数)
                fsize = getattr(dem_info, "file_size_bytes", None)
                mtime = getattr(dem_info, "mtime_ns", None)
                if (fsize is None or mtime is None) and os.path.exists(input_path):
                    try:
                        st = os.stat(input_path)
                        fsize = st.st_size
                        mtime = st.st_mtime_ns
                    except Exception:
                        pass

                dem_compat_spec = {
                    "width": dem_info.width,
                    "height": dem_info.height,
                    "crs": dem_info.crs,
                    "transform": dem_info.transform,
                    "bounds": dem_info.bounds,
                    "nodata": dem_info.nodata,
                    "file_size_bytes": fsize,
                    "mtime_ns": mtime,
                }
                c_compat, c_reasons = validate_tide_cache_compatibility(tide_cache_path, dem_compat_spec)
                if not c_compat:
                    manifest.upsert(
                        input_path,
                        status=STATUS_FAILED,
                        run_action="FAILED",
                        error_message=f"FROM_CACHE 模式 Tide Cache 与目标 DEM 几何/标识不兼容: {'; '.join(c_reasons)}"
                    )
                    manifest.save()
                    summary_counts["failed"] += 1
                    continue

                actual_cache_sig = cache_meta_info.get("signature", "")
                cache_ok = True

                # 同步 Cache 中的权威时间与控制网格参数至 manifest
                c_attrs = cache_meta_info.get("metadata", {})
                manifest.upsert(
                    input_path,
                    time_start=str(c_attrs.get("TIME_START", "")),
                    time_end=str(c_attrs.get("TIME_END", "")),
                    time_step=str(c_attrs.get("TIME_STEP", freq)),
                    timezone=str(c_attrs.get("TIMEZONE", "UTC")),
                    time_samples=cache_meta_info.get("time_samples", 0),
                    dem_datum=str(c_attrs.get("DEM_DATUM", dem_datum if dem_datum is not None else "")).lower(),
                    target_mode=str(c_attrs.get("TARGET_MODE", target_mode if target_mode is not None else "")).lower(),
                    cache_signature=actual_cache_sig,
                    control_node_count=cache_meta_info.get("num_nodes", 0),
                )

            else:
                # ---------------- 非 FROM_CACHE 模式：按本任务配置构建 Expected Spec ----------------
                st_val = start_time if start_time is not None else f"{year:04d}-01-01 00:00:00"
                et_val = end_time if end_time is not None else f"{year+1:04d}-01-01 00:00:00"

                expected_spec = build_expected_cache_spec(
                    info=dem_info,
                    start_time=st_val,
                    end_time=et_val,
                    freq=freq,
                    dem_datum=dem_datum,
                    constituents=constituents,
                    target_mode=target_mode,
                    initial_control_spacing_m=eff_init_sp,
                    min_control_spacing_m=eff_min_sp,
                    inundation_error_tolerance_pct=eff_tol,
                    topology_max_resolution_m=self.raster_engine.topology_max_resolution_m,
                    topology_valid_fraction_threshold=self.raster_engine.topology_valid_fraction_threshold,
                    fes_model="FES2022b",
                    fes_source_type="native_lgp2",
                    source_tz="UTC",
                    inclusive=eff_inclusive
                )
                actual_cache_sig = expected_spec["signature"]
                manifest.upsert(
                    input_path,
                    time_start=st_val,
                    time_end=et_val,
                    time_step=freq,
                    timezone="UTC",
                    time_samples=expected_spec["time_samples"],
                    dem_datum=str(dem_datum).lower(),
                    target_mode=str(target_mode).lower(),
                    cache_signature=actual_cache_sig,
                )

                if os.path.exists(tide_cache_path):
                    if is_cache_complete(tide_cache_path):
                        c_compat, c_reasons = validate_tide_cache_compatibility(tide_cache_path, expected_spec)
                        if c_compat:
                            cache_ok = True
                        elif policy == ExistingOutputPolicy.RESUME:
                            # 严谨科学防御: 缓存属于不同参数集时严禁错误复用或静默跳过！
                            err_msg = f"Existing cache belongs to another parameter set: {'; '.join(c_reasons)}. Use OVERWRITE to rebuild."
                            manifest.upsert(input_path, status=STATUS_FAILED, run_action="FAILED", error_message=err_msg)
                            manifest.save()
                            summary_counts["failed"] += 1
                            continue

            # ---------------- ERROR_IF_EXISTS 策略防线 ----------------
            if policy == ExistingOutputPolicy.ERROR_IF_EXISTS:
                conflict_files = []
                if need_tide and os.path.exists(tide_cache_path):
                    conflict_files.append(tide_cache_path)
                if need_freq:
                    if os.path.exists(frequency_path):
                        conflict_files.append(frequency_path)
                    if os.path.exists(qc_path):
                        conflict_files.append(qc_path)
                if need_exp:
                    for ep in [
                        exp_paths.exposure_fraction_path,
                        exp_paths.exposure_duration_h_path,
                        exp_paths.exposure_max_continuous_h_path,
                        exp_paths.exposure_mean_event_h_path,
                        exp_paths.exposure_event_count_path,
                        exp_paths.exposure_valid_time_fraction_path,
                        exp_paths.exposure_qc_path,
                    ]:
                        if os.path.exists(ep):
                            conflict_files.append(ep)

                if conflict_files:
                    err_msg = f"ExistingOutputError: 目标输出产物已存在且当前策略为 error_if_exists: {', '.join(conflict_files)}"
                    manifest.upsert(input_path, status=STATUS_FAILED, run_action="FAILED", error_message=err_msg)
                    manifest.save()
                    summary_counts["failed"] += 1
                    continue

            # ---------------- RESUME 策略深层兼容性与断点跳过检查 (权威 actual_cache_sig) ----------------
            if policy == ExistingOutputPolicy.RESUME:
                if job_mode == JOB_MODE_TIDE_ONLY:
                    if cache_ok:
                        manifest.upsert(input_path, status=STATUS_DONE, run_action="SKIPPED_EXISTING", exposure_products_complete=False)
                        manifest.save()
                        summary_counts["skipped"] += 1
                        continue

                elif job_mode == JOB_MODE_TIDE_AND_INUNDATION:
                    if cache_ok and _verify_raster_artifacts(dem_info, frequency_path, qc_path, expected_cache_sig=actual_cache_sig):
                        manifest.upsert(input_path, status=STATUS_DONE, run_action="SKIPPED_EXISTING", exposure_products_complete=False)
                        manifest.save()
                        summary_counts["skipped"] += 1
                        continue

                elif job_mode == JOB_MODE_INUNDATION_FROM_CACHE:
                    if _verify_raster_artifacts(dem_info, frequency_path, qc_path, expected_cache_sig=actual_cache_sig):
                        manifest.upsert(input_path, status=STATUS_DONE, run_action="SKIPPED_EXISTING", exposure_products_complete=False)
                        manifest.save()
                        summary_counts["skipped"] += 1
                        continue

                elif job_mode == JOB_MODE_TIDE_AND_EXPOSURE:
                    if cache_ok and _verify_exposure_artifacts(dem_info, exp_paths, expected_cache_sig=actual_cache_sig):
                        manifest.upsert(input_path, status=STATUS_DONE, run_action="SKIPPED_EXISTING", exposure_products_complete=True)
                        manifest.save()
                        summary_counts["skipped"] += 1
                        continue

                elif job_mode == JOB_MODE_EXPOSURE_FROM_CACHE:
                    if _verify_exposure_artifacts(dem_info, exp_paths, expected_cache_sig=actual_cache_sig):
                        manifest.upsert(input_path, status=STATUS_DONE, run_action="SKIPPED_EXISTING", exposure_products_complete=True)
                        manifest.save()
                        summary_counts["skipped"] += 1
                        continue

                elif job_mode == JOB_MODE_ALL:
                    if (cache_ok and
                        _verify_raster_artifacts(dem_info, frequency_path, qc_path, expected_cache_sig=actual_cache_sig) and
                        _verify_exposure_artifacts(dem_info, exp_paths, expected_cache_sig=actual_cache_sig)):
                        manifest.upsert(input_path, status=STATUS_DONE, run_action="SKIPPED_EXISTING", exposure_products_complete=True)
                        manifest.save()
                        summary_counts["skipped"] += 1
                        continue

            overall_pct = int(100 * idx / max(1, total_files))

            # 执行单文件处理 (带单瓦片失败隔离)
            try:
                # ---------------- Stage 1: Tide Calculation & Tide Cache ----------------
                need_tide = (job_mode in [JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_ALL])
                cache_already_ready = (
                    policy == ExistingOutputPolicy.RESUME and
                    cache_ok
                )

                if need_tide and not cache_already_ready:
                    manifest.upsert(input_path, status=STATUS_TIDE_RUNNING, run_action="TIDE_RUNNING")
                    manifest.save()
                    if progress_callback:
                        progress_callback(overall_pct, 10, str(rel_file_path), "Stage 1: 控制网格 FES 解算与 Tide Cache 生成...", summary_counts)

                    t_tide_start = time.time()

                    def _tile_prog(pct_val, msg_val):
                        if progress_callback:
                            progress_callback(overall_pct, int(pct_val * 0.5), str(rel_file_path), f"Stage 1: {msg_val}", summary_counts)

                    # 执行自适应四叉树控制网格解算并导出 Tide Cache
                    grid_data = self.raster_engine.build_tide_control_grid(
                        dem_path=input_path,
                        export_tide_cache_path=tide_cache_path,
                        year=year,
                        start_time=start_time,
                        end_time=end_time,
                        freq=freq,
                        dem_datum=dem_datum,
                        constituents=constituents,
                        target_mode=target_mode,
                        initial_control_spacing_m=eff_init_sp,
                        min_control_spacing_m=eff_min_sp,
                        inundation_error_tolerance_pct=eff_tol,
                        strict=strict,
                        progress_callback=_tile_prog,
                        cancel_event=cancel_event,
                        inclusive=eff_inclusive,
                        allow_overwrite=(policy in (ExistingOutputPolicy.OVERWRITE, ExistingOutputPolicy.RESUME))
                    )

                    elapsed_tide = time.time() - t_tide_start
                    manifest.upsert(
                        input_path,
                        status=STATUS_TIDE_READY,
                        run_action="TIDE_READY",
                        control_node_count=grid_data.get("num_nodes", 0),
                        time_samples=grid_data.get("time_samples", 0),
                        elapsed_tide_seconds=round(elapsed_tide, 2)
                    )
                    manifest.save()

                elif cache_already_ready:
                    manifest.upsert(input_path, status=STATUS_TIDE_READY)
                    manifest.save()

                # 如果仅要求 Tide Cache，则本瓦片到此完成
                if job_mode == JOB_MODE_TIDE_ONLY:
                    manifest.upsert(input_path, status=STATUS_DONE, run_action="PROCESSED", error_message="")
                    manifest.save()
                    summary_counts["completed"] += 1
                    continue

                # ---------------- Stage 2a: Inundation Frequency Calculation ----------------
                need_freq = (job_mode in [JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE, JOB_MODE_ALL])
                elapsed_freq = 0.0
                valid_pixels_cnt = 0
                if need_freq:
                    manifest.upsert(input_path, status=STATUS_FREQUENCY_RUNNING, run_action="FREQUENCY_RUNNING")
                    manifest.save()
                    if progress_callback:
                        progress_callback(overall_pct, 55, str(rel_file_path), "Stage 2a: 基于 Tide Cache 解算潜在天文潮淹没频率...", summary_counts)

                    t_freq_start = time.time()

                    def _freq_prog(pct_val, msg_val):
                        if progress_callback:
                            progress_callback(overall_pct, 50 + int(pct_val * 0.25), filename, f"Stage 2a: {msg_val}", summary_counts)

                    freq_summary = calculate_inundation_from_tide_cache(
                        dem_path=input_path,
                        cache_path=tide_cache_path,
                        output_path=frequency_path,
                        qc_output_path=qc_path,
                        block_size=block_size,
                        allow_overwrite=(policy in (ExistingOutputPolicy.OVERWRITE, ExistingOutputPolicy.RESUME)),
                        progress_callback=_freq_prog,
                        cancel_event=cancel_event
                    )
                    elapsed_freq = time.time() - t_freq_start
                    valid_pixels_cnt = freq_summary.input_valid_pixels

                # ---------------- Stage 2b: Exposure Duration Calculation ----------------
                need_exp = (job_mode in [JOB_MODE_TIDE_AND_EXPOSURE, JOB_MODE_EXPOSURE_FROM_CACHE, JOB_MODE_ALL])
                elapsed_exp = 0.0
                if need_exp:
                    manifest.upsert(input_path, status=STATUS_EXPOSURE_RUNNING, run_action="EXPOSURE_RUNNING")
                    manifest.save()
                    if progress_callback:
                        progress_callback(overall_pct, 75, str(rel_file_path), "Stage 2b: 基于 Tide Cache 解算潜在天文潮露出时间域产品...", summary_counts)

                    t_exp_start = time.time()

                    def _exp_prog(pct_val, msg_val):
                        if progress_callback:
                            progress_callback(overall_pct, 75 + int(pct_val * 0.25), filename, f"Stage 2b: {msg_val}", summary_counts)

                    exp_res = calculate_exposure_from_tide_cache(
                        dem_path=input_path,
                        cache_path=tide_cache_path,
                        output_paths=exp_paths,
                        block_size=block_size or 512,
                        allow_overwrite=(policy in (ExistingOutputPolicy.OVERWRITE, ExistingOutputPolicy.RESUME)),
                        progress_callback=_exp_prog,
                        cancel_event=cancel_event
                    )
                    elapsed_exp = time.time() - t_exp_start
                    if not need_freq and isinstance(exp_res, dict):
                        valid_pixels_cnt = exp_res.get("input_valid_pixels", 0)

                manifest.upsert(
                    input_path,
                    status=STATUS_DONE,
                    run_action="PROCESSED",
                    valid_pixel_count=valid_pixels_cnt,
                    elapsed_frequency_seconds=round(elapsed_freq, 2) if need_freq else None,
                    elapsed_exposure_seconds=round(elapsed_exp, 2) if need_exp else None,
                    exposure_products_complete=True if need_exp else False,
                    error_message=""
                )
                manifest.save()
                summary_counts["completed"] += 1

            except RasterCalculationCancelled:
                manifest.upsert(input_path, status=STATUS_CANCELLED, run_action="CANCELLED", error_message="用户取消了任务")
                manifest.save()
                summary_counts["cancelled"] += 1
                if progress_callback:
                    progress_callback(100, 0, filename, "任务已取消", summary_counts)
                break

            except Exception as ex:
                err_msg = f"{type(ex).__name__}: {str(ex)}"
                manifest.upsert(input_path, status=STATUS_FAILED, run_action="FAILED", error_message=err_msg)
                manifest.save()
                summary_counts["failed"] += 1
                # 隔离错误并继续后续瓦片
                continue

        if progress_callback:
            progress_callback(100, 100, "", "批量任务完成", summary_counts)

        return {
            "output_folder": str(out_p),
            "manifest_json": str(manifest.json_path),
            "manifest_csv": str(manifest.csv_path),
            "counts": summary_counts,
            **summary_counts
        }
