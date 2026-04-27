#!/usr/bin/env python3
"""
inject_fault.py â€” ASHIA Fault Injection Suite
Injects one of 7 fault types into the target system on demand.
Used for testing the ASHIA self-healing pipeline.

Usage:
  python inject_fault.py --type memory_leak --service order-service
  python inject_fault.py --type cpu_spike --service order-service --duration 60
  python inject_fault.py --type db_exhaustion --service user-service
  python inject_fault.py --type db_connection_exhaustion --service user-service
  python inject_fault.py --type slow_query --service order-service
  python inject_fault.py --type error_rate --service order-service --rate 0.8
  python inject_fault.py --type redis_overflow --service order-service --ratio 0.95
  python inject_fault.py --type cascade_failure
  python inject_fault.py --reset
  python inject_fault.py --list
"""
import argparse
import time
import sys
import requests

SERVICES = {
    "order-service": "http://localhost:8002",
    "user-service":  "http://localhost:8001",
    "api-gateway":   "http://localhost:8000",
}

# ANSI colours
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def log(msg, color=RESET):
    print(f"{color}{msg}{RESET}")


def check_service(url: str, name: str) -> bool:
    try:
        r = requests.get(f"{url}/health", timeout=3.0)
        if r.status_code == 200:
            log(f"  âœ“ {name} is healthy", GREEN)
            return True
    except Exception as e:
        log(f"  âœ— {name} unreachable: {e}", RED)
    return False


def preflight_check():
    log(f"\n{BOLD}=== ASHIA Fault Injection Suite ==={RESET}")
    log("Checking target services...", BLUE)
    all_ok = True
    for name, url in SERVICES.items():
        if not check_service(url, name):
            all_ok = False
    if not all_ok:
        log("\nWARNING: Some services unreachable. Run: cd target-system && docker compose up -d", YELLOW)
    return all_ok


# â”€â”€ Fault Types â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def inject_memory_leak(service: str = "order-service", cycles: int = 15):
    """
    Progressively allocates ~5MB per call until OOM pressure is visible.
    Prometheus metric: order_memory_leak_bytes will spike.
    Expected ASHIA response: Monitor fires CRITICAL â†’ restart_container.
    """
    url = SERVICES.get(service, SERVICES["order-service"])
    log(f"\n[FAULT] Injecting memory_leak into {service} ({cycles} cycles Ã— 5MB)", RED)
    log(f"  Prometheus metric to watch: order_memory_leak_bytes at localhost:9090", BLUE)

    for i in range(1, cycles + 1):
        try:
            r = requests.post(f"{url}/fault/memory-leak", params={"mb_per_call": 5}, timeout=5.0)
            data = r.json()
            leak_mb = data.get("leak_mb", "?")
            log(f"  Cycle {i:2d}/{cycles} â€” total leak: {leak_mb} MB", YELLOW if i < cycles else RED)
            time.sleep(1.5)
        except Exception as e:
            log(f"  Cycle {i} failed: {e}", RED)

    log(f"\n[FAULT] Memory leak injected. Total ~{cycles * 5}MB allocated.", RED)
    log("  ASHIA Monitor Agent will detect anomaly within 90s.", BLUE)
    log("  Watch: http://localhost:9090 â†’ query: order_memory_leak_bytes", BLUE)


def inject_cpu_spike(service: str = "order-service", duration: int = 60):
    """
    Floods the service with rapid requests to spike CPU and request count.
    Prometheus metric: order_requests_total rate will spike.
    Expected ASHIA response: Monitor detects error rate spike â†’ restart or investigate.
    """
    url    = SERVICES.get(service, SERVICES["order-service"])
    log(f"\n[FAULT] Injecting cpu_spike into {service} for {duration}s", RED)
    log(f"  This floods the service with rapid POST /orders requests", BLUE)

    end   = time.time() + duration
    count = 0
    try:
        while time.time() < end:
            try:
                requests.post(f"{url}/orders", timeout=2.0)
                count += 1
                if count % 50 == 0:
                    elapsed = duration - (end - time.time())
                    log(f"  Sent {count} requests in {elapsed:.0f}s...", YELLOW)
            except Exception:
                pass
    except KeyboardInterrupt:
        pass

    log(f"\n[FAULT] CPU spike complete. Sent {count} requests in {duration}s.", RED)
    log("  Watch: order_requests_total rate at localhost:9090", BLUE)


def inject_db_exhaustion(service: str = "user-service", connections: int = 95):
    """
    Simulates DB connection pool exhaustion by setting connection count near limit.
    Prometheus metric: user_db_connections_active will spike to 95/100.
    Expected ASHIA response: HIGH risk â†’ HITL triggered â†’ db_connection_reset.
    """
    url = SERVICES.get(service, SERVICES["user-service"])
    log(f"\n[FAULT] Injecting db_exhaustion into {service} ({connections}/100 connections)", RED)
    log("  This is a HIGH-risk scenario â€” ASHIA will pause for HITL approval", YELLOW)

    try:
        r = requests.post(f"{url}/fault/db-exhaustion", params={"connections": connections}, timeout=5.0)
        log(f"  Response: {r.json()}", GREEN if r.status_code == 200 else RED)
    except Exception as e:
        log(f"  Injection failed: {e}", RED)

    log(f"\n[FAULT] DB exhaustion injected. Check:", BLUE)
    log("  Prometheus: user_db_connections_active at localhost:9090", BLUE)
    log("  Expect: ASHIA sends Slack notification requesting human approval", YELLOW)


def inject_slow_query(service: str = "order-service"):
    """
    Activates artificial latency on all requests (simulates slow queries).
    Prometheus metric: order_request_duration_seconds p95 will spike.
    Expected ASHIA response: latency alert â†’ ROOT CAUSE â†’ investigate or config patch.
    """
    url = SERVICES.get(service, SERVICES["order-service"])
    log(f"\n[FAULT] Injecting slow_query into {service}", RED)

    try:
        r = requests.post(f"{url}/fault/slow-query", params={"active": "true"}, timeout=5.0)
        log(f"  Response: {r.json()}", GREEN if r.status_code == 200 else RED)
    except Exception as e:
        log(f"  Injection failed: {e}", RED)

    log(f"\n[FAULT] Slow query active. All requests now take 2-4s extra.", RED)
    log("  Watch: order_request_duration_seconds at localhost:9090", BLUE)
    log("  Deactivate with: python inject_fault.py --reset", BLUE)


def inject_error_rate(service: str = "order-service", rate: float = 0.7):
    """
    Injects a simulated HTTP 500 error rate.
    Prometheus metric: order_errors_total rate spikes.
    Expected ASHIA response: CRITICAL error rate â†’ ROOT CAUSE â†’ restart.
    """
    url = SERVICES.get(service, SERVICES["order-service"])
    log(f"\n[FAULT] Injecting error_rate={rate:.0%} into {service}", RED)

    try:
        r = requests.post(f"{url}/fault/error-rate", params={"rate": rate}, timeout=5.0)
        log(f"  Response: {r.json()}", GREEN if r.status_code == 200 else RED)
    except Exception as e:
        log(f"  Injection failed: {e}", RED)

    log(f"\n[FAULT] {rate:.0%} of requests will now return HTTP 500", RED)
    log("  Watch: rate(order_errors_total[1m]) at localhost:9090", BLUE)


def inject_redis_overflow(service: str = "order-service", ratio: float = 0.95):
    """
    Simulates Redis cache pressure / overflow for cache-remediation demos.
    Prometheus metric: redis_cache_pressure_ratio will spike near 1.0.
    """
    url = SERVICES.get(service, SERVICES["order-service"])
    log(f"\n[FAULT] Injecting redis_overflow into {service} ({ratio:.0%} pressure)", RED)

    try:
        r = requests.post(f"{url}/fault/redis-overflow", params={"ratio": ratio}, timeout=5.0)
        log(f"  Response: {r.json()}", GREEN if r.status_code == 200 else RED)
    except Exception as e:
        log(f"  Injection failed: {e}", RED)

    log(f"\n[FAULT] Redis pressure active at ~{ratio:.0%}", RED)
    log("  Watch: redis_cache_pressure_ratio at localhost:9090", BLUE)


def inject_cascade_failure():
    """
    Injects memory_leak + slow_query + redis_overflow + db_exhaustion together.
    Tests ASHIA's ability to reason across multiple simultaneous signals.
    The most complex test case in the demo flow.
    """
    log(f"\n{BOLD}[FAULT] CASCADE FAILURE - injecting memory, latency, cache pressure, and DB exhaustion{RESET}", RED)
    log("  This tests ASHIA's multi-signal reasoning capability", YELLOW)
    log("  Expected: Root Cause Agent must correlate multiple concurrent failure signals", BLUE)

    log("\n  Phase 1: Starting order-service pressure faults...", YELLOW)
    url_order = SERVICES["order-service"]
    for _ in range(8):
        try:
            requests.post(f"{url_order}/fault/memory-leak", params={"mb_per_call": 5}, timeout=5.0)
            time.sleep(0.5)
        except Exception:
            pass
    try:
        requests.post(f"{url_order}/fault/slow-query", params={"active": "true", "delay_seconds": 3.0}, timeout=5.0)
        requests.post(f"{url_order}/fault/redis-overflow", params={"ratio": 0.96}, timeout=5.0)
    except Exception as e:
        log(f"  Order-service pressure injection failed: {e}", RED)

    log("  Phase 2: Triggering DB exhaustion on user-service...", YELLOW)
    url_user = SERVICES["user-service"]
    try:
        requests.post(f"{url_user}/fault/db-exhaustion", params={"connections": 92}, timeout=5.0)
    except Exception as e:
        log(f"  DB exhaustion injection failed: {e}", RED)

    log(f"\n{BOLD}[FAULT] Cascade failure injected.{RESET}", RED)
    log("  Order-service and user-service now expose multi-signal degradation", RED)
    log("  Watch ASHIA coordinate resolution across both services", BLUE)


def inject_rollback(service: str = "order-service", target_version: str = "v0.9.0"):
    """
    Simulates a rollback to a previous stable deployment version.
    Useful for high-risk HITL approval demos.
    """
    url = SERVICES.get(service, SERVICES["order-service"])
    log(f"\n[FAULT] Triggering rollback on {service} to {target_version}", RED)

    try:
        r = requests.post(f"{url}/fault/rollback", params={"target_version": target_version}, timeout=5.0)
        log(f"  Response: {r.json()}", GREEN if r.status_code == 200 else RED)
    except Exception as e:
        log(f"  Rollback failed: {e}", RED)

    log("\n[FAULT] Rollback simulation complete.", RED)
    log("  Watch: service health/fault status and incident trace in ASHIA dashboard", BLUE)


def reset_all_faults():
    """Reset all injected faults on all services."""
    log(f"\n{BOLD}[RESET] Resetting all faults...{RESET}", BLUE)
    for name, url in [("order-service", SERVICES["order-service"]),
                      ("user-service",  SERVICES["user-service"])]:
        try:
            r = requests.post(f"{url}/fault/reset", timeout=5.0)
            log(f"  âœ“ {name}: {r.json()}", GREEN)
        except Exception as e:
            log(f"  âœ— {name} reset failed: {e}", RED)
    log("\n[RESET] All faults cleared. System should return to normal within 30s.", GREEN)


def list_faults():
    log(f"\n{BOLD}Available fault types:{RESET}")
    faults = [
        ("memory_leak",    "order-service", "Memory leak â€” OOM pressure. LOW risk â†’ auto-fix."),
        ("cpu_spike",      "order-service", "Rapid request flood â€” CPU/request rate spike."),
        ("db_exhaustion",  "user-service",  "DB connection pool at 95%. HIGH risk â†’ HITL required."),
        ("db_connection_exhaustion", "user-service", "Alias of db_exhaustion for the formal project spec."),
        ("slow_query",     "order-service", "Artificial 2-4s latency on all requests."),
        ("error_rate",     "order-service", "70% HTTP 500 error rate injection."),
        ("redis_overflow", "order-service", "Redis/cache memory pressure near full capacity."),
        ("cascade_failure","both services", "memory + latency + cache pressure + db exhaustion."),
        ("rollback",       "order-service", "Rollback to previous stable deployment version."),
    ]
    for name, svc, desc in faults:
        log(f"  {BOLD}{name:<18}{RESET} [{svc}] {desc}")
    log(f"\n  {BOLD}--reset{RESET}              Reset all active faults")
    log(f"  {BOLD}--list{RESET}               Show this list\n")


# â”€â”€ CLI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    parser = argparse.ArgumentParser(
        description="ASHIA Fault Injection Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--type",     choices=["memory_leak","cpu_spike","db_exhaustion","db_connection_exhaustion",
                                                "slow_query","error_rate","redis_overflow","cascade_failure","rollback"])
    parser.add_argument("--service",  default="order-service")
    parser.add_argument("--duration", type=int,   default=60,  help="Duration in seconds (cpu_spike)")
    parser.add_argument("--cycles",   type=int,   default=15,  help="Cycles for memory_leak")
    parser.add_argument("--rate",     type=float, default=0.7, help="Error rate 0.0-1.0")
    parser.add_argument("--ratio",    type=float, default=0.95, help="Pressure ratio for redis_overflow")
    parser.add_argument("--target-version", default="v0.9.0", help="Target version for rollback")
    parser.add_argument("--connections", type=int, default=95, help="DB connections (db_exhaustion)")
    parser.add_argument("--reset",    action="store_true", help="Reset all faults")
    parser.add_argument("--list",     action="store_true", help="List all fault types")
    parser.add_argument("--no-check", action="store_true", help="Skip preflight health check")

    args = parser.parse_args()

    if args.list:
        list_faults()
        return

    if not args.no_check:
        preflight_check()

    if args.reset:
        reset_all_faults()
        return

    if not args.type:
        parser.print_help()
        return

    if args.type == "memory_leak":
        inject_memory_leak(args.service, args.cycles)
    elif args.type == "cpu_spike":
        inject_cpu_spike(args.service, args.duration)
    elif args.type in {"db_exhaustion", "db_connection_exhaustion"}:
        inject_db_exhaustion(args.service, args.connections)
    elif args.type == "slow_query":
        inject_slow_query(args.service)
    elif args.type == "error_rate":
        inject_error_rate(args.service, args.rate)
    elif args.type == "redis_overflow":
        inject_redis_overflow(args.service, args.ratio)
    elif args.type == "cascade_failure":
        inject_cascade_failure()
    elif args.type == "rollback":
        inject_rollback(args.service, args.target_version)

    log(f"\n{BOLD}[ASHIA]{RESET} Monitor Agent will detect this anomaly within 90s.", GREEN)
    log("[ASHIA] Watch the dashboard at http://localhost:3000 for agent response.", GREEN)
    log("[ASHIA] Or run: python inject_fault.py --reset    to clear faults.\n", BLUE)


if __name__ == "__main__":
    main()
