"""
Script kiem tra he thong truoc khi trien khai ToolAutoXalat.
Chay: python check_system.py
"""
import sys
import io
# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import subprocess
import shutil
import platform
import os
import sys

def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def check_pass(name, ok, detail=""):
    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {status}  {name}" + (f" → {detail}" if detail else ""))
    return ok

def main():
    results = []
    print_header("KIỂM TRA HỆ THỐNG - ToolAutoXalat")
    print(f"  OS: {platform.system()} {platform.release()}")
    print(f"  Architecture: {platform.machine()}")

    # 1. Check ADB
    print_header("1. ADB (Android Debug Bridge)")
    adb_path = shutil.which("adb")
    results.append(check_pass("ADB đã cài đặt", adb_path is not None, adb_path or "Chưa cài. Tải tại: developer.android.com/tools/releases/platform-tools"))

    if adb_path:
        try:
            r = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
            lines = [l for l in r.stdout.strip().split("\n") if "\tdevice" in l]
            results.append(check_pass(f"Thiết bị kết nối: {len(lines)} device(s)", len(lines) > 0, 
                           ", ".join([l.split("\t")[0] for l in lines]) if lines else "Không có thiết bị. Cắm USB + bật USB Debugging"))
            
            if lines:
                device_id = lines[0].split("\t")[0]
                # Check resolution
                r2 = subprocess.run(["adb", "-s", device_id, "shell", "wm", "size"], capture_output=True, text=True, timeout=5)
                results.append(check_pass("Độ phân giải màn hình", True, r2.stdout.strip()))
                
                # Check Android version
                r3 = subprocess.run(["adb", "-s", device_id, "shell", "getprop", "ro.build.version.release"], capture_output=True, text=True, timeout=5)
                android_ver = r3.stdout.strip()
                results.append(check_pass(f"Android version", True, f"Android {android_ver}"))

                # Check WiFi ADB support
                wifi_adb_ok = False
                try:
                    android_major = int(android_ver.split(".")[0])
                    wifi_adb_ok = android_major >= 11
                except:
                    pass
                results.append(check_pass("WiFi ADB support (Android 11+)", wifi_adb_ok, 
                               f"Android {android_ver}" + (" → Hỗ trợ Wireless Debugging" if wifi_adb_ok else " → Dùng USB hoặc adb tcpip")))
                
                # Check screencap speed
                import time
                start = time.time()
                subprocess.run(["adb", "-s", device_id, "shell", "screencap", "-p", "/dev/null"], capture_output=True, timeout=10)
                elapsed = (time.time() - start) * 1000
                results.append(check_pass(f"Tốc độ screencap", elapsed < 500, f"{elapsed:.0f}ms (< 500ms là OK, scrcpy sẽ nhanh hơn nhiều)"))

                # Check if phone screen is on
                r4 = subprocess.run(["adb", "-s", device_id, "shell", "dumpsys", "power"], capture_output=True, text=True, timeout=5)
                screen_on = "Display Power: state=ON" in r4.stdout or "mScreenOn=true" in r4.stdout
                results.append(check_pass("Màn hình điện thoại đang BẬT", screen_on, 
                               "Đang bật" if screen_on else "Đang tắt - bật màn hình lên để tool hoạt động"))

        except subprocess.TimeoutExpired:
            results.append(check_pass("ADB phản hồi", False, "Timeout - kiểm tra lại kết nối"))
        except Exception as e:
            results.append(check_pass("ADB hoạt động", False, str(e)))

    # 2. Check scrcpy
    print_header("2. SCRCPY (Fast Screen Capture)")
    scrcpy_path = shutil.which("scrcpy")
    results.append(check_pass("scrcpy đã cài đặt", scrcpy_path is not None, scrcpy_path or "Chưa cài. Tải tại: github.com/Genymobile/scrcpy"))

    # 3. Check .NET
    print_header("3. .NET SDK")
    dotnet_path = shutil.which("dotnet")
    results.append(check_pass(".NET SDK đã cài", dotnet_path is not None, dotnet_path or "Chưa cài. Tải tại: dotnet.microsoft.com"))
    if dotnet_path:
        try:
            r = subprocess.run(["dotnet", "--version"], capture_output=True, text=True, timeout=5)
            ver = r.stdout.strip()
            is_net8 = ver.startswith("8.") or int(ver.split(".")[0]) >= 8
            results.append(check_pass(f".NET version >= 8.0", is_net8, f"v{ver}"))
        except:
            pass

    # 4. Check Python + Tesseract
    print_header("4. PYTHON & OCR")
    results.append(check_pass(f"Python version", True, f"{sys.version.split()[0]}"))
    
    tess_path = shutil.which("tesseract")
    results.append(check_pass("Tesseract OCR đã cài", tess_path is not None, tess_path or "Chưa cài (tùy chọn, có thể dùng PaddleOCR)"))

    # 5. Check GPU (NVIDIA)
    print_header("5. GPU (NVIDIA CUDA - Tùy chọn)")
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"], 
                             capture_output=True, text=True, timeout=5)
            gpu_info = r.stdout.strip()
            results.append(check_pass("NVIDIA GPU", True, gpu_info))
        except:
            results.append(check_pass("NVIDIA GPU", False, "nvidia-smi lỗi"))
    else:
        results.append(check_pass("NVIDIA GPU", False, "Không có GPU NVIDIA (Tool vẫn chạy được ở CPU mode, chậm hơn ~3x)"))

    # 6. System resources
    print_header("6. TÀI NGUYÊN HỆ THỐNG")
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024**3)
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        results.append(check_pass(f"RAM >= 8GB", ram_gb >= 8, f"{ram_gb:.1f} GB"))
        results.append(check_pass(f"CPU cores >= 4", cpu_count >= 4, f"{cpu_count} cores"))
        if cpu_freq:
            results.append(check_pass(f"CPU frequency", True, f"{cpu_freq.current:.0f} MHz"))
        
        # Disk space
        disk = psutil.disk_usage(os.path.abspath('.'))
        disk_free_gb = disk.free / (1024**3)
        results.append(check_pass(f"Ổ đĩa trống >= 10GB", disk_free_gb >= 10, f"{disk_free_gb:.1f} GB trống"))
        
    except ImportError:
        print("  ⚠️  Cài 'pip install psutil' để kiểm tra RAM/CPU chi tiết")
        # Fallback: dùng wmic trên Windows
        if platform.system() == "Windows":
            try:
                r = subprocess.run(["wmic", "OS", "get", "TotalVisibleMemorySize", "/value"], capture_output=True, text=True, timeout=10)
                for line in r.stdout.split("\n"):
                    if "TotalVisibleMemorySize" in line:
                        ram_kb = int(line.split("=")[1].strip())
                        ram_gb = ram_kb / (1024**2)
                        results.append(check_pass(f"RAM >= 8GB", ram_gb >= 8, f"{ram_gb:.1f} GB"))
            except:
                pass
            
            try:
                r = subprocess.run(["wmic", "cpu", "get", "NumberOfCores", "/value"], capture_output=True, text=True, timeout=10)
                for line in r.stdout.split("\n"):
                    if "NumberOfCores" in line:
                        cores = int(line.split("=")[1].strip())
                        results.append(check_pass(f"CPU cores >= 4", cores >= 4, f"{cores} cores"))
            except:
                pass

    # Summary
    print_header("KẾT QUẢ TỔNG HỢP")
    passed = sum(results)
    total = len(results)
    print(f"\n  Đạt: {passed}/{total} mục")
    
    if passed == total:
        print("  🎉 HỆ THỐNG SẴN SÀNG! Có thể triển khai ToolAutoXalat.")
    elif passed >= total * 0.7:
        print("  ⚠️  Cơ bản đạt yêu cầu. Kiểm tra các mục FAIL ở trên để tối ưu.")
    else:
        print("  ❌ Chưa đủ điều kiện. Cần cài đặt thêm các phần mềm bị thiếu.")

    print(f"\n{'='*60}")
    print("  Nếu tất cả PASS → Bạn có thể bắt đầu build và treo tool!")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
