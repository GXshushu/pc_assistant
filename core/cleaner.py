import os
import shutil
from pathlib import Path
from loguru import logger
import send2trash
import time
from concurrent.futures import ThreadPoolExecutor

# Independent function for cleaning files to work with multiprocessing
def _clean_file(file_path):
    """Clean a single file, return (success, size)"""
    try:
        path = Path(file_path) if not isinstance(file_path, Path) else file_path
        
        # Check if file exists
        if not path.exists():
            return (True, 0)  # File doesn't exist, consider it cleaned
        
        file_size = path.stat().st_size
        
        # Try multiple approaches to clean the file
        methods = [
            ("direct delete", lambda: path.unlink(missing_ok=True)),
            ("recycle bin", lambda: send2trash.send2trash(str(path)))
        ]
        
        for method_name, method in methods:
            try:
                method()
                return (True, file_size)
            except Exception as e:
                logger.debug(f"{method_name} failed for {file_path}: {e}")
                continue
        
        # All methods failed
        logger.error(f"All methods failed to clean file {file_path}")
        return (False, 0)
    except Exception as e:
        logger.error(f"Failed to clean file {file_path}: {e}")
        return (False, 0)

class JunkCleaner:
    def __init__(self):
        self.user_profile = Path(os.environ.get("USERPROFILE", ""))
        self.system_root = Path(os.environ.get("SystemRoot", "C:\\Windows"))
        
        # 定义要扫描的临时文件扩展名
        self.temp_extensions = {
            '.tmp', '._mp', '.log', '.gid', '.chk', '.old', '.xlk', '.bak'
        }

    def _iter_existing_dirs(self, dirs):
        for d in dirs:
            try:
                if d and d.exists():
                    yield d
            except Exception:
                continue

    def _windows_common_targets(self, mode):
        targets = []
        temp_env = os.environ.get("TEMP")
        if temp_env:
            targets.append(Path(temp_env))

        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            lad = Path(local_app_data)
            targets.append(lad / "Temp")
            targets.append(lad / "CrashDumps")

            if mode == "deep":
                chrome_root = lad / "Google" / "Chrome" / "User Data"
                edge_root = lad / "Microsoft" / "Edge" / "User Data"
                for root in [chrome_root, edge_root]:
                    if not root.exists():
                        continue
                    for profile in root.iterdir():
                        if not profile.is_dir():
                            continue
                        targets.append(profile / "Cache")
                        targets.append(profile / "Code Cache")
                        targets.append(profile / "GPUCache")

        # 添加用户目录下的特殊文件夹
        if self.user_profile:
            # Cookies文件夹
            cookies_path = self.user_profile / "Cookies"
            if cookies_path.exists():
                targets.append(cookies_path)
            
            # Recent文件夹（文件使用记录）
            recent_path = self.user_profile / "Recent"
            if recent_path.exists():
                targets.append(recent_path)
            
            # IE临时文件
            ie_cache = self.user_profile / "Local Settings" / "Temporary Internet Files"
            if ie_cache.exists():
                targets.append(ie_cache)
            
            # 另一个IE临时文件路径
            if local_app_data:
                ie_cache2 = Path(local_app_data) / "Microsoft" / "Windows" / "Temporary Internet Files"
                if ie_cache2.exists():
                    targets.append(ie_cache2)

        program_data = os.environ.get("PROGRAMDATA")
        if program_data:
            pd = Path(program_data)
            targets.append(pd / "Microsoft" / "Windows" / "WER" / "ReportArchive")
            targets.append(pd / "Microsoft" / "Windows" / "WER" / "ReportQueue")

        targets.append(self.system_root / "Temp")
        targets.append(self.system_root / "Prefetch")
        targets.append(self.system_root / "Minidump")
        targets.append(self.system_root / "LiveKernelReports")

        if mode == "deep":
            targets.append(self.system_root / "SoftwareDistribution" / "Download")

        return list(self._iter_existing_dirs(targets))

    def _discover_named_dirs(self, drive_root, mode, limit=40):
        names = {"temp", "cache", "caches", "logs", "log", "crashdumps", "gpucache", "code cache"}
        ignore = {"windows", "program files", "program files (x86)", "system volume information", "$recycle.bin"}
        discovered = []

        try:
            root = Path(drive_root)
        except Exception:
            return []

        if mode != "deep":
            bases = [root]
            try:
                for child in root.iterdir():
                    if child.is_dir():
                        bases.append(child)
            except Exception:
                pass

            for base in bases:
                try:
                    for child in base.iterdir():
                        if len(discovered) >= limit:
                            break
                        if child.is_dir() and child.name.lower() in names and child.name.lower() not in ignore:
                            discovered.append(child)
                except Exception:
                    continue

            return list(self._iter_existing_dirs(discovered))

        queue = [(root, 0)]
        visited = 0
        max_depth = 3
        max_nodes = 1500

        while queue and len(discovered) < limit and visited < max_nodes:
            current, depth = queue.pop(0)
            visited += 1
            try:
                for child in current.iterdir():
                    if not child.is_dir():
                        continue
                    n = child.name.lower()
                    if n in ignore:
                        continue
                    if n in names:
                        discovered.append(child)
                        if len(discovered) >= limit:
                            break
                    if depth + 1 < max_depth:
                        queue.append((child, depth + 1))
            except Exception:
                continue

        return list(self._iter_existing_dirs(discovered))

    def _scan_directory(self, directory, drive_roots, recursive, min_age_seconds, max_files, per_drive_limit, per_drive_counts, results, total_bytes_ref, by_drive):
        try:
            if not os.access(str(directory), os.R_OK):
                return
        except Exception:
            return

        try:
            dir_drive = directory.resolve().drive.upper()
        except Exception:
            return
        if dir_drive not in drive_roots:
            return

        now = time.time()
        drive_root = drive_roots[dir_drive]

        try:
            iterator = directory.rglob("*") if recursive else directory.iterdir()
            for path in iterator:
                if len(results) >= max_files:
                    break
                if per_drive_limit is not None and per_drive_counts.get(drive_root, 0) >= per_drive_limit:
                    break
                try:
                    if not path.is_file():
                        continue
                    stat = path.stat()
                    if now - stat.st_mtime < min_age_seconds:
                        continue
                    size = stat.st_size
                    if size <= 0:
                        continue
                    results.append({"path": str(path), "size": size, "drive": drive_root})
                    total_bytes_ref[0] += size
                    by_drive[drive_root] += size
                    per_drive_counts[drive_root] = per_drive_counts.get(drive_root, 0) + 1
                except Exception:
                    continue
        except Exception as e:
            if isinstance(e, PermissionError) or getattr(e, "winerror", None) == 5:
                logger.debug(f"Skip directory {directory}: {e}")
            else:
                logger.error(f"Error scanning directory {directory}: {e}")

    def _scan_temp_files_by_extension(self, directory, drive_roots, min_age_seconds, max_files, results, total_bytes_ref, by_drive, max_depth=3):
        """扫描特定扩展名的临时文件"""
        try:
            if not os.access(str(directory), os.R_OK):
                return
        except Exception:
            return

        try:
            dir_drive = directory.resolve().drive.upper()
        except Exception:
            return
        if dir_drive not in drive_roots:
            return

        now = time.time()
        drive_root = drive_roots[dir_drive]

        # 限制扫描深度，避免扫描整个系统盘
        def scan_with_depth(path, current_depth):
            if current_depth > max_depth or len(results) >= max_files:
                return
            
            try:
                for item in path.iterdir():
                    if len(results) >= max_files:
                        return
                    
                    if item.is_file():
                        # 检查文件扩展名
                        if item.suffix.lower() in self.temp_extensions:
                            try:
                                stat = item.stat()
                                if now - stat.st_mtime < min_age_seconds:
                                    continue
                                size = stat.st_size
                                if size <= 0:
                                    continue
                                results.append({"path": str(item), "size": size, "drive": drive_root})
                                total_bytes_ref[0] += size
                                by_drive[drive_root] += size
                            except Exception:
                                continue
                    elif item.is_dir():
                        # 递归扫描子目录
                        scan_with_depth(item, current_depth + 1)
            except (PermissionError, OSError):
                pass

        try:
            scan_with_depth(directory, 0)
        except Exception as e:
            if isinstance(e, PermissionError) or getattr(e, "winerror", None) == 5:
                logger.debug(f"Skip directory {directory}: {e}")
            else:
                logger.error(f"Error scanning temp files in {directory}: {e}")

    def scan(self, drives, mode="fast", max_files=20000):
        results = []
        drive_roots = {Path(d).resolve().drive.upper(): d for d in drives}
        if not drive_roots:
            return {"files": [], "total_bytes": 0, "by_drive": {}}

        min_age_seconds = 24 * 3600 if mode == "fast" else 1 * 3600
        recursive = mode != "fast"

        by_drive = {d: 0 for d in drives}
        per_drive_counts = {d: 0 for d in drives}
        per_drive_limit = max(500, max_files // max(1, len(drives)))
        total_bytes_ref = [0]

        windows_targets = self._windows_common_targets(mode)

        targets_by_drive = {d: [] for d in drives}
        for d in drives:
            try:
                root = Path(d)
            except Exception:
                continue

            drive_specific = []
            drive_specific.append(root / "Temp")
            drive_specific.append(root / "Windows" / "Temp")
            drive_specific.append(root / "Windows" / "Prefetch")
            if mode == "deep":
                drive_specific.append(root / "Windows" / "SoftwareDistribution" / "Download")
                program_data = root / "ProgramData"
                drive_specific.append(program_data / "Microsoft" / "Windows" / "WER" / "ReportArchive")
                drive_specific.append(program_data / "Microsoft" / "Windows" / "WER" / "ReportQueue")

                users_root = root / "Users"
                if users_root.exists():
                    try:
                        for user_dir in users_root.iterdir():
                            if not user_dir.is_dir():
                                continue
                            drive_specific.append(user_dir / "AppData" / "Local" / "Temp")
                            drive_specific.append(user_dir / "AppData" / "Local" / "CrashDumps")
                    except Exception:
                        pass

            drive_specific.extend(self._discover_named_dirs(d, mode))

            drive_specific_existing = list(self._iter_existing_dirs(drive_specific))

            filtered_windows_targets = []
            for wt in windows_targets:
                try:
                    if wt.resolve().drive.upper() == Path(d).resolve().drive.upper():
                        filtered_windows_targets.append(wt)
                except Exception:
                    continue

            targets_by_drive[d] = [*drive_specific_existing, *filtered_windows_targets]

        active = True
        while active and len(results) < max_files:
            active = False
            for drive_root in drives:
                if len(results) >= max_files:
                    break
                if per_drive_counts.get(drive_root, 0) >= per_drive_limit:
                    continue
                dirs = targets_by_drive.get(drive_root, [])
                if not dirs:
                    continue
                active = True
                directory = dirs.pop(0)
                self._scan_directory(
                    directory=directory,
                    drive_roots=drive_roots,
                    recursive=recursive,
                    min_age_seconds=min_age_seconds,
                    max_files=max_files,
                    per_drive_limit=per_drive_limit,
                    per_drive_counts=per_drive_counts,
                    results=results,
                    total_bytes_ref=total_bytes_ref,
                    by_drive=by_drive,
                )

        # 扫描特定扩展名的临时文件（仅在深度模式下）
        if mode == "deep" and len(results) < max_files:
            # 扫描系统盘下的临时文件
            system_drive = os.environ.get("SystemDrive", "C:")
            system_drive_path = Path(system_drive)
            if system_drive_path.exists():
                self._scan_temp_files_by_extension(
                    directory=system_drive_path,
                    drive_roots=drive_roots,
                    min_age_seconds=min_age_seconds,
                    max_files=max_files,
                    results=results,
                    total_bytes_ref=total_bytes_ref,
                    by_drive=by_drive,
                )
            
            # 扫描用户目录下的临时文件
            if self.user_profile and self.user_profile.exists():
                self._scan_temp_files_by_extension(
                    directory=self.user_profile,
                    drive_roots=drive_roots,
                    min_age_seconds=min_age_seconds,
                    max_files=max_files,
                    results=results,
                    total_bytes_ref=total_bytes_ref,
                    by_drive=by_drive,
                )

        results.sort(key=lambda x: x["size"], reverse=True)
        return {"files": results, "total_bytes": total_bytes_ref[0], "by_drive": by_drive}

    def clean_junk_files(self, files_to_clean):
        """Cleans specified junk files using threading.

        Priority: permanent delete first (actually frees disk space), fallback to recycle bin.
        """
        if not files_to_clean:
            return {"count": 0, "size": 0.0}
        
        # Use threading to clean files concurrently (better for I/O-bound tasks)
        cleaned_count = 0
        cleaned_size = 0
        
        # Determine the number of threads to use
        num_threads = min(8, len(files_to_clean))  # Use up to 8 threads
        
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            # Map the clean_file function to all files
            results = list(executor.map(_clean_file, files_to_clean))
            
            # Process the results
            for success, size in results:
                if success:
                    cleaned_count += 1
                    cleaned_size += size
        
        return {
            "count": cleaned_count,
            "size": cleaned_size / (1024**2) # MB
        }

if __name__ == "__main__":
    cleaner = JunkCleaner()
    drive = Path(os.environ.get("SystemDrive", "C:")).drive + "\\"
    r = cleaner.scan(drives=[drive], mode="fast")
    print(r["total_bytes"], len(r["files"]))
