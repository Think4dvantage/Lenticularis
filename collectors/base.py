"""
Base collector interface for weather data sources
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """
    Abstract base class for all weather data collectors
    
    Each collector must implement:
    - fetch_data(): Get raw data from source
    - normalize_data(): Convert to standard format
    """
    
    def __init__(self, source_name: str):
        self.source_name = source_name
        self.logger = logging.getLogger(f"{__name__}.{source_name}")
    
    @abstractmethod
    def fetch_data(self, station_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Fetch raw data from the weather source
        
        Args:
            station_ids: Optional list of specific stations to fetch. If None, fetch all.
        
        Returns:
            Raw data from the source API
        
        Raises:
            CollectorError: If data fetching fails
        """
        pass
    
    @abstractmethod
    def normalize_data(self, raw_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Normalize raw data to standard format
        
        Standard format:
        {
            "station_id": str,
            "source": str,
            "timestamp": datetime,
            "wind_speed": float (m/s),
            "wind_direction": int (degrees),
            "gust_speed": float (m/s),
            "gust_direction": int (degrees),
            "temperature": float (Celsius),
            "humidity": float (%),
            "pressure": float (hPa),
            "rain": float (mm)
        }
        
        Args:
            raw_data: Raw data from fetch_data()
        
        Returns:
            List of normalized measurement dictionaries
        """
        pass
    
    def collect(self, station_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Main collection method - fetches and normalizes data
        
        Args:
            station_ids: Optional list of specific stations
        
        Returns:
            List of normalized weather measurements
        """
        try:
            self.logger.info(f"Starting data collection from {self.source_name}")
            raw_data = self.fetch_data(station_ids)
            normalized = self.normalize_data(raw_data)
            self.logger.info(f"Successfully collected {len(normalized)} measurements from {self.source_name}")
            return normalized
        except Exception as e:
            self.logger.error(f"Error collecting data from {self.source_name}: {e}")
            raise CollectorError(f"Collection failed for {self.source_name}: {e}")
    
    @staticmethod
    def kmh_to_ms(kmh: float) -> float:
        """Convert km/h to m/s"""
        return kmh / 3.6
    
    @staticmethod
    def ms_to_kmh(ms: float) -> float:
        """Convert m/s to km/h"""
        return ms * 3.6


class CollectorError(Exception):
    """Custom exception for collector errors"""
    pass
