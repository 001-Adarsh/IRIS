import time
import socket
import subprocess
import platform
import os

class SystemDiagnosticsTool:
    """
    High‑speed system diagnostic tool for measuring CPU temperature and network latency.
    """
    name = "system_diagnostics"
    description = "Measures high‑speed system CPU temperature and network latency."

    def __init__(self):
        pass

    def run(self, *args, **kwargs):
        """
        Entry point used by the ToolManager. Delegates to `execute`.
        """
        return self.execute()

    def execute(self) -> dict:
        """
        Execute the diagnostic measurements and return a dictionary.
        """
        return {
            "cpu_temperature_celsius": self.get_cpu_temperature(),
            "network_latency_ms": self.get_network_latency(),
            "timestamp": time.time(),
        }

    def get_cpu_temperature(self) -> float:
        """
        Attempts to retrieve CPU temperature using platform‑specific methods.
        Returns temperature in Celsius or a default/simulated value if unavailable.
        """
        system = platform.system()
        try:
            if system == "Linux":
                # Common thermal zone path
                thermal_dir = "/sys/class/thermal/"
                if os.path.isdir(thermal_dir):
                    for entry in os.listdir(thermal_dir):
                        if entry.startswith("thermal_zone"):
                            temp_file = os.path.join(thermal_dir, entry, "temp")
                            if os.path.isfile(temp_file):
                                with open(temp_file, "r") as f:
                                    temp_val = float(f.read().strip())
                                    # Usually in millidegrees Celsius
                                    return temp_val / 1000.0
            elif system == "Darwin":  # macOS
                # Requires external tool `osx-cpu-temp` or `powermetrics`
                process = subprocess.Popen(
                    ["osx-cpu-temp"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                stdout, _ = process.communicate()
                if stdout:
                    temp_str = stdout.decode("utf-8").strip().replace("°C", "").strip()
                    return float(temp_str)
            elif system == "Windows":
                # PowerShell WMI query
                cmd = (
                    "powershell -Command "
                    "\"Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi | "
                    "Select-Object -ExpandProperty CurrentTemperature\""
                )
                process = subprocess.Popen(
                    cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                stdout, _ = process.communicate()
                if stdout:
                    val = float(stdout.decode("utf-8").strip())
                    # WMI returns Kelvin * 10
                    return (val / 10.0) - 273.15
        except Exception:
            pass

        # Fallback default value if hardware sensors are inaccessible
        return 45.0

    def get_network_latency(self, host: str = "8.8.8.8", port: int = 53, timeout: float = 1.0) -> float:
        """
        Measures network latency in milliseconds by opening a TCP connection to a reliable host.
        """
        start_time = time.time()
        try:
            socket.setdefaulttimeout(timeout)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, port))
            s.close()
            end_time = time.time()
            return round((end_time - start_time) * 1000.0, 2)
        except Exception:
            return -1.0