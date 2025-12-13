"""
Utility to seed weather stations into the database
This can be used to populate all MeteoSwiss stations
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from app.core.logging import setup_logging
from app.db.sqlite.connection import db
from collectors.sources.meteoswiss import MeteoSwissCollector

logger = setup_logging()


def seed_meteoswiss_stations():
    """Fetch and seed all MeteoSwiss stations"""
    try:
        logger.info("Fetching MeteoSwiss stations...")
        
        collector = MeteoSwissCollector()
        data = collector.collect()
        
        logger.info(f"Found {len(data)} MeteoSwiss stations")
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            added = 0
            updated = 0
            
            for station in data:
                station_id = station["station_id"]
                source = station["source"]
                name = station.get("station_name", f"Station-{station_id}")
                
                # Check if station exists
                cursor.execute(
                    "SELECT id FROM stations WHERE station_id = ? AND source = ?",
                    (station_id, source)
                )
                existing = cursor.fetchone()
                
                if existing:
                    # Update
                    cursor.execute("""
                        UPDATE stations 
                        SET name = ?, updated_at = ?
                        WHERE station_id = ? AND source = ?
                    """, (name, datetime.utcnow().isoformat(), station_id, source))
                    updated += 1
                else:
                    # Insert
                    cursor.execute("""
                        INSERT INTO stations (station_id, source, name, active)
                        VALUES (?, ?, ?, 1)
                    """, (station_id, source, name))
                    added += 1
            
            conn.commit()
            
            logger.info(f"Seeding complete: {added} added, {updated} updated")
            logger.info(f"Total stations in database: {added + updated}")
    
    except Exception as e:
        logger.error(f"Failed to seed stations: {e}")
        raise


if __name__ == "__main__":
    seed_meteoswiss_stations()
