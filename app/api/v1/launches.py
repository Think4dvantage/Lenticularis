"""
Launch management API endpoints
"""
from fastapi import APIRouter, HTTPException, status
from typing import List
from datetime import datetime

from app.models import Launch, LaunchCreate, LaunchUpdate
from app.db.sqlite.connection import db

router = APIRouter()


@router.get("/", response_model=List[Launch])
async def list_launches():
    """Get all launches"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM launches WHERE active = 1 ORDER BY name")
        rows = cursor.fetchall()
        
        launches = []
        for row in rows:
            launches.append(Launch(
                id=row["id"],
                name=row["name"],
                location=row["location"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                elevation=row["elevation"],
                description=row["description"],
                preferred_wind_directions=row["preferred_wind_directions"],
                webcam_urls=row["webcam_urls"],
                active=bool(row["active"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None
            ))
        
        return launches


@router.post("/", response_model=Launch, status_code=status.HTTP_201_CREATED)
async def create_launch(launch: LaunchCreate):
    """Create a new launch site"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO launches (name, location, latitude, longitude, elevation, 
                                description, preferred_wind_directions, webcam_urls, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            launch.name, launch.location, launch.latitude, launch.longitude,
            launch.elevation, launch.description, launch.preferred_wind_directions,
            launch.webcam_urls, launch.active
        ))
        
        launch_id = cursor.lastrowid
        conn.commit()
        
        # Fetch created launch
        cursor.execute("SELECT * FROM launches WHERE id = ?", (launch_id,))
        row = cursor.fetchone()
        
        return Launch(
            id=row["id"],
            name=row["name"],
            location=row["location"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            elevation=row["elevation"],
            description=row["description"],
            preferred_wind_directions=row["preferred_wind_directions"],
            webcam_urls=row["webcam_urls"],
            active=bool(row["active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=None
        )


@router.get("/{launch_id}", response_model=Launch)
async def get_launch(launch_id: int):
    """Get a specific launch"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM launches WHERE id = ? AND active = 1", (launch_id,))
        row = cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Launch not found")
        
        return Launch(
            id=row["id"],
            name=row["name"],
            location=row["location"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            elevation=row["elevation"],
            description=row["description"],
            preferred_wind_directions=row["preferred_wind_directions"],
            webcam_urls=row["webcam_urls"],
            active=bool(row["active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None
        )


@router.put("/{launch_id}", response_model=Launch)
async def update_launch(launch_id: int, launch: LaunchUpdate):
    """Update a launch"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Check if exists
        cursor.execute("SELECT id FROM launches WHERE id = ?", (launch_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Launch not found")
        
        # Build update query dynamically
        updates = []
        values = []
        
        for field, value in launch.dict(exclude_unset=True).items():
            updates.append(f"{field} = ?")
            values.append(value)
        
        if updates:
            updates.append("updated_at = ?")
            values.append(datetime.utcnow().isoformat())
            values.append(launch_id)
            
            query = f"UPDATE launches SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, values)
            conn.commit()
        
        # Return updated launch
        return await get_launch(launch_id)


@router.delete("/{launch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_launch(launch_id: int):
    """Delete a launch (soft delete)"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE launches SET active = 0, updated_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), launch_id)
        )
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Launch not found")
        
        conn.commit()
