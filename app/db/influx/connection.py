"""
InfluxDB connection and client
"""
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from typing import List, Dict, Any
from datetime import datetime
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class InfluxConnection:
    """InfluxDB connection manager"""
    
    def __init__(self):
        self.url = settings.INFLUXDB_URL
        self.token = settings.INFLUXDB_TOKEN
        self.org = settings.INFLUXDB_ORG
        self.bucket = settings.INFLUXDB_BUCKET
        self._client = None
        self._write_api = None
        self._query_api = None
    
    @property
    def client(self) -> InfluxDBClient:
        """Lazy initialization of client"""
        if self._client is None:
            self._client = InfluxDBClient(
                url=self.url,
                token=self.token,
                org=self.org
            )
            logger.info(f"Connected to InfluxDB at {self.url}")
        return self._client
    
    @property
    def write_api(self):
        """Get write API"""
        if self._write_api is None:
            self._write_api = self.client.write_api(write_options=SYNCHRONOUS)
        return self._write_api
    
    @property
    def query_api(self):
        """Get query API"""
        if self._query_api is None:
            self._query_api = self.client.query_api()
        return self._query_api
    
    def write_weather_data(self, measurements: List[Dict[str, Any]]):
        """
        Write weather measurements to InfluxDB
        
        Args:
            measurements: List of normalized weather data dictionaries
        """
        points = []
        
        for m in measurements:
            point = Point("weather_data") \
                .tag("station_id", m["station_id"]) \
                .tag("source", m["source"])
            
            # Add fields
            if "wind_speed" in m and m["wind_speed"] is not None:
                point.field("wind_speed", float(m["wind_speed"]))
            if "wind_direction" in m and m["wind_direction"] is not None:
                point.field("wind_direction", int(m["wind_direction"]))
            if "gust_speed" in m and m["gust_speed"] is not None:
                point.field("gust_speed", float(m["gust_speed"]))
            if "gust_direction" in m and m["gust_direction"] is not None:
                point.field("gust_direction", int(m["gust_direction"]))
            if "temperature" in m and m["temperature"] is not None:
                point.field("temperature", float(m["temperature"]))
            if "humidity" in m and m["humidity"] is not None:
                point.field("humidity", float(m["humidity"]))
            if "pressure" in m and m["pressure"] is not None:
                point.field("pressure", float(m["pressure"]))
            if "rain" in m and m["rain"] is not None:
                point.field("rain", float(m["rain"]))
            
            # Set timestamp
            if "timestamp" in m:
                point.time(m["timestamp"], WritePrecision.S)
            
            points.append(point)
        
        if points:
            self.write_api.write(bucket=self.bucket, org=self.org, record=points)
            logger.info(f"Wrote {len(points)} weather measurements to InfluxDB")
    
    def write_decision(self, launch_id: int, status: str, factors: Dict[str, Any], message: str = None):
        """
        Write launch decision to InfluxDB
        
        Args:
            launch_id: Launch site ID
            status: Decision status (green/orange/red)
            factors: Contributing factors dictionary
            message: Optional message
        """
        point = Point("launch_decisions") \
            .tag("launch_id", str(launch_id)) \
            .tag("status", status) \
            .field("launch_id_int", launch_id)
        
        if message:
            point.field("message", message)
        
        # Add contributing factors as fields
        for key, value in factors.items():
            if isinstance(value, (int, float)):
                point.field(f"factor_{key}", float(value))
            elif isinstance(value, str):
                point.field(f"factor_{key}", value)
        
        self.write_api.write(bucket=self.bucket, org=self.org, record=point)
        logger.info(f"Wrote decision for launch {launch_id}: {status}")
    
    def query_latest_weather(self, station_id: str, hours: int = 1) -> List[Dict[str, Any]]:
        """
        Query latest weather data for a station
        
        Args:
            station_id: Station identifier
            hours: Number of hours to look back
        
        Returns:
            List of weather measurements
        """
        query = f'''
        from(bucket: "{self.bucket}")
            |> range(start: -{hours}h)
            |> filter(fn: (r) => r["_measurement"] == "weather_data")
            |> filter(fn: (r) => r["station_id"] == "{station_id}")
            |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
        '''
        
        tables = self.query_api.query(query, org=self.org)
        
        results = []
        for table in tables:
            for record in table.records:
                results.append(record.values)
        
        return results
    
    def close(self):
        """Close the client connection"""
        if self._client:
            self._client.close()
            logger.info("InfluxDB connection closed")


# Global instance
influx = InfluxConnection()
