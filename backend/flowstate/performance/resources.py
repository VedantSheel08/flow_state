"""Process-tree resource samples without an additional runtime dependency."""
from __future__ import annotations

import ctypes
import os
import time


def sample(pid: int | None = None) -> dict:
    pid = pid or os.getpid()
    if os.name == "nt":
        from ctypes import wintypes as w

        class Memory(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("PageFaultCount", w.DWORD),
                        *[(k, ctypes.c_size_t) for k in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                          "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = w.HANDLE
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.GetProcessTimes.argtypes = [w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)]
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.POINTER(Memory), w.DWORD]
        handle = kernel.OpenProcess(0x410, False, pid)
        if not handle:
            return {"pid": pid, "available": False}
        try:
            memory = Memory()
            memory.cb = ctypes.sizeof(memory)
            times = [w.FILETIME() for _ in range(4)]
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                raise ctypes.WinError(ctypes.get_last_error())
            cpu = sum((t.dwHighDateTime << 32) + t.dwLowDateTime for t in times[2:]) / 1e7
            return {"pid": pid, "available": True, "cpuSeconds": cpu,
                    "rssBytes": memory.WorkingSetSize, "peakRssBytes": memory.PeakWorkingSetSize}
        finally:
            kernel.CloseHandle(handle)
    if pid == os.getpid():
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"pid": pid, "available": True, "cpuSeconds": time.process_time(),
                "peakRssBytes": peak * (1 if os.sys.platform == "darwin" else 1024)}
    return {"pid": pid, "available": False}
