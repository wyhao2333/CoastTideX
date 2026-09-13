"""
CoastTideX 批量潮间带栅格解算调度引擎 (Batch Intertidal Raster Engine v1.5 Alpha Hardened)
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
    calculate_inundation_from_tide_cache, estimate_tide_cache_size,
    inspect_tide_cache_metadata, validate_tide_cache_compatibility,
    TideCacheCompatibilityError
)

# 任务运行模式
JOB_MODE_TIDE_ONLY = "tide"                                 # 仅解算控制节点潮位并生成 Tide Cache (*_tide.nc)
JOB_MODE_TIDE_AND_INUNDATION = "tide-inundation"            # 先生成 Tide Cache，再解算淹没频率 (默认完整两阶段流程)
JOB_MODE_INUNDATION_FROM_CACHE = "inundation-from-cache"    # 从已有 Tide Cache 直接解算淹没频率 (零 FES 调用)

# 现有输出处理策略 (ExistingOutputPolicy)
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
        "status",
        "run_action",
        "time_start",
        "time_end",
        "time_step",
        "time_samples",
        "crs",
        "width",
        "height",
        "nodata",
        "valid_pixel_count",
        "control_node_count",
        "elapsed_tide_seconds",
        "elapsed_frequency_seconds",
        "error_message"
    ]

    def __init__(self, output_folder: str):
        self.output_folder = Path(output_folder)
        self.json_path = self.output_folder / "batch_manifest.json"
        self.csv_path = self.output_folder / "batch_manifest.csv"
        self.records: Dict[str, Dict[str, Any]] = OrderedDict()

    def load(self) -> None:
        """从已存在的 batch_manifest.json 加载既有记录"""
        if self.json_path.exists():
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.records = OrderedDict((item["input_path"], item) for item in data)
            except Exception:
                self.records = OrderedDict()

    def get_status(self, input_path: str) -> Optional[str]:
        item = self.records.get(input_path)
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
            size_mb = f.stat().st_size / (1024.0 * 1024.0)

            # 轻量读取头文件元数据 (不读像元)
            meta: Dict[str, Any] = {
                "input_path": str(f.resolve()),
                "relative_path": rel_path,
                "filename": f.name,
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
                    meta["valid"] = True
            except Exception as ex:
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
        cancel_event = None
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
        # 归一化策略
        policy = normalize_existing_output_policy(
            existing_policy=existing_policy,
            resume=resume,
            overwrite=overwrite
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

        discovered = self.discover_rasters(input_folder, recursive=recursive, output_folder=str(out_p))
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

            tide_cache_path = str(tile_out_dir / f"{stem}_tide.nc")
            frequency_path = str(tile_out_dir / f"{stem}_inundation.tif")
            qc_path = str(tile_out_dir / f"{stem}_inundation_qc.tif")

            manifest.upsert(
                input_path,
                input_name=filename,
                tide_cache_path=tide_cache_path,
                frequency_path=frequency_path,
                qc_path=qc_path,
                crs=item["crs"],
                width=item["width"],
                height=item["height"],
                nodata=item["nodata"],
                time_step=freq
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

            # ---------------- ERROR_IF_EXISTS 策略防线 ----------------
            if policy == ExistingOutputPolicy.ERROR_IF_EXISTS:
                conflict_files = []
                if job_mode in (JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION):
                    if os.path.exists(tide_cache_path):
                        conflict_files.append(tide_cache_path)
                if job_mode in (JOB_MODE_TIDE_AND_INUNDATION, JOB_MODE_INUNDATION_FROM_CACHE):
                    if os.path.exists(frequency_path):
                        conflict_files.append(frequency_path)
                    if os.path.exists(qc_path):
                        conflict_files.append(qc_path)

                if conflict_files:
                    err_msg = f"ExistingOutputError: 目标输出产物已存在且当前策略为 error_if_exists: {', '.join(conflict_files)}"
                    manifest.upsert(input_path, status=STATUS_FAILED, run_action="FAILED", error_message=err_msg)
                    manifest.save()
                    summary_counts["failed"] += 1
                    continue

            # ---------------- RESUME 策略断点跳过检查 ----------------
            if policy == ExistingOutputPolicy.RESUME:
                if job_mode == JOB_MODE_TIDE_ONLY:
                    if is_cache_complete(tide_cache_path):
                        # 维持 DONE 状态，标记本轮 run_action 为 SKIPPED_EXISTING
                        manifest.upsert(input_path, status=STATUS_DONE, run_action="SKIPPED_EXISTING")
                        manifest.save()
                        summary_counts["skipped"] += 1
                        continue
                elif job_mode == JOB_MODE_TIDE_AND_INUNDATION:
                    if os.path.exists(frequency_path) and (prev_status == STATUS_DONE or (os.path.exists(qc_path) and is_cache_complete(tide_cache_path))):
                        manifest.upsert(input_path, status=STATUS_DONE, run_action="SKIPPED_EXISTING")
                        manifest.save()
                        summary_counts["skipped"] += 1
                        continue
                elif job_mode == JOB_MODE_INUNDATION_FROM_CACHE:
                    if os.path.exists(frequency_path) and (prev_status == STATUS_DONE or os.path.exists(qc_path)):
                        manifest.upsert(input_path, status=STATUS_DONE, run_action="SKIPPED_EXISTING")
                        manifest.save()
                        summary_counts["skipped"] += 1
                        continue

            # ---------------- JOB_MODE_INUNDATION_FROM_CACHE 前置检查 ----------------
            if job_mode == JOB_MODE_INUNDATION_FROM_CACHE:
                if not os.path.exists(tide_cache_path):
                    manifest.upsert(
                        input_path,
                        status=STATUS_FAILED,
                        run_action="FAILED",
                        error_message=f"Mode 3 前置 Tide Cache 不存在: {tide_cache_path} (绝不回退至 FES 计算)"
                    )
                    manifest.save()
                    summary_counts["failed"] += 1
                    continue
                if not is_cache_complete(tide_cache_path):
                    manifest.upsert(
                        input_path,
                        status=STATUS_FAILED,
                        run_action="FAILED",
                        error_message=f"Mode 3 前置 Tide Cache 不完整 (未包含 CACHE_COMPLETE 标记): {tide_cache_path}"
                    )
                    manifest.save()
                    summary_counts["failed"] += 1
                    continue

            overall_pct = int(100 * idx / max(1, total_files))

            # 执行单文件处理 (带单瓦片失败隔离)
            try:
                # ---------------- Stage 1: Tide Calculation & Tide Cache ----------------
                need_tide = (job_mode in [JOB_MODE_TIDE_ONLY, JOB_MODE_TIDE_AND_INUNDATION])
                cache_already_ready = (
                    policy == ExistingOutputPolicy.RESUME and
                    is_cache_complete(tide_cache_path)
                )

                if need_tide and not cache_already_ready:
                    manifest.upsert(input_path, status=STATUS_TIDE_RUNNING, run_action="TIDE_RUNNING")
                    manifest.save()
                    if progress_callback:
                        progress_callback(overall_pct, 10, filename, "Stage 1: 控制网格 FES 解算与 Tide Cache 生成...", summary_counts)

                    t_tide_start = time.time()

                    def _tile_prog(pct_val, msg_val):
                        if progress_callback:
                            progress_callback(overall_pct, int(pct_val * 0.5), filename, f"Stage 1: {msg_val}", summary_counts)

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
                        initial_control_spacing_m=initial_control_spacing_m,
                        min_control_spacing_m=min_control_spacing_m,
                        inundation_error_tolerance_pct=inundation_error_tolerance_pct,
                        strict=strict,
                        progress_callback=_tile_prog,
                        cancel_event=cancel_event,
                        allow_overwrite=(policy == ExistingOutputPolicy.OVERWRITE)
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

                # ---------------- Stage 2: Inundation Frequency Calculation ----------------
                # 注意: 在 JOB_MODE_INUNDATION_FROM_CACHE 下，tide_cache_path 严格为只读输入，绝不修改
                manifest.upsert(input_path, status=STATUS_FREQUENCY_RUNNING, run_action="FREQUENCY_RUNNING")
                manifest.save()
                if progress_callback:
                    progress_callback(overall_pct, 60, filename, "Stage 2: 基于 Tide Cache 解算潜在天文潮淹没频率...", summary_counts)

                t_freq_start = time.time()

                def _freq_prog(pct_val, msg_val):
                    if progress_callback:
                        progress_callback(overall_pct, 50 + int(pct_val * 0.5), filename, f"Stage 2: {msg_val}", summary_counts)

                freq_summary = calculate_inundation_from_tide_cache(
                    dem_path=input_path,
                    cache_path=tide_cache_path,
                    output_path=frequency_path,
                    qc_output_path=qc_path,
                    block_size=block_size,
                    allow_overwrite=(policy == ExistingOutputPolicy.OVERWRITE),
                    progress_callback=_freq_prog,
                    cancel_event=cancel_event
                )

                elapsed_freq = time.time() - t_freq_start
                manifest.upsert(
                    input_path,
                    status=STATUS_DONE,
                    run_action="PROCESSED",
                    valid_pixel_count=freq_summary.input_valid_pixels,
                    elapsed_frequency_seconds=round(elapsed_freq, 2),
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
            "counts": summary_counts
        }
