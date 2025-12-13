"""
Database initialization script
"""
import sys
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.sqlite.connection import db

logger = setup_logging()


def init_database():
    """Initialize the SQLite database schema"""
    try:
        logger.info("Initializing database...")
        db.init_schema()
        logger.info("Database initialized successfully!")
        logger.info(f"Database location: {settings.SQLITE_DB_PATH}")
        
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


if __name__ == "__main__":
    init_database()
