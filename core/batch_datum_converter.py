"""
CoastTideX 批量 DEM 垂直基准转换模块 (Batch DEM Datum Converter v1.7.1)
===================================================================

功能职责:
1. 文件夹级 DEM_EGM2008 栅格瓦片自动化扫描与确定性排序；
2. 瓦片元数据检查与 DATUM=MSL 智能探测 (防止重复转换)；
3. 断点恢复 (Resume) 机制: 基于 conversion_manifest.csv 与目标产物检查安全跳过；
4. 单瓦片流式解算与内存控制: 逐瓦片按分块处理，杜绝全量加载；
5. 错误隔离与高可用调度: 单瓦片失败自动记录错误，不中断批处理队列；
6. conversion_manifest.csv (及 .json) 任务清单原子级持久化；
7. 多线程/异步 Worker 封装，保障 GUI 交互流畅不阻塞。
"""

import os
import sys
import csv
import json
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Tuple
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

import rasterio

from .dem_datum_converter import (
    DEMDatumConverter,
    DEMConversionSummary,
    QC_MDT_NATIVE,
    QC_MDT_EXTRAPOLATED,
    QC_MDT_NODATA,
    MAX_MDT_EXTRAPOLATION_DISTANCE_KM
)


# 任务执行状态常量
STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED_MSL = "SKIPPED_MSL"
STATUS_SKIPPED_RESUME = "SKIPPED_RESUME"

VALID_STATUSES = {
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_SKIPPED_MSL,
    STATUS_SKIPPED_RESUME
}


@dataclass
class BatchConversionSummary:
    """批量 DEM 基准转换执行汇总统计"""
    input_dir: str
    output_dir: str
    total_tiles: int
    success_count: int
    failed_count: int
    skipped_msl_count: int
    skipped_resume_count: int
    elapsed_seconds: float
    manifest_csv: str
    manifest_json: str
    records: List[Dict[str, Any]] = field(default_factory=list)


class BatchConversionManifest:
    """
    批量转换任务清单管理器 (Batch Conversion Manifest Manager)
    支持 conversion_manifest.csv 与 conversion_manifest.json 双格式原子持久化。
    """

    FIELDS = [
        "input_file",
        "output_file",
        "status",
        "start_time",
        "end_time",
        "elapsed_seconds",
        "total_pixels",
        "valid_pixels",
        "native_mdt_pixels",
        "idw_pixels",
        "nodata_pixels",
        "error_message"
    ]

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.csv_path = self.output_dir / "conversion_manifest.csv"
        self.json_path = self.output_dir / "conversion_manifest.json"
        self.records: Dict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.load()

    def load(self) -> None:
        """从既有清单文件中恢复历史记录"""
        with self._lock:
            # 优先从 JSON 读取
            if self.json_path.exists():
                try:
                    with open(self.json_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.records = OrderedDict()
                    for item in data:
                        inp = item.get("input_file", "")
                        if inp:
                            rec = {k: "" for k in self.FIELDS}
                            rec.update(item)
                            self.records[inp] = rec
                    return
                except Exception:
                    pass

            # 回退从 CSV 读取
            if self.csv_path.exists():
                try:
                    with open(self.csv_path, "r", encoding="utf-8", newline="") as f:
                        reader = csv.DictReader(f)
                        self.records = OrderedDict()
                        for row in reader:
                            inp = row.get("input_file", "")
                            if inp:
                                rec = {k: "" for k in self.FIELDS}
                                rec.update(row)
                                self.records[inp] = rec
                except Exception:
                    self.records = OrderedDict()

    def get_entry(self, input_file: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.records.get(input_file)

    def get_status(self, input_file: str) -> Optional[str]:
        entry = self.get_entry(input_file)
        return entry.get("status") if entry else None

    def is_success(self, input_file: str) -> bool:
        status = self.get_status(input_file)
        return status == STATUS_SUCCESS

    def upsert(self, input_file: str, **kwargs) -> None:
        with self._lock:
            if input_file not in self.records:
                self.records[input_file] = {k: "" for k in self.FIELDS}
                self.records[input_file]["input_file"] = input_file
                self.records[input_file]["status"] = STATUS_PENDING
            self.records[input_file].update(kwargs)

    def save(self) -> None:
        """原子级持久化 CSV 与 JSON 清单文件"""
        with self._lock:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            items = list(self.records.values())

            # 1. 写入临时 JSON 并原子替换
            tmp_json = self.json_path.with_suffix(".tmp.json")
            with open(tmp_json, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2, ensure_ascii=False)
            os.replace(tmp_json, self.json_path)

            # 2. 写入临时 CSV 并原子替换
            tmp_csv = self.csv_path.with_suffix(".tmp.csv")
            with open(tmp_csv, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDS)
                writer.writeheader()
                for item in items:
                    writer.writerow({k: item.get(k, "") for k in self.FIELDS})
            os.replace(tmp_csv, self.csv_path)


def scan_dem_directory(input_dir: str, recursive: bool = True) -> List[str]:
    """
    扫描目标目录下的全部待转换 DEM 影像 (*.tif, *.tiff)。
    自动排除中间产物 (*_MSL.tif, *_qc.tif, *.tmp.tif) 以免造成循环依赖。
    返回按路径字典序严格排序的路径列表。
    """
    input_path = Path(input_dir)
    if not input_path.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")

    pattern = "**/*" if recursive else "*"
    valid_exts = {".tif", ".tiff", ".geotiff"}
    excluded_suffixes = {"_msl.tif", "_msl.tiff", "_qc.tif", "_qc.tiff", ".tmp.tif", ".tmp.tiff"}

    candidates = []
    for p in input_path.glob(pattern):
        if p.is_file() and p.suffix.lower() in valid_exts:
            fname_lower = p.name.lower()
            if any(fname_lower.endswith(ex) for ex in excluded_suffixes):
                continue
            candidates.append(str(p.resolve()))

    candidates.sort()
    return candidates


def is_dem_already_msl(file_path: str) -> Tuple[bool, str]:
    """
    探测 GeoTIFF 元数据标签，判断输入 DEM 是否已经为 MSL 基准。
    返回 (is_msl, detection_reason)。
    """
    try:
        with rasterio.open(file_path) as src:
            tags = src.tags()
            datum_tag = str(tags.get("DATUM", "")).strip().upper()
            ref_tag = str(tags.get("ANALYSIS_REFERENCE", "")).strip().upper()
            target_tag = str(tags.get("TARGET_VERTICAL_DATUM", "")).strip().upper()

            if datum_tag == "MSL" or ref_tag == "MSL" or target_tag == "MSL":
                return True, f"GeoTIFF 标签标明基准已为 MSL (DATUM={datum_tag}, REF={ref_tag})"
    except Exception as e:
        return False, f"元数据探测异常: {e}"
    return False, ""


class BatchDEMDatumConverter:
    """
    批量 DEM 垂直基准转换调度引擎 (Batch DEM Datum Converter)
    管理批量扫描、断点续传、逐瓦片内存隔离转换与清单报告生成。
    """

    def __init__(
        self,
        converter: Optional[DEMDatumConverter] = None,
        max_extrapolation_distance_km: float = MAX_MDT_EXTRAPOLATION_DISTANCE_KM,
        block_size: int = 512
    ):
        self.converter = converter or DEMDatumConverter(
            max_extrapolation_distance_km=max_extrapolation_distance_km
        )
        self.max_extrapolation_distance_km = max_extrapolation_distance_km
        self.block_size = block_size

    def run_batch(
        self,
        input_dir: str,
        output_dir: str,
        max_dist_km: Optional[float] = None,
        workers: int = 1,
        resume: bool = False,
        overwrite: bool = False,
        progress_callback: Optional[Callable[[int, int, str, str, Dict[str, Any]], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> BatchConversionSummary:
        """
        执行批量 DEM 垂直基准转换工作流。

        :param input_dir: 输入 DEM 文件夹
        :param output_dir: 输出 MSL DEM 文件夹
        :param max_dist_km: 近岸 IDW 最大外推距离门禁 (默认 100km)
        :param workers: 工作线程数 (默认 1, 逐瓦片顺序执行以保障内存受控)
        :param resume: 是否开启断点恢复 (跳过已成功瓦片)
        :param overwrite: 是否允许强制覆盖既有产物
        :param progress_callback: 进度回调 (current_idx, total_tiles, current_file, status, stats_dict)
        :param cancel_event: 任务取消事件句柄
        """
        t0 = time.time()
        eff_max_dist = float(max_dist_km if max_dist_km is not None else self.max_extrapolation_distance_km)
        in_path = Path(input_dir).resolve()
        out_path = Path(output_dir).resolve()
        out_path.mkdir(parents=True, exist_ok=True)

        tiles = scan_dem_directory(str(in_path), recursive=True)
        total_tiles = len(tiles)

        manifest = BatchConversionManifest(str(out_path))

        success_count = 0
        failed_count = 0
        skipped_msl_count = 0
        skipped_resume_count = 0

        stats = {
            "total": total_tiles,
            "success": 0,
            "failed": 0,
            "skipped_msl": 0,
            "skipped_resume": 0,
            "current_file": ""
        }

        def _emit_progress(idx: int, cur_file: str, status_msg: str):
            if progress_callback:
                progress_callback(idx, total_tiles, cur_file, status_msg, stats)

        def _process_single_tile(idx: int, tile_path: str) -> Dict[str, Any]:
            nonlocal success_count, failed_count, skipped_msl_count, skipped_resume_count

            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("用户主动取消了批量 DEM 基准转换任务。")

            stats["current_file"] = os.path.basename(tile_path)
            _emit_progress(idx, tile_path, f"正在处理 ({idx + 1}/{total_tiles}): {os.path.basename(tile_path)}")

            # 构建相对输出路径以保持子目录结构
            try:
                rel_p = Path(tile_path).relative_to(in_path)
                out_tile = out_path / rel_p.parent / f"{rel_p.stem}_MSL{rel_p.suffix}"
            except ValueError:
                rel_p = Path(os.path.basename(tile_path))
                out_tile = out_path / f"{rel_p.stem}_MSL{rel_p.suffix}"

            out_tile.parent.mkdir(parents=True, exist_ok=True)
            out_tile_str = str(out_tile.resolve())

            # 1. 检查输入是否已是 MSL 基准 (防呆拦截)
            is_msl, msl_reason = is_dem_already_msl(tile_path)
            if is_msl:
                skipped_msl_count += 1
                stats["skipped_msl"] = skipped_msl_count
                manifest.upsert(
                    tile_path,
                    output_file=out_tile_str,
                    status=STATUS_SKIPPED_MSL,
                    start_time=datetime.now(timezone.utc).isoformat(),
                    end_time=datetime.now(timezone.utc).isoformat(),
                    elapsed_seconds=0.0,
                    error_message=msl_reason
                )
                manifest.save()
                _emit_progress(idx + 1, tile_path, f"已跳过 (已是 MSL): {os.path.basename(tile_path)}")
                return manifest.get_entry(tile_path)

            # 2. 检查断点恢复 (Resume)
            if resume and out_tile.exists():
                prev_status = manifest.get_status(tile_path)
                if prev_status == STATUS_SUCCESS:
                    skipped_resume_count += 1
                    stats["skipped_resume"] = skipped_resume_count
                    _emit_progress(idx + 1, tile_path, f"已跳过 (断点恢复已完成): {os.path.basename(tile_path)}")
                    return manifest.get_entry(tile_path)

            # 3. 检查非覆盖模式下的文件冲突
            if not overwrite and not resume and out_tile.exists():
                failed_count += 1
                stats["failed"] = failed_count
                err_msg = f"输出文件已存在且未开启覆盖权限: {out_tile_str}"
                manifest.upsert(
                    tile_path,
                    output_file=out_tile_str,
                    status=STATUS_FAILED,
                    start_time=datetime.now(timezone.utc).isoformat(),
                    end_time=datetime.now(timezone.utc).isoformat(),
                    elapsed_seconds=0.0,
                    error_message=err_msg
                )
                manifest.save()
                _emit_progress(idx + 1, tile_path, f"转换失败: {err_msg}")
                return manifest.get_entry(tile_path)

            # 4. 执行单个瓦片转换
            start_iso = datetime.now(timezone.utc).isoformat()
            manifest.upsert(
                tile_path,
                output_file=out_tile_str,
                status=STATUS_RUNNING,
                start_time=start_iso,
                error_message=""
            )
            manifest.save()

            try:
                # 必须逐个瓦片流式解算，内存完全隔离
                res: DEMConversionSummary = self.converter.convert_raster(
                    input_dem_path=tile_path,
                    output_msl_path=out_tile_str,
                    output_qc_path=None,
                    max_extrapolation_distance_km=eff_max_dist,
                    block_size=self.block_size,
                    allow_overwrite=True,
                    cancel_event=cancel_event
                )
                end_iso = datetime.now(timezone.utc).isoformat()

                manifest.upsert(
                    tile_path,
                    output_file=out_tile_str,
                    status=STATUS_SUCCESS,
                    start_time=start_iso,
                    end_time=end_iso,
                    elapsed_seconds=round(res.elapsed_seconds, 2),
                    total_pixels=res.total_pixels,
                    valid_pixels=res.valid_dem_pixels,
                    native_mdt_pixels=res.native_mdt_pixels,
                    idw_pixels=res.extrapolated_mdt_pixels,
                    nodata_pixels=res.nodata_pixels,
                    error_message=""
                )
                manifest.save()
                success_count += 1
                stats["success"] = success_count
                _emit_progress(idx + 1, tile_path, f"转换成功: {os.path.basename(tile_path)}")
                return manifest.get_entry(tile_path)

            except Exception as e:
                if cancel_event is not None and cancel_event.is_set():
                    raise
                end_iso = datetime.now(timezone.utc).isoformat()
                err_msg = str(e)
                failed_count += 1
                stats["failed"] = failed_count
                manifest.upsert(
                    tile_path,
                    output_file=out_tile_str,
                    status=STATUS_FAILED,
                    start_time=start_iso,
                    end_time=end_iso,
                    elapsed_seconds=0.0,
                    error_message=err_msg
                )
                manifest.save()
                _emit_progress(idx + 1, tile_path, f"瓦片失败 (已隔离跳过): {os.path.basename(tile_path)} -> {err_msg}")
                return manifest.get_entry(tile_path)

        # 调度执行
        if workers <= 1:
            for i, t_path in enumerate(tiles):
                if cancel_event is not None and cancel_event.is_set():
                    break
                _process_single_tile(i, t_path)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_process_single_tile, i, t): t for i, t in enumerate(tiles)}
                for fut in as_completed(futures):
                    if cancel_event is not None and cancel_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
                    try:
                        fut.result()
                    except Exception as e:
                        if cancel_event is not None and cancel_event.is_set():
                            break

        elapsed_total = round(time.time() - t0, 2)
        summary = BatchConversionSummary(
            input_dir=str(in_path),
            output_dir=str(out_path),
            total_tiles=total_tiles,
            success_count=success_count,
            failed_count=failed_count,
            skipped_msl_count=skipped_msl_count,
            skipped_resume_count=skipped_resume_count,
            elapsed_seconds=elapsed_total,
            manifest_csv=str(manifest.csv_path),
            manifest_json=str(manifest.json_path),
            records=list(manifest.records.values())
        )
        return summary
