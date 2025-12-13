"""
Holfuy weather station collector
Fetches data from Holfuy mountain weather stations
"""
import requests
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from collectors.base import BaseCollector, CollectorError


class HolfuyCollector(BaseCollector):
    """
    Collector for Holfuy weather stations
    
    Note: Holfuy may require API credentials for full access
    Provides wind, temperature, pressure, and humidity data
    """
    
    # API endpoint (may require authentication)
    BASE_URL = "https://api.holfuy.com/live/"
    
    def __init__(self, api_key: Optional[str] = None):
        super().__init__("holfuy")
        self.api_key = api_key
        
        if not api_key:
            self.logger.warning("No Holfuy API key provided - data access may be limited")
    
    def fetch_data(self, station_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Fetch raw data from Holfuy API
        
        Args:
            station_ids: List of Holfuy station IDs to fetch
        
        Returns:
            Raw API response data
        """
        if not station_ids:
            raise CollectorError("Holfuy requires specific station IDs to be provided")
        
        all_data = {}
        
        for station_id in station_ids:
            try:
                # Build request URL
                params = {"s": station_id}
                if self.api_key:
                    params["pw"] = self.api_key
                
                self.logger.debug(f"Fetching Holfuy station {station_id}")
                response = requests.get(
                    f"{self.BASE_URL}",
                    params=params,
                    timeout=30
                )
                
                if response.status_code != 200:
                    self.logger.error(f"HTTP {response.status_code} for station {station_id}")
                    continue
                
                data = response.json()
                all_data[station_id] = data
                
            except requests.RequestException as e:
                self.logger.error(f"Request failed for station {station_id}: {e}")
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON for station {station_id}: {e}")
        
        if not all_data:
            raise CollectorError("Failed to fetch any data from Holfuy")
        
        return all_data
    
    def normalize_data(self, raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Normalize Holfuy data to standard format
        
        Holfuy example format:
        {
            "stationId": 101,
            "stationName": "TestStation",
            "dateTime": "2025-12-12 14:44:42",
            "wind": {
                "speed": 5.5,
                "gust": 8.2,
                "min": 3.1,
                "unit": "m/s",
                "direction": 250
            },
            "humidity": 77.2,
            "pressure": 1026,
            "rain": 0,
            "temperature": 6
        }
        """
        normalized = []
        
        for station_id, data in raw_data.items():
            try:
                # Parse timestamp
                timestamp_str = data.get("dateTime", "")
                try:
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    timestamp = datetime.utcnow()
                
                # Extract wind data
                wind = data.get("wind", {})
                
                # Build normalized record
                record = {
                    "station_id": str(data.get("stationId", station_id)),
                    "source": self.source_name,
                    "station_name": data.get("stationName", f"Holfuy-{station_id}"),
                    "timestamp": timestamp,
                }
                
                # Add measurements (Holfuy already uses m/s)
                if "speed" in wind and wind["speed"] is not None:
                    record["wind_speed"] = float(wind["speed"])
                if "gust" in wind and wind["gust"] is not None:
                    record["gust_speed"] = float(wind["gust"])
                if "direction" in wind and wind["direction"] is not None:
                    record["wind_direction"] = int(wind["direction"])
                
                if "temperature" in data and data["temperature"] is not None:
                    record["temperature"] = float(data["temperature"])
                if "humidity" in data and data["humidity"] is not None:
                    record["humidity"] = float(data["humidity"])
                if "pressure" in data and data["pressure"] is not None:
                    record["pressure"] = float(data["pressure"])
                if "rain" in data and data["rain"] is not None:
                    record["rain"] = float(data["rain"])
                
                normalized.append(record)
                
            except Exception as e:
                self.logger.warning(f"Error normalizing Holfuy station {station_id}: {e}")
                continue
        
        return normalized


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # Example usage
    collector = HolfuyCollector()
    
    # Test with example data (since we don't have real API access yet)
    test_data = {
        "101": {
            "stationId": 101,
            "stationName": "TestStation",
            "dateTime": "2025-12-12 14:44:42",
            "wind": {
                "speed": 5.5,
                "gust": 8.2,
                "direction": 250
            },
            "humidity": 77.2,
            "pressure": 1026,
            "temperature": 6
        }
    }
    
    normalized = collector.normalize_data(test_data)
    print("Normalized Holfuy data:")
    print(json.dumps(normalized, indent=2, default=str))
