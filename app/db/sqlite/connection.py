"""
SQLite database connection and models
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class SQLiteConnection:
    """SQLite database connection manager"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or settings.SQLITE_DB_PATH
        self._ensure_db_exists()
    
    def _ensure_db_exists(self):
        """Ensure database directory exists"""
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
    
    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()
    
    def init_schema(self):
        """Initialize database schema"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Launches table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS launches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    location TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    elevation INTEGER NOT NULL,
                    description TEXT,
                    preferred_wind_directions TEXT,
                    webcam_urls TEXT,
                    active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """)
            
            # Weather stations table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    station_id TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    name TEXT NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    elevation INTEGER,
                    active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP,
                    UNIQUE(station_id, source)
                )
            """)
            
            # Rules table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    launch_id INTEGER NOT NULL,
                    rule_type TEXT NOT NULL,
                    station_id TEXT,
                    operator TEXT NOT NULL,
                    threshold_value REAL NOT NULL,
                    threshold_value_max REAL,
                    severity TEXT NOT NULL,
                    priority INTEGER DEFAULT 1,
                    active BOOLEAN DEFAULT 1,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP,
                    FOREIGN KEY (launch_id) REFERENCES launches(id) ON DELETE CASCADE
                )
            """)
            
            # Launch-Station associations
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS launch_stations (
                    launch_id INTEGER NOT NULL,
                    station_id TEXT NOT NULL,
                    priority INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (launch_id, station_id),
                    FOREIGN KEY (launch_id) REFERENCES launches(id) ON DELETE CASCADE
                )
            """)
            
            # Create indices
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_stations_source 
                ON stations(source)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_rules_launch 
                ON rules(launch_id)
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_launch_stations_launch 
                ON launch_stations(launch_id)
            """)
            
            conn.commit()
            logger.info("SQLite schema initialized successfully")


# Global instance
db = SQLiteConnection()
