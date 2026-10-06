import subprocess


ALLOWED_COMMANDS = {
    "systeminfo",
    "hostname",
    "whoami",
    "ipconfig",
    "get-date",
    "get-process",
    "get-service",
    "get-computerinfo",
    "get-ciminstance"
}


SAFE_EXACT_COMMANDS = {
    "get-ciminstance win32_computersystem | select-object totalphysicalmemory":
        "Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory",

    "get-ciminstance win32_computersystem | select-object totalphysicalmemory, numberoflogicalprocessors":
        "Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory, NumberOfLogicalProcessors",

    "get-ciminstance win32_processor | select-object name, numberofcores, numberoflogicalprocessors":
        "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors",

    "get-ciminstance win32_operatingsystem | select-object caption, version, osarchitecture":
        "Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, OSArchitecture"
}


def run_powershell(command: str) -> str:

    command = command.strip()

    if not command:
        return "PowerShell command is empty."

    normalized = " ".join(command.lower().split())

    if normalized in SAFE_EXACT_COMMANDS:
        command = SAFE_EXACT_COMMANDS[normalized]

    else:
        first_word = normalized.split()[0]

        if first_word not in ALLOWED_COMMANDS:
            return (
                "Command blocked by IRIS safety policy.\n"
                f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}"
            )

        if first_word == "get-ciminstance":
            return (
                "Command blocked by IRIS safety policy.\n"
                "Only approved read-only Get-CimInstance queries are allowed."
            )

    try:

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                command
            ],
            capture_output=True,
            text=True,
            timeout=20
        )

        if result.returncode != 0:
            return (
                "PowerShell error:\n"
                f"{result.stderr.strip()}"
            )

        return result.stdout.strip()

    except subprocess.TimeoutExpired:

        return "PowerShell command timed out."

    except Exception as e:

        return f"PowerShell tool error: {e}"
