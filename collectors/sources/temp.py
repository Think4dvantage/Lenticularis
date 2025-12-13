import requests
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# --- CONFIGURATION ---
SOURCES = {
    "speed": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-windgeschwindigkeit-kmh-10min/ch.meteoschweiz.messwerte-windgeschwindigkeit-kmh-10min_en.json",
    "gusts": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-wind-boeenspitze-kmh-10min/ch.meteoschweiz.messwerte-wind-boeenspitze-kmh-10min_en.json",
    "temperature": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-lufttemperatur-10min/ch.meteoschweiz.messwerte-lufttemperatur-10min_en.json",
    "humidity": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-luftfeuchtigkeit-10min/ch.meteoschweiz.messwerte-luftfeuchtigkeit-10min_en.json",
    "pressure": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-luftdruck-qff-10min/ch.meteoschweiz.messwerte-luftdruck-qff-10min_en.json"
}

HEADERS = {"User-Agent": "Lenticularis/0.1 (Linux Alpine 3.23; x64)"}

def fetch_url(key, url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        return key, resp.json()
    except Exception as e:
        print(f"Error fetching {key}: {e}")
        return key, None

def main():
    # 1. FETCH DATA
    raw_data = {}
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        futures = [executor.submit(fetch_url, k, u) for k, u in SOURCES.items()]
        for future in futures:
            key, data = future.result()
            if data:
                raw_data[key] = data

    # 2. MERGE DATA
    merged_stations = {}

    for measure_type, content in raw_data.items():
        if 'features' not in content:
            continue
            
        for item in content['features']:
            station_id = item['id']
            props = item['properties']
            
            if station_id not in merged_stations:
                merged_stations[station_id] = {
                    "station_id": station_id,
                    "source": "meteoswiss",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "wind_speed": None,
                    "gust_speed": None,
                    "wind_dir": None,
                    "gust_dir": None,
                    "temp": None,
                    "humidity": None,
                    "pressure": None
                }

            if measure_type == "speed":
                merged_stations[station_id]["wind_speed"] = props.get("value")
                merged_stations[station_id]["wind_dir"] = props.get("wind_direction")
                
            elif measure_type == "gusts":
                merged_stations[station_id]["gust_speed"] = props.get("value")
                merged_stations[station_id]["gust_dir"] = props.get("wind_direction")
                
            elif measure_type == "temperature":
                merged_stations[station_id]["temp"] = props.get("value")
                
            elif measure_type == "humidity":
                merged_stations[station_id]["humidity"] = props.get("value")
                
            elif measure_type == "pressure":
                merged_stations[station_id]["pressure"] = props.get("value")

    # 3. FILTER AND OUTPUT
    # Only keep stations where BOTH wind_speed and gust_speed have values
    valid_stations = [
        station for station in merged_stations.values()
        if station["wind_speed"] is not None and station["gust_speed"] is not None
    ]

    print(json.dumps(valid_stations, indent=2))
    print(f"\n# Total valid wind stations: {len(valid_stations)}")

if __name__ == "__main__":
    main()