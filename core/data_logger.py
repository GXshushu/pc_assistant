import csv
import os
import tempfile
from datetime import datetime

class DataLogger:
    def __init__(self, filename_prefix='monitor_log_'):
        # Create a temporary file that will be deleted on close
        self.temp_file = tempfile.NamedTemporaryFile(
            mode='w+', delete=False, newline='', encoding='utf-8', suffix='.csv'
        )
        self.filepath = self.temp_file.name
        self.writer = csv.writer(self.temp_file)
        # Write header
        self.writer.writerow(['timestamp', 'cpu', 'mem', 'gpu', 'temp'])
        self.temp_file.flush()

    def log(self, cpu, mem, gpu, temp=None):
        """Logs a new data point."""
        timestamp = datetime.now().isoformat()
        self.writer.writerow([timestamp, cpu, mem, gpu, temp])
        self.temp_file.flush() # Ensure data is written to disk

    def get_all_data(self):
        """Reads all data from the log file."""
        self.temp_file.seek(0)
        reader = csv.reader(self.temp_file)
        header = next(reader) # Skip header
        data = {
            'timestamp': [],
            'cpu': [],
            'mem': [],
            'gpu': [],
            'temp': [],
        }
        for row in reader:
            if len(row) < 3:
                continue
            data['timestamp'].append(datetime.fromisoformat(row[0]))
            data['cpu'].append(self._safe_float(row[1]))
            data['mem'].append(self._safe_float(row[2]))
            gpu_value = row[3] if len(row) > 3 else ""
            data['gpu'].append(self._safe_float(gpu_value))
            temp_value = row[4] if len(row) > 4 else ""
            data['temp'].append(self._safe_float(temp_value))
        return data

    def _safe_float(self, value):
        if value in ("", "None", None):
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def close(self):
        """Closes the file handle and deletes the file."""
        self.temp_file.close()
        try:
            os.remove(self.filepath)
        except OSError as e:
            print(f"Error removing log file {self.filepath}: {e}")
