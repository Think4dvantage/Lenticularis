"""
MeteoSwiss weather data collector
Fetches data from Swiss Federal Office of Meteorology and Climatology
"""
import requests
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from collectors.base import BaseCollector, CollectorError


class MeteoSwissCollector(BaseCollector):
    """
    Collector for MeteoSwiss weather stations
    
    Fetches data from public APIs:
    - Wind speed and gusts
    - Temperature
    - Humidity
    - Pressure
    """
    
    SOURCES = {
        "speed": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-windgeschwindigkeit-kmh-10min/ch.meteoschweiz.messwerte-windgeschwindigkeit-kmh-10min_en.json",
        "gusts": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-wind-boeenspitze-kmh-10min/ch.meteoschweiz.messwerte-wind-boeenspitze-kmh-10min_en.json",
        "temperature": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-lufttemperatur-10min/ch.meteoschweiz.messwerte-lufttemperatur-10min_en.json",
        "humidity": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-luftfeuchtigkeit-10min/ch.meteoschweiz.messwerte-luftfeuchtigkeit-10min_en.json",
        "pressure": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-luftdruck-qff-10min/ch.meteoschweiz.messwerte-luftdruck-qff-10min_en.json",
        "wind_direction": "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-windrichtung-10min/ch.meteoschweiz.messwerte-windrichtung-10min_en.json"
    }
    
    def __init__(self):
        super().__init__("meteoswiss")
        self.headers = {
            "User-Agent": "Lenticularis/0.1 (Weather forecasting tool for paragliding)"
        }
    
    def fetch_data(self, station_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Fetch raw data from all MeteoSwiss endpoints
        
        Returns:
            Dictionary with measurement types as keys and API responses as values
        """
        raw_data = {}
        
        for measurement_type, url in self.SOURCES.items():
            try:
                self.logger.debug(f"Fetching {measurement_type} from MeteoSwiss")
                response = requests.get(url, headers=self.headers, timeout=30)
                
                if response.status_code != 200:
                    self.logger.error(f"HTTP {response.status_code} for {measurement_type}")
                    continue
                
                data = response.json()
                raw_data[measurement_type] = data
                
            except requests.RequestException as e:
                self.logger.error(f"Request failed for {measurement_type}: {e}")
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON for {measurement_type}: {e}")
        
        if not raw_data:
            raise CollectorError("Failed to fetch any data from MeteoSwiss")
        
        return raw_data
    
    def normalize_data(self, raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Normalize MeteoSwiss data to standard format
        
        Combines data from different endpoints by station_id
        """
        # Build a dictionary of stations with all their measurements
        stations = {}
        
        # Process each measurement type
        for measurement_type, data in raw_data.items():
            if "features" not in data:
                continue
            
            for feature in data["features"]:
                try:
                    station_id = feature.get("id")
                    if not station_id:
                        continue
                    
                    props = feature.get("properties", {})
                    
                    # Initialize station entry if needed
                    if station_id not in stations:
                        stations[station_id] = {
                            "station_id": station_id,
                            "source": self.source_name,
                            "station_name": props.get("station_name", "Unknown"),
                            "timestamp": None
                        }
                    
                    # Add measurement data
                    value = props.get("value")
                    if value is None:
                        continue
                    
                    # Convert values based on type
                    if measurement_type == "speed":
                        stations[station_id]["wind_speed"] = self.kmh_to_ms(value)
                    elif measurement_type == "gusts":
                        stations[station_id]["gust_speed"] = self.kmh_to_ms(value)
                    elif measurement_type == "wind_direction":
                        stations[station_id]["wind_direction"] = int(value)
                    elif measurement_type == "temperature":
                        stations[station_id]["temperature"] = float(value)
                    elif measurement_type == "humidity":
                        stations[station_id]["humidity"] = float(value)
                    elif measurement_type == "pressure":
                        stations[station_id]["pressure"] = float(value)
                    
                    # Use reference_ts for timestamp
                    if "reference_ts" in props and not stations[station_id]["timestamp"]:
                        try:
                            # Parse timestamp: "2025-12-13T14:30:00Z"
                            ts_str = props["reference_ts"]
                            stations[station_id]["timestamp"] = datetime.fromisoformat(
                                ts_str.replace("Z", "+00:00")
                            )
                        except (ValueError, AttributeError) as e:
                            self.logger.warning(f"Failed to parse timestamp for {station_id}: {e}")
                
                except Exception as e:
                    self.logger.warning(f"Error processing feature: {e}")
                    continue
        
        # Convert to list and add current timestamp if none available
        normalized = []
        for station_data in stations.values():
            if station_data["timestamp"] is None:
                station_data["timestamp"] = datetime.utcnow()
            
            normalized.append(station_data)
        
        return normalized


# For backwards compatibility and testing
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    collector = MeteoSwissCollector()
    try:
        data = collector.collect()
        print(f"Collected {len(data)} stations")
        if data:
            print("\nSample station:")
            print(json.dumps(data[0], indent=2, default=str))
    except CollectorError as e:
        print(f"Error: {e}")
