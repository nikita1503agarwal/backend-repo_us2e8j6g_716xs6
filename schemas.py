"""
Database Schemas for Futsal Leaderboard App

Each Pydantic model corresponds to a MongoDB collection. The collection name is the lowercase of the class name.

Use these models for validation in API endpoints and to keep a clear contract of stored data.
"""
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from datetime import datetime

# Core domain schemas

class Team(BaseModel):
    name: str = Field(..., description="Team name")
    country: str = Field(..., description="Country where the team is based")
    city: str = Field(..., description="City where the team is based")
    coach: Optional[str] = Field(None, description="Coach name")
    logo_url: Optional[str] = Field(None, description="Logo image URL")

class Player(BaseModel):
    name: str = Field(..., description="Full name")
    position: Literal["GK","DEF","MID","FWD"] = Field(..., description="Primary position")
    team_id: Optional[str] = Field(None, description="Team ObjectId as string. Can be null if free agent")
    number: Optional[int] = Field(None, ge=0, le=99, description="Jersey number")
    country: Optional[str] = Field(None, description="Player nationality")
    city: Optional[str] = Field(None, description="City for regional leaderboards")
    avatar_url: Optional[str] = Field(None, description="Avatar image URL")

class MatchEvent(BaseModel):
    # stored embedded inside Match.events
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    type: Literal["goal","assist","yellow","red","own_goal","substitution"]
    team_id: Optional[str] = Field(None, description="Team involved in the event")
    player_id: Optional[str] = Field(None, description="Primary player involved (e.g., goal scorer)")
    secondary_player_id: Optional[str] = Field(None, description="Secondary player (e.g., assister)")
    minute: Optional[int] = Field(None, ge=0, le=60, description="Minute mark in futsal match (typ. 2x20)")
    notes: Optional[str] = None

class Match(BaseModel):
    home_team_id: str
    away_team_id: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    events: List[MatchEvent] = Field(default_factory=list)
    home_score: int = 0
    away_score: int = 0
    winner_team_id: Optional[str] = None  # null for draw

# Optional: simple formation model that can be saved per team
class Formation(BaseModel):
    team_id: str
    name: str = Field("Default", description="Formation name, e.g., 2-2, 3-1")
    # positions as percentage coordinates on the field for 5 players + GK
    positions: List[dict] = Field(
        ..., description="List of {player_id, x, y} where x,y are 0..100 percentage coordinates"
    )

# Keep a lightweight schema endpoint compatible structure in main.py will introspect these
