"""
Test collector functionality
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from collectors.sources.meteoswiss import MeteoSwissCollector
from collectors.sources.holfuy import HolfuyCollector


def test_meteoswiss():
    """Test MeteoSwiss collector"""
    print("Testing MeteoSwiss collector...")
    print("-" * 50)
    
    collector = MeteoSwissCollector()
    try:
        data = collector.collect()
        print(f"✓ Successfully collected {len(data)} stations")
        
        if data:
            print("\nSample station:")
            print(json.dumps(data[0], indent=2, default=str))
        
        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_holfuy():
    """Test Holfuy collector"""
    print("\n\nTesting Holfuy collector...")
    print("-" * 50)
    
    # Test with example data
    collector = HolfuyCollector()
    test_data = {
        "101": {
            "stationId": 101,
            "stationName": "TestStation",
            "dateTime": "2025-12-13 14:30:00",
            "wind": {
                "speed": 5.5,
                "gust": 8.2,
                "direction": 270
            },
            "humidity": 65.0,
            "pressure": 1013.25,
            "temperature": 12.5
        }
    }
    
    try:
        normalized = collector.normalize_data(test_data)
        print(f"✓ Successfully normalized test data")
        print("\nNormalized output:")
        print(json.dumps(normalized, indent=2, default=str))
        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("Lenticularis Collector Tests")
    print("=" * 50)
    
    results = []
    results.append(("MeteoSwiss", test_meteoswiss()))
    results.append(("Holfuy", test_holfuy()))
    
    print("\n" + "=" * 50)
    print("Test Summary")
    print("=" * 50)
    
    for name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{name:20} {status}")
