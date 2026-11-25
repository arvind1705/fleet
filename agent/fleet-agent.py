#!/usr/bin/env python3
"""Fleet node agent: a small, dependency-free telemetry daemon for Linux and macOS.

Serves JSON on 127.0.0.1:<agent_port> (default 9108):
  GET  /system            CPU, memory, swap, disk, network, processes,
                          containers and health of this node's services
  GET  /stacks/<name>     state of a controllable Compose stack
  POST /stacks/<name>     {"action": "start" | "stop"} (only stacks listed
                          in the node's "controls" in inventory.json)

The edge proxies it as /api/nodes/<node>/... Runs on Python 3.9+.
"""

import ctypes
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
FLEET_DIR = os.path.dirname(HERE)
IS_MAC = platform.system() == "Darwin"
ENV_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
TAILSCALE = "/Applications/Tailscale.app/Contents/MacOS/Tailscale" if IS_MAC else "tailscale"


def run(args, timeout=5):
    env = dict(os.environ)
    env["PATH"] = ENV_PATH + ":" + env.get("PATH", "")
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        return res.returncode, res.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def load_inventory():
    with open(os.path.join(FLEET_DIR, "inventory.json")) as f:
        return json.load(f)


def detect_node(inv):
    forced = os.environ.get("FLEET_NODE")
    if forced:
        return forced
    want = "macos" if IS_MAC else "linux"
    matches = [n for n, v in inv["nodes"].items() if v.get("os") == want]
    if len(matches) == 1:
        return matches[0]
    sys.exit("Set FLEET_NODE: cannot pick a node from inventory.json")


# --------------------------------------------------------------------------- CPU

class _MacCpu:
    """Per-core tick counters via host_processor_info (no psutil)."""

    def __init__(self):
        self.lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
        self.lib.mach_host_self.restype = ctypes.c_uint
        self.lib.host_processor_info.argtypes = [
            ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_int)), ctypes.POINTER(ctypes.c_uint)]
        self.lib.vm_deallocate.argtypes = [ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
        self.task = ctypes.c_uint.in_dll(self.lib, "mach_task_self_").value
        self.host = self.lib.mach_host_self()

    def ticks(self):
        count = ctypes.c_uint()
        info = ctypes.POINTER(ctypes.c_int)()
        info_count = ctypes.c_uint()
        if self.lib.host_processor_info(self.host, 2, ctypes.byref(count), ctypes.byref(info),
                                        ctypes.byref(info_count)) != 0:
            return []
        try:
            # CPU_STATE_USER, SYSTEM, IDLE, NICE
            out = []
            for i in range(count.value):
                user, system, idle, nice = (info[i * 4 + j] & 0xFFFFFFFF for j in range(4))
                out.append((user + system + nice, user + system + nice + idle))
            return out
        finally:
            self.lib.vm_deallocate(self.task, ctypes.cast(info, ctypes.c_void_p).value,
                                   info_count.value * ctypes.sizeof(ctypes.c_int))


def _linux_ticks():
    out = []
    with open("/proc/stat") as f:
        for line in f:
            if re.match(r"cpu\d+ ", line):
                v = [int(x) for x in line.split()[1:]]
                idle = v[3] + (v[4] if len(v) > 4 else 0)
                total = sum(v[:8])
                out.append((total - idle, total))
    return out


# ---------------------------------------------------------------------- collector

class Collector:
    def __init__(self, inv, node):
        self.inv = inv
        self.node = node
        self.cfg = inv["nodes"][node]
        self.services = [s for s in inv["services"] if s["node"] == node]
        self.lock = threading.Lock()
        self.mac_cpu = _MacCpu() if IS_MAC else None
        self.prev_ticks = self._ticks()
        self.prev_net = (time.time(), self._net_bytes())
        self.cpu_name = self._cpu_name()
        self.cores, self.threads = self._cores()
        self.cache = None
        self.cache_time = 0.0
        self.slow = {"services": {}, "containers": [], "stats": {}, "tailscale": None}
        self.transitions = {}
        threading.Thread(target=self._slow_loop, daemon=True).start()

    # --- static facts
    def _cpu_name(self):
        if IS_MAC:
            return run(["sysctl", "-n", "machdep.cpu.brand_string"])[1].strip() or platform.machine()
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return re.sub(r"\s+", " ", line.split(":", 1)[1]).strip()
        except OSError:
            pass
        return platform.machine()

    def _cores(self):
        threads = os.cpu_count() or 1
        if IS_MAC:
            phys = run(["sysctl", "-n", "hw.physicalcpu"])[1].strip()
            return (int(phys) if phys.isdigit() else threads), threads
        try:
            pairs = set()
            phys = core = None
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("physical id"):
                        phys = line.split(":")[1].strip()
                    elif line.startswith("core id"):
                        core = line.split(":")[1].strip()
                        pairs.add((phys, core))
            return (len(pairs) or threads), threads
        except OSError:
            return threads, threads

    # --- fast samples
    def _ticks(self):
        try:
            return self.mac_cpu.ticks() if IS_MAC else _linux_ticks()
        except Exception:
            return []

    def _cpu(self):
        cur = self._ticks()
        per = []
        for (b0, t0), (b1, t1) in zip(self.prev_ticks, cur):
            dt = t1 - t0
            per.append(round(100.0 * (b1 - b0) / dt, 1) if dt > 0 else 0.0)
        self.prev_ticks = cur
        total = round(sum(per) / len(per), 1) if per else 0.0
        return {
            "name": self.cpu_name, "physical_cores": self.cores, "threads": self.threads,
            "percent": total, "per_cpu": per, "load": [round(x, 2) for x in os.getloadavg()],
            "temp": self._temp(),
        }

    def _temp(self):
        if IS_MAC:
            return None
        best = None
        for base in sorted(os.listdir("/sys/class/hwmon")) if os.path.isdir("/sys/class/hwmon") else []:
            path = os.path.join("/sys/class/hwmon", base)
            try:
                with open(os.path.join(path, "name")) as f:
                    name = f.read().strip()
            except OSError:
                continue
            if name not in ("coretemp", "k10temp", "zenpower", "cpu_thermal"):
                continue
            try:
                with open(os.path.join(path, "temp1_input")) as f:
                    best = round(int(f.read().strip()) / 1000.0, 1)
                    break
            except (OSError, ValueError):
                continue
        return best

    def _memory(self):
        if IS_MAC:
            total = int(run(["sysctl", "-n", "hw.memsize"])[1].strip() or 0)
            out = run(["vm_stat"])[1]
            page = int(re.search(r"page size of (\d+)", out).group(1)) if "page size" in out else 16384
            v = {}
            for line in out.splitlines()[1:]:
                if ":" in line:
                    k, val = line.split(":", 1)
                    try:
                        v[k.strip()] = int(val.strip().rstrip("."))
                    except ValueError:
                        pass
            # Matches Activity Monitor's "Memory Used": app + wired + compressed.
            app = v.get("Anonymous pages", 0) - v.get("Pages purgeable", 0)
            used = (app + v.get("Pages wired down", 0) + v.get("Pages occupied by compressor", 0)) * page
            swap = run(["sysctl", "-n", "vm.swapusage"])[1]

            def mb(key):
                m = re.search(key + r" = ([\d.]+)M", swap)
                return int(float(m.group(1)) * 1024 * 1024) if m else 0
            swap_total, swap_used = mb("total"), mb("used")
        else:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, val = line.split(":", 1)
                    info[k] = int(val.split()[0]) * 1024
            total = info.get("MemTotal", 0)
            used = total - info.get("MemAvailable", 0)
            swap_total = info.get("SwapTotal", 0)
            swap_used = swap_total - info.get("SwapFree", 0)
        pct = lambda a, b: round(100.0 * a / b, 1) if b else 0.0
        return {"total": total, "used": used, "percent": pct(used, total),
                "swap_total": swap_total, "swap_used": swap_used, "swap_percent": pct(swap_used, swap_total)}

    def _disk(self):
        mount = "/System/Volumes/Data" if IS_MAC else "/"
        try:
            du = shutil.disk_usage(mount)
        except OSError:
            du = shutil.disk_usage("/")
        return {"mount": mount, "total": du.total, "used": du.used,
                "percent": round(100.0 * du.used / du.total, 1) if du.total else 0.0}

    def _net_bytes(self):
        rx = tx = 0
        if IS_MAC:
            out = run(["netstat", "-ibn"])[1].splitlines()
            if not out:
                return 0, 0
            head = out[0].split()
            try:
                ib, ob = head.index("Ibytes"), head.index("Obytes")
            except ValueError:
                return 0, 0
            for line in out[1:]:
                cols = line.split()
                if len(cols) < len(head) or "<Link#" not in line:
                    continue
                name = cols[0]
                if name.startswith(("lo", "utun", "bridge", "awdl", "llw", "anpi", "ap", "gif", "stf")):
                    continue
                # Link rows can lack the Address column; count from the right.
                off = len(cols) - len(head)
                try:
                    rx += int(cols[ib + off])
                    tx += int(cols[ob + off])
                except (ValueError, IndexError):
                    pass
        else:
            with open("/proc/net/dev") as f:
                for line in f.readlines()[2:]:
                    name, data = line.split(":", 1)
                    name = name.strip()
                    if name == "lo" or name.startswith(("docker", "veth", "br-", "tailscale")):
                        continue
                    cols = data.split()
                    rx += int(cols[0])
                    tx += int(cols[8])
        return rx, tx

    def _network(self):
        now, cur = time.time(), self._net_bytes()
        t0, prev = self.prev_net
        self.prev_net = (now, cur)
        dt = now - t0
        if dt <= 0:
            return {"rx_rate": 0, "tx_rate": 0}
        return {"rx_rate": max(0, int((cur[0] - prev[0]) / dt)), "tx_rate": max(0, int((cur[1] - prev[1]) / dt))}

    def _uptime(self):
        if IS_MAC:
            m = re.search(r"sec = (\d+)", run(["sysctl", "-n", "kern.boottime"])[1])
            return int(time.time() - int(m.group(1))) if m else None
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))

    def _processes(self):
        args = ["ps", "-Ao", "pcpu=,rss=,comm=", "-r"] if IS_MAC else ["ps", "-eo", "pcpu=,rss=,comm=", "--sort=-pcpu"]
        rows = []
        for line in run(args)[1].splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            try:
                rows.append({"name": os.path.basename(parts[2].strip())[:40],
                             "cpu": float(parts[0]), "mem": int(parts[1]) * 1024})
            except ValueError:
                continue
            if len(rows) >= 6:
                break
        return rows

    # --- slow samples (background)
    def _probe(self, svc):
        url = "http://%s%s" % (svc["upstream"], svc.get("health", "/"))
        start = time.time()
        code = 0
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fleet-agent"})
            with urllib.request.urlopen(req, timeout=4) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:
            code = 0
        ms = int((time.time() - start) * 1000)
        up = 200 <= code < 400 or code in (401, 403)
        return svc["id"], {"up": up, "code": code, "ms": ms}

    def _containers(self):
        code, out = run(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=8)
        rows = []
        if code != 0:
            return None
        for line in out.splitlines():
            try:
                c = json.loads(line)
            except ValueError:
                continue
            rows.append({"name": c.get("Names"), "image": c.get("Image"), "state": c.get("State"),
                         "status": c.get("Status"),
                         "project": (re.search(r"com\.docker\.compose\.project=([^,]+)", c.get("Labels", "")) or [None, None])[1]})
        return rows

    def _stats(self):
        code, out = run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=15)
        stats = {}
        if code != 0:
            return stats
        for line in out.splitlines():
            try:
                s = json.loads(line)
            except ValueError:
                continue
            stats[s.get("Name")] = {"cpu": s.get("CPUPerc"), "mem": (s.get("MemUsage") or "").split(" / ")[0]}
        return stats

    def _tailscale(self):
        code, out = run([TAILSCALE, "status", "--json"], timeout=4)
        if code != 0 or not out:
            return None
        try:
            me = json.loads(out).get("Self") or {}
        except ValueError:
            return None
        ips = me.get("TailscaleIPs") or []
        return {"dns_name": (me.get("DNSName") or "").rstrip(".") or None,
                "ip": next((ip for ip in ips if "." in ip), None), "online": me.get("Online")}

    def _slow_loop(self):
        pool = ThreadPoolExecutor(max_workers=6)
        tick = 0
        while True:
            try:
                services = dict(pool.map(self._probe, self.services))
                containers = self._containers()
                update = {"services": services, "containers": containers}
                if tick % 3 == 0:
                    update["stats"] = self._stats()
                if tick % 6 == 0:
                    update["tailscale"] = self._tailscale()
                with self.lock:
                    self.slow.update(update)
            except Exception as exc:  # keep the loop alive
                print("slow sample failed: %s" % exc, file=sys.stderr)
            tick += 1
            time.sleep(10)

    # --- stacks
    def stack_state(self, name):
        if name in self.transitions:
            return self.transitions[name]
        with self.lock:
            containers = self.slow.get("containers") or []
        wanted = [c for s in self.services if s.get("stack") == name for c in s.get("containers", [])]
        running = [c for c in containers if c["name"] in wanted and c["state"] == "running"]
        if wanted and len(running) == len(wanted):
            return "running"
        return "partial" if running else "stopped"

    def stack_action(self, name, action):
        if name not in self.cfg.get("controls", []):
            return 403, {"error": "stack %s is not controllable on %s" % (name, self.node)}
        if action not in ("start", "stop"):
            return 400, {"error": "action must be start or stop"}
        if name in self.transitions:
            return 409, {"state": self.transitions[name]}
        self.transitions[name] = "starting" if action == "start" else "stopping"

        def worker():
            try:
                if IS_MAC:
                    d = os.path.join(FLEET_DIR, "stacks", name)
                    run(["docker", "compose", "up", "-d", "--wait"] if action == "start" else ["docker", "compose", "stop"],
                        timeout=300) if os.path.isdir(d) else None
                else:
                    run(["sudo", "-n", "systemctl", action, "stack@%s" % name], timeout=300)
            finally:
                with self.lock:
                    self.slow["containers"] = self._containers() or self.slow.get("containers")
                self.transitions.pop(name, None)

        threading.Thread(target=worker, daemon=True).start()
        return 202, {"state": self.transitions[name]}

    # --- snapshot
    def snapshot(self):
        with self.lock:
            now = time.time()
            if self.cache and now - self.cache_time < 1.5:
                return self.cache
            slow = dict(self.slow)
        containers = slow.get("containers")
        stats = slow.get("stats") or {}
        by_name = {c["name"]: c for c in (containers or [])}
        services = {}
        for s in self.services:
            probe = slow["services"].get(s["id"])
            names = s.get("containers", [])
            running = [n for n in names if by_name.get(n, {}).get("state") == "running"]
            if probe is None:
                status = "unknown"
            elif probe["up"]:
                status = "up"
            elif names and containers is not None and not running:
                status = "stopped"
            else:
                status = "down"
            res = [stats[n] for n in names if n in stats]
            services[s["id"]] = dict(probe or {}, status=status, resources=res[0] if res else None)
        snap = {
            "node": self.node,
            "hostname": socket.gethostname(),
            "os": "macos" if IS_MAC else "linux",
            "os_version": platform.mac_ver()[0] if IS_MAC else platform.release(),
            "cpu": self._cpu(),
            "memory": self._memory(),
            "disk": self._disk(),
            "network": self._network(),
            "uptime_seconds": self._uptime(),
            "top_processes": self._processes(),
            "containers": [dict(c, **({"resources": stats[c["name"]]} if c["name"] in stats else {}))
                           for c in (containers or [])],
            "docker": containers is not None,
            "services": services,
            "stacks": {n: self.stack_state(n) for n in self.cfg.get("controls", [])},
            "tailscale": slow.get("tailscale"),
            "timestamp": int(now),
        }
        with self.lock:
            self.cache, self.cache_time = snap, now
        return snap


# ------------------------------------------------------------------------ server

def make_handler(collector):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _path(self):
            p = self.path.split("?", 1)[0].rstrip("/")
            return p[4:] if p.startswith("/api") else p  # accept /api/system too

        def do_GET(self):
            p = self._path()
            if p in ("", "/system"):
                return self._send(200, collector.snapshot())
            if p == "/health":
                return self._send(200, {"ok": True, "node": collector.node})
            m = re.match(r"^/stacks/([\w.-]+)$", p)
            if m:
                return self._send(200, {"state": collector.stack_state(m.group(1))})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            m = re.match(r"^/stacks/([\w.-]+)$", self._path())
            if not m:
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            code, out = collector.stack_action(m.group(1), body.get("action"))
            self._send(code, out)

        def log_message(self, fmt, *args):
            pass

    return Handler


def main():
    inv = load_inventory()
    node = detect_node(inv)
    port = int(os.environ.get("FLEET_AGENT_PORT") or inv["nodes"][node].get("agent_port", 9108))
    host = os.environ.get("FLEET_AGENT_HOST", "127.0.0.1")
    collector = Collector(inv, node)
    server = ThreadingHTTPServer((host, port), make_handler(collector))
    print("fleet-agent for %s listening on %s:%d" % (node, host, port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
