#!/usr/bin/env python3
import urllib.request
import urllib.error
import json
import sys
import time

PORT = 8080
BASE_URL = f"http://127.0.0.1:{PORT}"

def fetch(url):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except urllib.error.URLError as e:
        print(f"❌ HTTP Error for {url}: {e}")
        return None
    except json.JSONDecodeError:
        print(f"❌ Invalid JSON response for {url}")
        return None

def test_system_diagnostics():
    print(f"\n--- Testing /api/system/diagnostics ---")
    data = fetch(f"{BASE_URL}/api/system/diagnostics")
    if not data:
        return False
    
    assert data.get("ok") is True, "Expected ok: True"
    assert "status" in data, "Missing 'status' field in diagnostics"
    assert data["status"] in ["ok", "degraded", "failed"], f"Invalid status: {data['status']}"
    assert "timestamp" in data, "Missing 'timestamp'"
    print(f"✅ System diagnostics passed! Status: {data['status']}")
    return True

def test_runtime_snapshot():
    print(f"\n--- Testing /api/ops/runtime_snapshot ---")
    data = fetch(f"{BASE_URL}/api/ops/runtime_snapshot")
    if not data:
        return False
    
    assert data.get("ok") is True, "Expected ok: True"
    assert "router_state" in data, "Missing router_state"
    assert "tier_state" in data, "Missing tier_state"
    assert "breaker_state" in data, "Missing breaker_state"
    assert "queue_depth" in data, "Missing queue_depth"
    assert "observability" in data, "Missing observability data"
    
    obs = data["observability"]
    assert "metrics" in obs, "Missing observability metrics"
    assert "timeline_tail" in obs, "Missing observability timeline_tail"
    
    print(f"✅ Runtime snapshot passed! Active tier: {data['router_state'].get('active_tier')}")
    return True

def test_metrics():
    print(f"\n--- Testing /api/ops/metrics ---")
    data = fetch(f"{BASE_URL}/api/ops/metrics")
    if not data:
        return False
        
    assert data.get("ok") is True, "Expected ok: True"
    assert "metrics" in data, "Missing metrics object"
    
    print(f"✅ Metrics endpoint passed!")
    return True
    
def test_timeline():
    print(f"\n--- Testing /api/ops/timeline ---")
    data = fetch(f"{BASE_URL}/api/ops/timeline")
    if not data:
        return False
        
    assert data.get("ok") is True, "Expected ok: True"
    assert "events" in data, "Missing events array"
    assert isinstance(data["events"], list), "Events is not a list"
    
    print(f"✅ Timeline endpoint passed! Events count: {len(data['events'])}")
    return True

def main():
    print("🔍 Starting Observability Smoke Tests against active server...")
    
    tests = [
        test_system_diagnostics,
        test_runtime_snapshot,
        test_metrics,
        test_timeline
    ]
    
    all_passed = True
    for test in tests:
        try:
            if not test():
                all_passed = False
        except AssertionError as e:
            print(f"❌ Test Failed: {e}")
            all_passed = False
        except Exception as e:
            print(f"❌ Unexpected Error: {e}")
            all_passed = False
            
    if all_passed:
        print("\n🎉 All observability tests passed securely!")
        sys.exit(0)
    else:
        print("\n💥 Some tests failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
