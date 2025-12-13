import requests
import json
import sys

#Library with all needed Endpoints deliverying the needed Measurements
SOURCES = {
        "speed": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-windgeschwindigkeit-kmh-10min/ch.meteoschweiz.messwerte-windgeschwindigkeit-kmh-10min_en.json",
        "gusts":"https://data.geo.admin.ch/ch.meteoschweiz.messwerte-wind-boeenspitze-kmh-10min/ch.meteoschweiz.messwerte-wind-boeenspitze-kmh-10min_en.json",
        "temperature":"https://data.geo.admin.ch/ch.meteoschweiz.messwerte-lufttemperatur-10min/ch.meteoschweiz.messwerte-lufttemperatur-10min_en.json",
        "humidity":"https://data.geo.admin.ch/ch.meteoschweiz.messwerte-luftfeuchtigkeit-10min/ch.meteoschweiz.messwerte-luftfeuchtigkeit-10min_en.json",
        "pressure":"https://data.geo.admin.ch/ch.meteoschweiz.messwerte-luftdruck-qff-10min/ch.meteoschweiz.messwerte-luftdruck-qff-10min_en.json"
    }

def fetch_all_measurements(SOURCES):
    # Headers to not be blocked by WAF
    headers = {
        "User-Agent": "Lenticularis/0.1 (Linux Alpine 3.23; x64)"
    }

    RAW_DATA = {
        "speed":requests.get(SOURCES["speed"], headers=headers),
        "gusts":requests.get(SOURCES["gusts"], headers=headers),
        "temp":requests.get(SOURCES["temperature"], headers=headers),
        "humi":requests.get(SOURCES["humidity"], headers=headers),
        "press":requests.get(SOURCES["pressure"], headers=headers)
    }
    


def inspect_raw_data():

    {
    "station_id": "INT",       # String
    "source": "meteoswiss",    # String (To track where it came from)
    "timestamp": "...",        # ISO Format String
    "wind_speed": 15.5,        # Float (km/h) - Note: Holfuy sends m/s!
    "wind_gust": 28.0,         # Float (km/h)
    "wind_dir": 260,           # Int (Degrees)
    "temp": 12.0,              # Float (Celsius)
    "humidity": 65,            # Float (%)
    "pressure": 1013,          # Float (hPa)
}

    

    target_url = SOURCES["speed"]
    print(f"Fetching raw data from: {target_url}")
    
    response = requests.get(target_url, headers=headers)

    # --- SAFETY CHECK ---
    if response.status_code != 200:
        print(f"CRITICAL ERROR: Server returned status {response.status_code}")
        # Print a bit less text this time to keep it clean
        print("Response:", response.text[:200]) 
        sys.exit(1)

    try:
        data = response.json()
    except json.JSONDecodeError:
        print("Error: The data returned was not valid JSON.")
        sys.exit(1)
    
    # Success! Let's print the structure.
    print(f"\nRoot Keys: {list(data.keys())}")
    
    if 'features' in data and len(data['features']) > 0:
        # Get the first station
        first_station = data['features'][0]
        
        # We only want to see the essential fields, let's filter the output slightly
        # so it's not a huge wall of text
        simplified_view = {
            "id": first_station.get("id"),
            "station_name": first_station.get("properties", {}).get("station_name"),
            "value": first_station.get("properties", {}).get("value"),
            "unit": "km/h"
        }
        
        print("\n--- RAW DATA SAMPLE (First Station) ---")
        print(json.dumps(first_station, indent=4))
        print("\n--- WHAT WE LIKELY WANT ---")
        print(json.dumps(simplified_view, indent=4))
        print("---------------------------------------")
    else:
        print("Warning: No 'features' found in the data.")

if __name__ == "__main__":
    inspect_raw_data()