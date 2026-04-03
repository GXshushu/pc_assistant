import psutil
import GPUtil
import platform
import re
import subprocess
import threading
import time
from loguru import logger

class SystemMonitor:
    def __init__(self):
        self.cpu_count = psutil.cpu_count()
        self.total_memory = psutil.virtual_memory().total
        self._cpu_name = None
        self._wmi_conn = None
        self._gpu_process_wmi_disabled = False
        self._proc_cpu_cache = {}
        self._proc_cpu_cache_lock = threading.Lock()
        # Prime the CPU usage calculation so the first get_cpu_usage call is valid
        psutil.cpu_percent(interval=None)

    @property
    def cpu_name(self):
        if self._cpu_name is None:
            self._cpu_name = self._get_cpu_name()
        return self._cpu_name

    def _get_cpu_name(self):
        if platform.system() == "Windows":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                    value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            except Exception:
                pass
            try:
                import pythoncom
                pythoncom.CoInitialize()
                try:
                    import wmi
                    c = wmi.WMI()
                    for cpu in c.Win32_Processor():
                        return cpu.Name
                finally:
                    pythoncom.CoUninitialize()
            except Exception as e:
                logger.warning(f"Could not get CPU name via wmi: {e}")
                return platform.processor()
        return platform.processor()

    def get_cpu_usage(self, interval=0.2):
        """Returns CPU usage percentage."""
        return psutil.cpu_percent(interval=interval)

    def get_memory_usage(self):
        """Returns Memory usage stats: percent, used, total."""
        mem = psutil.virtual_memory()
        return {
            "percent": mem.percent,
            "used": mem.used / (1024**3),  # GB
            "total": mem.total / (1024**3), # GB
            "model": "DDR4/5 (Unknown)" # Placeholder, specific model is hard to get reliably
        }

    def get_gpu_usage(self):
        """Returns GPU usage stats for the first available GPU."""
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu = gpus[0]
                return {
                    "name": gpu.name,
                    "load": gpu.load * 100,
                    "memory_used": gpu.memoryUsed,
                    "memory_total": gpu.memoryTotal,
                    "temperature": gpu.temperature
                }
        except Exception as e:
            logger.warning(f"Failed to get GPU usage: {e}")
        return None

    def get_process_list(self, limit=5, sort_by='cpu_percent'):
        """Returns top processes by specified metric."""
        now = time.monotonic()
        processes = []
        for proc in psutil.process_iter(['name', 'memory_info']):
            try:
                pinfo = proc.info
                # Skip System Idle Process
                if pinfo['name'] == 'System Idle Process' or proc.pid == 0:
                    continue
                
                # Convert memory to MB once
                pid = proc.pid
                name = pinfo.get('name') or f"PID {pid}"
                memory_mb = pinfo['memory_info'].rss / (1024 * 1024)

                item = {
                    "pid": pid,
                    "name": name,
                    "memory_mb": memory_mb,
                }
                if sort_by == 'cpu_percent':
                    item['cpu_percent'] = self._get_process_cpu_percent(proc, now)
                processes.append(item)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
        # Sort and return top N
        return sorted(processes, key=lambda p: p[sort_by], reverse=True)[:limit]

    def _get_process_cpu_percent(self, proc, now):
        try:
            cpu_times = proc.cpu_times()
            total_cpu_time = float(getattr(cpu_times, "user", 0.0)) + float(getattr(cpu_times, "system", 0.0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0

        with self._proc_cpu_cache_lock:
            prev = self._proc_cpu_cache.get(proc.pid)
            self._proc_cpu_cache[proc.pid] = (total_cpu_time, now)

        if not prev:
            return 0.0

        prev_cpu_time, prev_now = prev
        dt = now - prev_now
        if dt <= 0:
            return 0.0

        delta_cpu = total_cpu_time - prev_cpu_time
        if delta_cpu <= 0:
            return 0.0

        return round((delta_cpu / dt) * 100.0, 1)

    def get_gpu_process_list(self, limit=5):
        """Returns top processes by GPU usage percentage (Windows only)."""
        if platform.system() != "Windows":
            return []

        results = self._get_gpu_process_list_via_powershell(limit=limit)
        if results:
            return results

        if self._gpu_process_wmi_disabled:
            return []

        gpu_processes = {}
        try:
            import pythoncom
            pythoncom.CoInitialize()
            try:
                import wmi
                c = wmi.WMI()

                # Query GPU Engine counters
                for item in c.Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine():
                    try:
                        # Name format: pid_1234_luid_...
                        name_parts = item.Name.split('_')
                        if len(name_parts) >= 2 and name_parts[0] == 'pid':
                            pid = int(name_parts[1])
                            usage = int(item.UtilizationPercentage)
                            if usage > 0:
                                gpu_processes[pid] = gpu_processes.get(pid, 0) + usage
                    except (ValueError, IndexError):
                        continue
            finally:
                pythoncom.CoUninitialize()
            
            # Get process names for the top GPU consumers
            results = []
            sorted_pids = sorted(gpu_processes.items(), key=lambda x: x[1], reverse=True)[:limit]
            
            for pid, usage in sorted_pids:
                try:
                    proc = psutil.Process(pid)
                    results.append({
                        'name': proc.name(),
                        'gpu_percent': usage
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    results.append({
                        'name': f"Unknown (PID {pid})",
                        'gpu_percent': usage
                    })
            return results
        except Exception as e:
            logger.warning(f"Failed to get GPU process list: {e}")
            self._gpu_process_wmi_disabled = True
            return []

    def _get_gpu_process_list_via_powershell(self, limit=5):
        gpu_processes = {}
        command = r"(Get-Counter '\GPU Engine(*)\Utilization Percentage').CounterSamples | ForEach-Object { ""$($_.InstanceName)|$($_.CookedValue)"" }"
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                encoding="gbk",
                errors="ignore",
                timeout=6,
                startupinfo=startupinfo
            )
            if result.returncode != 0:
                return []

            for line in result.stdout.splitlines():
                if "|" not in line:
                    continue
                instance_name, cooked_value = line.split("|", 1)
                match = re.search(r"pid_(\d+)", instance_name)
                if not match:
                    continue
                try:
                    pid = int(match.group(1))
                    usage = float(cooked_value.strip())
                except ValueError:
                    continue
                if usage <= 0:
                    continue
                gpu_processes[pid] = gpu_processes.get(pid, 0.0) + usage

            if not gpu_processes:
                return []

            results = []
            sorted_pids = sorted(gpu_processes.items(), key=lambda x: x[1], reverse=True)[:limit]
            for pid, usage in sorted_pids:
                try:
                    proc = psutil.Process(pid)
                    results.append({
                        "name": proc.name(),
                        "gpu_percent": round(usage, 1)
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    results.append({
                        "name": f"Unknown (PID {pid})",
                        "gpu_percent": round(usage, 1)
                    })
            return results
        except Exception:
            return []

if __name__ == "__main__":
    monitor = SystemMonitor()
    print(f"CPU Name: {monitor.cpu_name}")
    print(f"CPU Usage: {monitor.get_cpu_usage()}%")
    print(f"Memory Usage: {monitor.get_memory_usage()}")
    print(f"GPU Usage: {monitor.get_gpu_usage()}")
