import sys
import os
import shutil
from pathlib import Path

import psutil
from PySide6.QtCore import Qt, QSize, QTimer, QThread, Signal, QObject, QPointF, QThreadPool, QRunnable
from PySide6.QtGui import QIcon, QPainter, QFont, QAction, QColor
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QGridLayout, QPushButton, QSystemTrayIcon, QMenu, QDial, QCheckBox, QRadioButton, QButtonGroup, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis, QDateTimeAxis, QPieSeries
from datetime import datetime

from qfluentwidgets import (FluentWindow, SubtitleLabel, setTheme, Theme, 
                            FluentIcon as FIF, NavigationItemPosition, 
                            ProgressBar, BodyLabel, CardWidget, StrongBodyLabel, 
                            TransparentToolButton, ExpandLayout, SwitchButton)

from core.monitor import SystemMonitor
from core.data_logger import DataLogger
from core.cleaner import JunkCleaner


class _FetchSignals(QObject):
    result = Signal(str, object)


class _FetchTask(QRunnable):
    def __init__(self, name, func):
        super().__init__()
        self.name = name
        self.func = func
        self.signals = _FetchSignals()

    def run(self):
        try:
            value = self.func()
        except Exception:
            value = None
        self.signals.result.emit(self.name, value)


class MonitorCoordinator(QObject):
    stats_updated = Signal(dict)

    def __init__(self, monitor, logger, parent=None):
        super().__init__(parent)
        self.monitor = monitor
        self.logger = logger
        self.pool = QThreadPool.globalInstance()

        self._closing = False
        self._is_background = False
        self._inflight = set()

        self._latest = {
            "cpu_usage": 0.0,
            "cpu_name": None,
            "mem_usage": None,
            "gpu_usage": None,
            "gpu_temp": None,
            "gpu_initialized": False,
            "top_cpu_processes": [],
            "top_mem_processes": [],
            "top_gpu_processes": [],
        }

        self._cpu_mem_timer = QTimer(self)
        self._cpu_mem_timer.timeout.connect(self._request_cpu_mem)

        self._gpu_timer = QTimer(self)
        self._gpu_timer.timeout.connect(self._request_gpu_usage)

        self._proc_timer = QTimer(self)
        self._proc_timer.timeout.connect(self._request_proc_lists)

        self._gpu_proc_timer = QTimer(self)
        self._gpu_proc_timer.timeout.connect(self._request_gpu_proc_list)

        self.set_background_mode(False)

        self._request_cpu_name()
        self._request_cpu_mem()
        self._request_gpu_usage()

    def shutdown(self):
        self._closing = True
        self._cpu_mem_timer.stop()
        self._gpu_timer.stop()
        self._proc_timer.stop()
        self._gpu_proc_timer.stop()
        
        # Wait for all tasks to complete
        self.pool.waitForDone(5000)  # Wait up to 5 seconds

    def set_background_mode(self, enabled):
        self._is_background = enabled

        cpu_mem_interval = 30000 if enabled else 1000
        gpu_interval = 30000 if enabled else 2000
        heavy_interval = 60000 if enabled else 5000

        self._cpu_mem_timer.setInterval(cpu_mem_interval)
        self._gpu_timer.setInterval(gpu_interval)
        self._proc_timer.setInterval(heavy_interval)
        self._gpu_proc_timer.setInterval(heavy_interval)

        if not self._cpu_mem_timer.isActive():
            self._cpu_mem_timer.start()
        if not self._gpu_timer.isActive():
            self._gpu_timer.start()
        if not self._proc_timer.isActive():
            self._proc_timer.start()
        if not self._gpu_proc_timer.isActive():
            self._gpu_proc_timer.start()

        if not enabled:
            self._request_cpu_mem()
            self._request_gpu_usage()

    def _submit(self, name, func):
        if self._closing:
            return
        if name in self._inflight:
            return
        self._inflight.add(name)
        task = _FetchTask(name, func)
        task.signals.result.connect(self._on_result)
        self.pool.start(task)

    def _request_cpu_name(self):
        self._submit("cpu_name", lambda: self.monitor.cpu_name)

    def _request_cpu_mem(self):
        def fetch():
            return {
                "cpu_usage": self.monitor.get_cpu_usage(),
                "mem_usage": self.monitor.get_memory_usage(),
            }

        self._submit("cpu_mem", fetch)

    def _request_gpu_usage(self):
        self._submit("gpu_usage", self.monitor.get_gpu_usage)

    def _request_proc_lists(self):
        def fetch():
            return {
                "top_cpu_processes": self.monitor.get_process_list(limit=5, sort_by="cpu_percent"),
                "top_mem_processes": self.monitor.get_process_list(limit=5, sort_by="memory_mb"),
            }

        self._submit("proc_lists", fetch)

    def _request_gpu_proc_list(self):
        self._submit("gpu_proc_list", lambda: self.monitor.get_gpu_process_list(limit=5))

    def _on_result(self, name, value):
        if self._closing:
            return
        self._inflight.discard(name)

        payload = {}

        if name == "cpu_name":
            if isinstance(value, str) and value.strip():
                self._latest["cpu_name"] = value.strip()
                payload["cpu_name"] = self._latest["cpu_name"]

        elif name == "cpu_mem":
            if isinstance(value, dict):
                if "cpu_usage" in value:
                    self._latest["cpu_usage"] = value["cpu_usage"]
                    payload["cpu_usage"] = value["cpu_usage"]
                if "mem_usage" in value:
                    self._latest["mem_usage"] = value["mem_usage"]
                    payload["mem_usage"] = value["mem_usage"]

                gpu_load = None
                if isinstance(self._latest.get("gpu_usage"), dict):
                    gpu_load = self._latest["gpu_usage"].get("load")
                gpu_temp = self._latest.get("gpu_temp")
                try:
                    if self._latest.get("mem_usage") is not None:
                        self.logger.log(self._latest["cpu_usage"], self._latest["mem_usage"]["percent"], gpu_load, gpu_temp)
                except Exception:
                    pass

        elif name == "gpu_usage":
            self._latest["gpu_usage"] = value
            payload["gpu_usage"] = value
            if isinstance(value, dict) and value.get("temperature") is not None:
                self._latest["gpu_temp"] = value.get("temperature")
                payload["gpu_temp"] = self._latest["gpu_temp"]
            if not self._latest["gpu_initialized"]:
                self._latest["gpu_initialized"] = True
                payload["gpu_initialized"] = True

        elif name == "proc_lists":
            if isinstance(value, dict):
                self._latest["top_cpu_processes"] = value.get("top_cpu_processes") or []
                self._latest["top_mem_processes"] = value.get("top_mem_processes") or []
                payload["top_cpu_processes"] = self._latest["top_cpu_processes"]
                payload["top_mem_processes"] = self._latest["top_mem_processes"]
                payload["new_heavy_data"] = True

        elif name == "gpu_proc_list":
            self._latest["top_gpu_processes"] = value or []
            payload["top_gpu_processes"] = self._latest["top_gpu_processes"]
            payload["new_heavy_data"] = True

        if payload:
            if "gpu_initialized" not in payload:
                payload["gpu_initialized"] = self._latest["gpu_initialized"]
            self.stats_updated.emit(payload)

class HistoryChart(QChartView):
    def __init__(self, title, color, parent=None, y_max=100, y_label_format="%d%%"):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing)
        self._last_timestamp_ms = None
        
        self.chart = QChart()
        self.chart.setTitle(title)
        # Use a safe font and size
        title_font = QFont("Microsoft YaHei", 10, QFont.Bold)
        self.chart.setTitleFont(title_font)
        self.chart.legend().hide()
        self.chart.setAnimationOptions(QChart.NoAnimation)
        
        self.series = QLineSeries()
        self.series.setColor(color)
        self.chart.addSeries(self.series)
        
        self.axis_x = QDateTimeAxis()
        self.axis_x.setFormat("hh:mm:ss")
        self.axis_x.setTitleText("时间")
        axis_font = QFont()
        axis_font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "Arial"])
        axis_font.setPointSize(8)
        self.axis_x.setLabelsFont(axis_font)
        self.chart.addAxis(self.axis_x, Qt.AlignBottom)
        self.series.attachAxis(self.axis_x)
        
        self.axis_y = QValueAxis()
        self.axis_y.setRange(0, y_max)
        self.axis_y.setLabelFormat(y_label_format)
        self.axis_y.setLabelsFont(axis_font)
        self.chart.addAxis(self.axis_y, Qt.AlignLeft)
        self.series.attachAxis(self.axis_y)
        
        self.setChart(self.chart)
        self.setMinimumHeight(300)

    def update_data(self, timestamps, values):
        points = []
        for ts, v in zip(timestamps, values):
            if v is not None:
                points.append(QPointF(ts.timestamp() * 1000, v))
        self.series.replace(points)
        
        if timestamps:
            self.axis_x.setRange(timestamps[0], timestamps[-1])
            self._last_timestamp_ms = int(timestamps[-1].timestamp() * 1000)
        else:
            self._last_timestamp_ms = None

    def append_point(self, timestamp, value):
        if value is not None:
            timestamp_ms = int(timestamp.timestamp() * 1000)
            if self._last_timestamp_ms is not None and timestamp_ms <= self._last_timestamp_ms:
                return
            self.series.append(timestamp_ms, value)
            self._last_timestamp_ms = timestamp_ms
            if self.series.count() > 1:
                first_point = self.series.at(0)
                self.axis_x.setRange(
                    datetime.fromtimestamp(first_point.x() / 1000),
                    datetime.fromtimestamp(timestamp_ms / 1000),
                )

class ChartWindow(QWidget):
    def __init__(self, title, color, parent=None, y_max=100, y_label_format="%d%%"):
        super().__init__(None)
        self.setWindowTitle(title)
        self.resize(800, 500)
        self.setAttribute(Qt.WA_DeleteOnClose) # Cleanup on close
        self.layout = QVBoxLayout(self)
        self.chart_view = HistoryChart(title, color, self, y_max=y_max, y_label_format=y_label_format)
        self.layout.addWidget(self.chart_view)

    def update_full_data(self, timestamps, values):
        self.chart_view.update_data(timestamps, values)

    def append_data_point(self, timestamp, value):
        self.chart_view.append_point(timestamp, value)

class MonitorCard(CardWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent=parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        
        # Header with toggle
        self.header_layout = QHBoxLayout()
        self.title_label = StrongBodyLabel(title, self)
        self.history_btn = TransparentToolButton(FIF.HISTORY, self)
        self.history_btn.setToolTip("查看历史记录")
        self.history_btn.clicked.connect(self.show_history_window)
        
        self.header_layout.addWidget(self.title_label)
        self.header_layout.addStretch(1)
        self.header_layout.addWidget(self.history_btn)
        
        self.model_label = BodyLabel("", self)
        self.progress_bar = ProgressBar(self)
        self.value_label = BodyLabel("0%", self)
        
        # Ranking
        self.ranking_title = StrongBodyLabel("占用排行:", self)
        self.ranking_title.hide()
        self.ranking_label = BodyLabel("", self)
        self.ranking_label.setStyleSheet("color: grey; font-size: 12px;")
        self.ranking_label.setMinimumHeight(100) # Fixed height to prevent layout shifts
        self.ranking_label.setAlignment(Qt.AlignTop)
        
        self.layout.addLayout(self.header_layout)
        self.layout.addWidget(self.model_label)
        self.layout.addWidget(self.progress_bar)
        self.layout.addWidget(self.value_label)
        self.layout.addSpacing(10)
        self.layout.addWidget(self.ranking_title)
        self.layout.addWidget(self.ranking_label)

        self.chart_color = Qt.blue if "CPU" in title else (Qt.green if "内存" in title else Qt.red)
        self.data_key = "cpu" if "CPU" in title else ("mem" if "内存" in title else "gpu")
        self.chart_window = None

    def show_history_window(self):
        # Check if window exists and is not deleted
        try:
            if self.chart_window and self.chart_window.isVisible():
                self.chart_window.raise_()
                self.chart_window.activateWindow()
                return
        except RuntimeError:
            self.chart_window = None

        if not self.chart_window:
            self.chart_window = ChartWindow(f"{self.title_label.text()} 历史记录", self.chart_color)
            # Load all historical data at once
            all_data = self.parent().data_logger.get_all_data()
            timestamps = all_data['timestamp']
            values = all_data[self.data_key]
            self.chart_window.update_full_data(timestamps, values)
            
        self.chart_window.show()
        self.chart_window.raise_()
        self.chart_window.activateWindow()


class TemperatureCard(CardWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)

        self.header_layout = QHBoxLayout()
        self.title_label = StrongBodyLabel("温度", self)
        self.history_btn = TransparentToolButton(FIF.HISTORY, self)
        self.history_btn.setToolTip("查看温度曲线")
        self.history_btn.clicked.connect(self.show_history_window)

        self.header_layout.addWidget(self.title_label)
        self.header_layout.addStretch(1)
        self.header_layout.addWidget(self.history_btn)

        self.subtitle_label = BodyLabel("GPU 温度", self)
        self.dial = QDial(self)
        self.dial.setRange(0, 110)
        self.dial.setNotchesVisible(True)
        self.dial.setEnabled(False)

        self.value_label = StrongBodyLabel("检测中...", self)

        self.layout.addLayout(self.header_layout)
        self.layout.addWidget(self.subtitle_label)
        self.layout.addWidget(self.dial, alignment=Qt.AlignCenter)
        self.layout.addWidget(self.value_label, alignment=Qt.AlignCenter)

        self.chart_window = None

    def show_history_window(self):
        try:
            if self.chart_window and self.chart_window.isVisible():
                self.chart_window.raise_()
                self.chart_window.activateWindow()
                return
        except RuntimeError:
            self.chart_window = None

        if not self.chart_window:
            self.chart_window = ChartWindow("温度历史记录", Qt.red, y_max=130, y_label_format="")
            all_data = self.parent().data_logger.get_all_data()
            self.chart_window.update_full_data(all_data["timestamp"], all_data["temp"])

        self.chart_window.show()
        self.chart_window.raise_()
        self.chart_window.activateWindow()

class SystemMonitorPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("monitorPage")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(30, 30, 30, 30)
        self.layout.setSpacing(20)

        self.title_label = SubtitleLabel("系统状态监控", self)
        self.layout.addWidget(self.title_label)

        # 监控卡片容器
        self.cards_layout = QGridLayout()
        self.layout.addLayout(self.cards_layout)

        self.cpu_card = MonitorCard("CPU 使用率", self)
        self.cpu_card.ranking_title.show()
        self.cpu_card.value_label.setText("加载中...")
        self.cpu_card.model_label.setText("型号: 获取中...")
        self.cards_layout.addWidget(self.cpu_card, 0, 0)

        self.mem_card = MonitorCard("内存使用率", self)
        self.mem_card.ranking_title.show()
        self.mem_card.value_label.setText("加载中...")
        self.cards_layout.addWidget(self.mem_card, 0, 1)

        # GPU 卡片
        self.gpu_card = MonitorCard("GPU 使用率", self)
        self.gpu_card.value_label.setText("正在检测 GPU...")
        self.gpu_card.ranking_title.show() # Show ranking for GPU
        self.cards_layout.addWidget(self.gpu_card, 1, 0)

        self.temp_card = TemperatureCard(self)
        self.cards_layout.addWidget(self.temp_card, 1, 1)

        self.layout.addStretch(1)

        self._stats_cache = {
            "cpu_usage": 0.0,
            "cpu_name": None,
            "mem_usage": None,
            "gpu_usage": None,
            "gpu_temp": None,
            "gpu_initialized": False,
            "new_heavy_data": False,
            "top_cpu_processes": [],
            "top_mem_processes": [],
            "top_gpu_processes": [],
        }

        self.monitor = SystemMonitor()
        self.data_logger = DataLogger()
        self.coordinator = MonitorCoordinator(self.monitor, self.data_logger, self)
        self.coordinator.stats_updated.connect(self.update_stats)

    def update_stats(self, stats):
        self._stats_cache.update(stats)
        stats = self._stats_cache

        # Update cards (Quick update: CPU and Memory)
        cpu_usage = stats.get("cpu_usage")
        if cpu_usage is not None:
            self.cpu_card.progress_bar.setValue(int(cpu_usage))
            self.cpu_card.value_label.setText(f"{cpu_usage}%")
        cpu_name = stats.get("cpu_name")
        if cpu_name:
            self.cpu_card.model_label.setText(f"型号: {cpu_name}")
        else:
            self.cpu_card.model_label.setText("型号: 获取中...")
        
        mem_usage = stats.get("mem_usage")
        if mem_usage:
            self.mem_card.progress_bar.setValue(int(mem_usage["percent"]))
            self.mem_card.value_label.setText(
                f"{mem_usage['percent']}% ({mem_usage['used']:.1f} GB / {mem_usage['total']:.1f} GB)"
            )
        
        # GPU detection state handling
        gpu_usage = stats.get("gpu_usage")
        if gpu_usage:
            self.gpu_card.progress_bar.setValue(int(gpu_usage['load']))
            self.gpu_card.value_label.setText(f"{gpu_usage['load']:.1f}% ({gpu_usage['memory_used']} MB / {gpu_usage['memory_total']} MB)")
            self.gpu_card.model_label.setText(f"型号: {gpu_usage['name']}")
        elif not stats.get("gpu_initialized", False):
            self.gpu_card.value_label.setText("正在检测 GPU...")
        else:
            self.gpu_card.progress_bar.setValue(0)
            self.gpu_card.value_label.setText("未检测到 GPU")
            self.gpu_card.model_label.setText("")

        gpu_temp = stats.get("gpu_temp")
        if isinstance(gpu_temp, (int, float)) and gpu_temp > 0:
            self.temp_card.dial.setValue(int(gpu_temp))
            self.temp_card.value_label.setText(f"{gpu_temp:.0f}°C")
        else:
            if not stats.get("gpu_initialized", False):
                self.temp_card.value_label.setText("检测中...")
            else:
                self.temp_card.value_label.setText("暂无温度数据")

        # Update rankings only if new heavy data arrived (Throttling)
        if stats.get("new_heavy_data", False):
            cpu_ranking = "\n".join([f"{p['name']}: {p['cpu_percent']}%" for p in (stats.get("top_cpu_processes") or [])])
            self.cpu_card.ranking_label.setText(cpu_ranking)
            mem_ranking = "\n".join([f"{p['name']}: {p['memory_mb']:.1f} MB" for p in (stats.get("top_mem_processes") or [])])
            self.mem_card.ranking_label.setText(mem_ranking)
            
            # GPU Ranking update
            if stats.get("top_gpu_processes"):
                gpu_ranking = "\n".join([f"{p['name']}: {p['gpu_percent']}%" for p in (stats.get("top_gpu_processes") or [])])
                self.gpu_card.ranking_label.setText(gpu_ranking)
            else:
                self.gpu_card.ranking_label.setText("暂无进程数据")

        # Append data to visible charts
        now = datetime.now()
        for card in [self.cpu_card, self.mem_card, self.gpu_card]:
            try:
                if card.chart_window and card.chart_window.isVisible():
                    val = cpu_usage if card == self.cpu_card else \
                          (mem_usage['percent'] if (card == self.mem_card and mem_usage) else \
                          (gpu_usage['load'] if (card == self.gpu_card and gpu_usage) else None))
                    card.chart_window.append_data_point(now, val)
            except RuntimeError:
                card.chart_window = None

    def set_background_mode(self, enabled):
        if hasattr(self, "coordinator"):
            self.coordinator.set_background_mode(enabled)

    def shutdown(self):
        if hasattr(self, "coordinator"):
            self.coordinator.shutdown()
        if hasattr(self, "data_logger"):
            self.data_logger.close()
        if hasattr(self, "temp_card") and self.temp_card.chart_window:
            try:
                self.temp_card.chart_window.close()
            except RuntimeError:
                pass

def _format_bytes(byte_count):
    value = float(byte_count)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.2f} {units[unit_index]}"


class CleanupProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_percent = 0
        self.cleanup_percent = 0
        self.setMinimumHeight(20)
    
    def set_values(self, current_percent, cleanup_percent):
        self.current_percent = current_percent
        self.cleanup_percent = cleanup_percent
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw background
        rect = self.rect()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(240, 240, 240))
        painter.drawRoundedRect(rect, 4, 4)
        
        # Draw current usage (red)
        current_width = int(rect.width() * self.current_percent / 100)
        current_rect = rect.adjusted(0, 0, current_width - rect.width(), 0)
        painter.setBrush(QColor(255, 71, 87))  # Red
        painter.drawRoundedRect(current_rect, 4, 4)
        
        # Draw cleanup potential (green)
        if self.cleanup_percent < self.current_percent:
            cleanup_width = int(rect.width() * (self.current_percent - self.cleanup_percent) / 100)
            cleanup_start = int(rect.width() * self.cleanup_percent / 100)
            cleanup_rect = rect.adjusted(cleanup_start, 0, cleanup_width - (rect.width() - cleanup_start), 0)
            painter.setBrush(QColor(76, 175, 80))  # Green
            painter.drawRoundedRect(cleanup_rect, 4, 4)


class _CleanerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class _CleanerTask(QRunnable):
    def __init__(self, func):
        super().__init__()
        self.func = func
        self.signals = _CleanerSignals()

    def run(self):
        try:
            self.signals.finished.emit(self.func())
        except Exception as e:
            self.signals.failed.emit(str(e))


class JunkCleanerPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("cleanerPage")
        self.cleaner = JunkCleaner()
        self.pool = QThreadPool.globalInstance()

        self.scan_result = None
        self.selected_drives = []

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(30, 30, 30, 30)
        self.layout.setSpacing(16)

        self.title_label = SubtitleLabel("垃圾清理", self)
        self.layout.addWidget(self.title_label)

        self.drive_card = CardWidget(self)
        self.drive_layout = QVBoxLayout(self.drive_card)
        self.drive_layout.setContentsMargins(15, 15, 15, 15)
        self.drive_layout.setSpacing(10)
        self.drive_card_title = StrongBodyLabel("磁盘空间", self.drive_card)
        self.drive_layout.addWidget(self.drive_card_title)
        self.drive_list_container = QWidget(self.drive_card)
        self.drive_list_layout = QVBoxLayout(self.drive_list_container)
        self.drive_list_layout.setContentsMargins(0, 0, 0, 0)
        self.drive_list_layout.setSpacing(8)
        self.drive_layout.addWidget(self.drive_list_container)
        self.layout.addWidget(self.drive_card)

        self.controls_card = CardWidget(self)
        self.controls_layout = QHBoxLayout(self.controls_card)
        self.controls_layout.setContentsMargins(15, 15, 15, 15)
        self.controls_layout.setSpacing(12)

        self.fast_radio = QRadioButton("极速清理扫描", self.controls_card)
        self.deep_radio = QRadioButton("深度清理扫描", self.controls_card)
        self.mode_group = QButtonGroup(self.controls_card)
        self.mode_group.addButton(self.fast_radio)
        self.mode_group.addButton(self.deep_radio)
        self.fast_radio.setChecked(True)

        self.scan_button = QPushButton("扫描", self.controls_card)
        self.clean_button = QPushButton("清理", self.controls_card)
        self.clean_button.setEnabled(False)

        self.controls_layout.addWidget(self.fast_radio)
        self.controls_layout.addWidget(self.deep_radio)
        self.controls_layout.addStretch(1)
        self.controls_layout.addWidget(self.scan_button)
        self.controls_layout.addWidget(self.clean_button)
        self.layout.addWidget(self.controls_card)

        self.status_label = BodyLabel("请选择磁盘并点击扫描", self)
        self.layout.addWidget(self.status_label)

        self.files_table = QTableWidget(self)
        self.files_table.setColumnCount(3)
        self.files_table.setHorizontalHeaderLabels(["清理", "文件", "大小"])
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.files_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.files_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.files_table.setMinimumHeight(220)
        self.layout.addWidget(self.files_table)

        self.scan_button.clicked.connect(self.start_scan)
        self.clean_button.clicked.connect(self.start_clean)

        self.drive_rows = {}
        self.refresh_drives()

    def refresh_drives(self):
        while self.drive_list_layout.count():
            item = self.drive_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.drive_rows = {}

        partitions = []
        try:
            partitions = psutil.disk_partitions(all=False)
        except Exception:
            partitions = []

        seen = set()
        for p in partitions:
            mountpoint = p.mountpoint
            if not mountpoint:
                continue
            drive = mountpoint.rstrip("\\/") + "\\"
            drive_letter = drive[:2].upper()
            if drive_letter in seen:
                continue
            seen.add(drive_letter)
            try:
                usage = shutil.disk_usage(drive)
            except Exception:
                continue

            row = QWidget(self.drive_list_container)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)

            checkbox = QCheckBox(drive_letter, row)
            info_label = BodyLabel(f"{_format_bytes(usage.used)} / {_format_bytes(usage.total)}", row)
            bar = CleanupProgressBar(row)
            used_percent = int(round((usage.used / usage.total) * 100)) if usage.total else 0
            bar.set_values(used_percent, used_percent)  # No cleanup initially

            row_layout.addWidget(checkbox)
            row_layout.addWidget(info_label)
            row_layout.addWidget(bar, 1)

            self.drive_list_layout.addWidget(row)
            self.drive_rows[drive_letter] = {"checkbox": checkbox, "info": info_label, "bar": bar, "root": drive, "usage": usage}

    def _get_selected_drives(self):
        drives = []
        for drive_letter, row in self.drive_rows.items():
            if row["checkbox"].isChecked():
                drives.append(row["root"])
        return drives

    def start_scan(self):
        drives = self._get_selected_drives()
        if not drives:
            QMessageBox.information(self, "提示", "请先选择至少一个磁盘。")
            return
        self.selected_drives = drives

        mode = "fast" if self.fast_radio.isChecked() else "deep"
        self.status_label.setText("扫描中...")
        self.scan_button.setEnabled(False)
        self.clean_button.setEnabled(False)
        self.files_table.setRowCount(0)
        self.scan_result = None

        task = _CleanerTask(lambda: self.cleaner.scan(drives=drives, mode=mode))
        task.signals.finished.connect(self._on_scan_finished)
        task.signals.failed.connect(self._on_task_failed)
        self.pool.start(task)

    def _on_scan_finished(self, result):
        self.scan_button.setEnabled(True)
        self.scan_result = result or {"files": [], "total_bytes": 0, "by_drive": {}}
        files = self.scan_result.get("files") or []
        total_bytes = int(self.scan_result.get("total_bytes") or 0)

        self.status_label.setText(f"扫描完成：可清理 {len(files)} 个文件，约 {_format_bytes(total_bytes)}")
        self.clean_button.setEnabled(len(files) > 0)

        self._update_chart()
        self._populate_table(files[:200])

    def _populate_table(self, files):
        self.files_table.setRowCount(len(files))
        for row_index, item in enumerate(files):
            check_item = QTableWidgetItem("")
            check_item.setFlags(check_item.flags() | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Checked)
            self.files_table.setItem(row_index, 0, check_item)

            path_item = QTableWidgetItem(item["path"])
            size_item = QTableWidgetItem(_format_bytes(item["size"]))
            self.files_table.setItem(row_index, 1, path_item)
            self.files_table.setItem(row_index, 2, size_item)

    def _update_chart(self):
        by_drive = (self.scan_result or {}).get("by_drive") or {}

        for drive_root in self.selected_drives:
            drive_letter = Path(drive_root).drive.upper()
            if drive_letter in self.drive_rows:
                try:
                    usage = self.drive_rows[drive_letter].get("usage") or shutil.disk_usage(drive_root)
                    used_percent_before = (usage.used / usage.total) * 100 if usage.total else 0
                    reclaim = float(by_drive.get(drive_root, 0))
                    used_after = max(0.0, usage.used - reclaim)
                    used_percent_after = (used_after / usage.total) * 100 if usage.total else 0
                    
                    # Update the progress bar with cleanup information
                    bar = self.drive_rows[drive_letter].get("bar")
                    if bar:
                        bar.set_values(int(round(used_percent_before)), int(round(used_percent_after)))
                except Exception:
                    pass

    def start_clean(self):
        if not self.scan_result:
            return
        files = self.scan_result.get("files") or []
        if not files:
            return

        selected_paths = []
        max_rows = self.files_table.rowCount()
        selected_set = set()
        for i in range(max_rows):
            item = self.files_table.item(i, 0)
            if item and item.checkState() == Qt.Checked:
                path_item = self.files_table.item(i, 1)
                if path_item:
                    selected_set.add(path_item.text())

        for f in files:
            if f["path"] in selected_set:
                selected_paths.append(f["path"])

        if not selected_paths:
            QMessageBox.information(self, "提示", "请勾选要清理的文件。")
            return

        self.status_label.setText("清理中...")
        self.scan_button.setEnabled(False)
        self.clean_button.setEnabled(False)

        task = _CleanerTask(lambda: self.cleaner.clean_junk_files(selected_paths))
        task.signals.finished.connect(self._on_clean_finished)
        task.signals.failed.connect(self._on_task_failed)
        self.pool.start(task)

    def _on_clean_finished(self, result):
        self.scan_button.setEnabled(True)
        self.clean_button.setEnabled(False)
        self.files_table.setRowCount(0)
        self.scan_result = None
        cleaned_count = (result or {}).get("count", 0)
        cleaned_size = (result or {}).get("size", 0)
        self.status_label.setText(f"清理完成：已处理 {cleaned_count} 个文件，约 {cleaned_size:.2f} MB")
        self.refresh_drives()

    def _on_task_failed(self, message):
        self.scan_button.setEnabled(True)
        self.clean_button.setEnabled(False)
        self.status_label.setText("操作失败")
        QMessageBox.warning(self, "错误", message)

class AIAssistantPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("aiPage")
        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignCenter)
        self.label = SubtitleLabel("AI 助手界面", self)
        self.layout.addWidget(self.label)

class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("settingsPage")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(30, 30, 30, 30)
        self.layout.setSpacing(16)
        
        self.title_label = SubtitleLabel("设置", self)
        self.layout.addWidget(self.title_label)
        
        # 开机自启动设置
        self.autostart_card = CardWidget(self)
        autostart_layout = QHBoxLayout(self.autostart_card)
        autostart_layout.setContentsMargins(15, 15, 15, 15)
        autostart_layout.setSpacing(12)
        
        autostart_label = StrongBodyLabel("开机自启动", self.autostart_card)
        autostart_desc = BodyLabel("程序将在Windows启动时自动运行", self.autostart_card)
        autostart_desc.setStyleSheet("color: gray;")
        
        self.autostart_switch = SwitchButton(self.autostart_card)
        self.autostart_switch.setChecked(self._is_autostart_enabled())
        self.autostart_switch.checkedChanged.connect(self._toggle_autostart)
        
        autostart_text_layout = QVBoxLayout()
        autostart_text_layout.addWidget(autostart_label)
        autostart_text_layout.addWidget(autostart_desc)
        
        autostart_layout.addLayout(autostart_text_layout)
        autostart_layout.addStretch(1)
        autostart_layout.addWidget(self.autostart_switch)
        
        self.layout.addWidget(self.autostart_card)
        self.layout.addStretch(1)
    
    def _is_autostart_enabled(self):
        """检查是否已启用开机自启动"""
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                                 r"Software\Microsoft\Windows\CurrentVersion\Run", 
                                 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, "personal_pc_assistant")
                winreg.CloseKey(key)
                return True
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except Exception:
            return False
    
    def _toggle_autostart(self, checked):
        """切换开机自启动状态"""
        try:
            import winreg
            
            # 先检查当前状态，避免重复操作
            current_state = self._is_autostart_enabled()
            if current_state == checked:
                # 状态已经一致，无需操作
                return
            
            # 尝试打开注册表项
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 
                                     r"Software\Microsoft\Windows\CurrentVersion\Run", 
                                     0, winreg.KEY_WRITE)
            except PermissionError:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "权限不足", "无法修改注册表，请以管理员身份运行程序。")
                self.autostart_switch.blockSignals(True)
                self.autostart_switch.setChecked(not checked)
                self.autostart_switch.blockSignals(False)
                return
            
            if checked:
                # 添加开机自启动
                import sys
                exe_path = sys.executable if getattr(sys, 'frozen', False) else __file__
                winreg.SetValueEx(key, "PC助手", 0, winreg.REG_SZ, f'"{exe_path}"')
            else:
                # 移除开机自启动
                try:
                    winreg.DeleteValue(key, "PC助手")
                except FileNotFoundError:
                    pass
            
            winreg.CloseKey(key)
            
                
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "错误", f"设置开机自启动失败：{e}")
            # 恢复开关状态
            self.autostart_switch.blockSignals(True)
            self.autostart_switch.setChecked(not checked)
            self.autostart_switch.blockSignals(False)

class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        # 设置标题和窗口大小
        self.setWindowTitle("个人 PC 助手")
        # self.setWindowIcon(QIcon("resources/logo.png")) 
        self.resize(1000, 700)

        self._is_quitting = False

        # 创建子页面
        self.monitor_page = SystemMonitorPage(self)
        self.cleaner_page = JunkCleanerPage(self)
        self.ai_page = AIAssistantPage(self)
        self.settings_page = SettingsPage(self)

        # 初始化导航
        self.init_navigation()
        
        # 初始化系统托盘
        self.init_tray()

    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        # 使用监控图标作为托盘图标
        self.tray_icon.setIcon(FIF.IOT.icon())
        
        # 创建菜单
        self.tray_menu = QMenu()
        self.open_action = QAction("打开", self)
        self.quit_action = QAction("退出", self)
        
        self.open_action.triggered.connect(self.show_and_activate)
        self.quit_action.triggered.connect(self.quit_app)
        
        self.tray_menu.addAction(self.open_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.quit_action)
        
        self.tray_icon.setContextMenu(self.tray_menu)
        
        # 托盘图标激活事件（双击）
        self.tray_icon.activated.connect(self.on_tray_activated)
        
        self.tray_icon.show()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger: # 单击
            pass
        elif reason == QSystemTrayIcon.DoubleClick: # 双击
            self.show_and_activate()

    def show_and_activate(self):
        self.show()
        if hasattr(self.monitor_page, "set_background_mode"):
            self.monitor_page.set_background_mode(False)
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._is_quitting = True
        self.close()

    def closeEvent(self, event):
        if self._is_quitting:
            # 执行真正的退出清理逻辑
            if hasattr(self.monitor_page, "shutdown"):
                self.monitor_page.shutdown()
                
            # 关闭所有弹出的图表窗口
            for card in [self.monitor_page.cpu_card, self.monitor_page.mem_card, self.monitor_page.gpu_card]:
                if card.chart_window:
                    card.chart_window.close()
            
            self.tray_icon.hide() # 退出前隐藏托盘图标
            event.accept()
            
            # 确保应用程序完全退出
            from PySide6.QtWidgets import QApplication
            QApplication.quit()
        else:
            # 仅仅是隐藏窗口到托盘
            event.ignore()
            self.hide()
            # 切换到低采样率模式以节省性能
            if hasattr(self.monitor_page, "set_background_mode"):
                self.monitor_page.set_background_mode(True)
                
            self.tray_icon.showMessage(
                "个人 PC 助手",
                "程序已最小化到托盘",
                QSystemTrayIcon.Information,
                2000
            )

    def init_navigation(self):
        self.addSubInterface(self.monitor_page, FIF.IOT, "监控")
        self.addSubInterface(self.cleaner_page, FIF.DELETE, "清理")
        self.addSubInterface(self.ai_page, FIF.CHAT, "AI 助手")

        # 添加底部导航项
        self.navigationInterface.addSeparator()
        self.addSubInterface(self.settings_page, FIF.SETTING, "设置", NavigationItemPosition.BOTTOM)
