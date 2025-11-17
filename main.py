import os
from datetime import datetime
from typing import List, Optional, Literal, Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import db, create_document, get_documents
from schemas import Team as TeamSchema, Player as PlayerSchema, MatchEvent as MatchEventSchema, Match as MatchSchema, Formation as FormationSchema

# ----------------------
# FastAPI app + CORS
# ----------------------
app = FastAPI(title="Futsal Leaderboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------
# Helpers for Mongo ObjectId and serialization
# ----------------------
from bson import ObjectId

def to_object_id(id_str: Optional[str]) -> Optional[ObjectId]:
    if id_str is None:
        return None
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid id: {id_str}")


def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    d = dict(doc)
    if d.get("_id"):
        d["id"] = str(d.pop("_id"))
    # convert nested ids in events
    if "events" in d and isinstance(d["events"], list):
        for ev in d["events"]:
            if isinstance(ev, dict):
                if ev.get("player_id") and isinstance(ev["player_id"], ObjectId):
                    ev["player_id"] = str(ev["player_id"])
                if ev.get("secondary_player_id") and isinstance(ev["secondary_player_id"], ObjectId):
                    ev["secondary_player_id"] = str(ev["secondary_player_id"])
                if ev.get("team_id") and isinstance(ev["team_id"], ObjectId):
                    ev["team_id"] = str(ev["team_id"])
    for k, v in d.items():
        if isinstance(v, ObjectId):
            d[k] = str(v)
    return d

# ----------------------
# Basic endpoints
# ----------------------
@app.get("/")
def read_root():
    return {"message": "Futsal Leaderboard API is running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "collections": []
    }
    try:
        if db is None:
            response["database"] = "❌ Not Connected"
        else:
            response["database"] = "✅ Connected"
            response["collections"] = db.list_collection_names()
    except Exception as e:
        response["database"] = f"⚠️ Error: {str(e)[:80]}"
    return response

# Expose schemas (lightweight) so viewers can inspect
@app.get("/schema")
def get_schema():
    return {
        "team": TeamSchema.model_json_schema(),
        "player": PlayerSchema.model_json_schema(),
        "match": MatchSchema.model_json_schema(),
        "formation": FormationSchema.model_json_schema(),
    }

# ----------------------
# Teams
# ----------------------
class TeamCreate(TeamSchema):
    pass

@app.post("/teams")
def create_team(team: TeamCreate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    # unique by name within city + country
    existing = db.team.find_one({"name": team.name, "country": team.country, "city": team.city})
    if existing:
        raise HTTPException(status_code=400, detail="Team already exists in this city")
    team_id = create_document("team", team)
    doc = db.team.find_one({"_id": ObjectId(team_id)})
    return serialize_doc(doc)

@app.get("/teams")
def list_teams(country: Optional[str] = None, city: Optional[str] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    filt = {}
    if country:
        filt["country"] = country
    if city:
        filt["city"] = city
    teams = list(db.team.find(filt).sort("name", 1))
    return [serialize_doc(t) for t in teams]

# ----------------------
# Players
# ----------------------
class PlayerCreate(PlayerSchema):
    pass

@app.post("/players")
def create_player(player: PlayerCreate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    data = player.model_dump()
    if data.get("team_id"):
        data["team_id"] = to_object_id(data["team_id"])
    pid = db.player.insert_one({**data, "created_at": datetime.utcnow(), "updated_at": datetime.utcnow()}).inserted_id
    doc = db.player.find_one({"_id": pid})
    return serialize_doc(doc)

@app.get("/players")
def list_players(team_id: Optional[str] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    filt: Dict[str, Any] = {}
    if team_id:
        filt["team_id"] = to_object_id(team_id)
    players = list(db.player.find(filt).sort("name", 1))
    return [serialize_doc(p) for p in players]

# ----------------------
# Matches & Events
# ----------------------
class MatchStart(BaseModel):
    home_team_id: str
    away_team_id: str

@app.post("/matches/start")
def start_match(payload: MatchStart):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    match = {
        "home_team_id": to_object_id(payload.home_team_id),
        "away_team_id": to_object_id(payload.away_team_id),
        "started_at": datetime.utcnow(),
        "ended_at": None,
        "events": [],
        "home_score": 0,
        "away_score": 0,
        "winner_team_id": None,
    }
    mid = db.match.insert_one(match).inserted_id
    return {"match_id": str(mid), **serialize_doc(match | {"_id": mid})}

class EventCreate(BaseModel):
    type: Literal["goal","assist","yellow","red","own_goal","substitution"]
    team_id: Optional[str] = None
    player_id: Optional[str] = None
    secondary_player_id: Optional[str] = None
    minute: Optional[int] = Field(None, ge=0, le=60)
    notes: Optional[str] = None

@app.post("/matches/{match_id}/event")
def add_event(match_id: str, event: EventCreate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    m = db.match.find_one({"_id": to_object_id(match_id)})
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    ev = event.model_dump()
    if ev.get("team_id"):
        ev["team_id"] = to_object_id(ev["team_id"])
    if ev.get("player_id"):
        ev["player_id"] = to_object_id(ev["player_id"])
    if ev.get("secondary_player_id"):
        ev["secondary_player_id"] = to_object_id(ev["secondary_player_id"])
    ev["timestamp"] = datetime.utcnow()

    # Update score if goal/own_goal
    home_id = m["home_team_id"]
    away_id = m["away_team_id"]
    inc = {"events": ev, "updated_at": datetime.utcnow()}

    if event.type == "goal":
        if ev.get("team_id") == home_id:
            db.match.update_one({"_id": m["_id"]}, {"$inc": {"home_score": 1}, "$push": {"events": ev}})
        elif ev.get("team_id") == away_id:
            db.match.update_one({"_id": m["_id"]}, {"$inc": {"away_score": 1}, "$push": {"events": ev}})
        else:
            db.match.update_one({"_id": m["_id"]}, {"$push": {"events": ev}})
    elif event.type == "own_goal":
        if ev.get("team_id") == home_id:
            db.match.update_one({"_id": m["_id"]}, {"$inc": {"away_score": 1}, "$push": {"events": ev}})
        elif ev.get("team_id") == away_id:
            db.match.update_one({"_id": m["_id"]}, {"$inc": {"home_score": 1}, "$push": {"events": ev}})
        else:
            db.match.update_one({"_id": m["_id"]}, {"$push": {"events": ev}})
    else:
        db.match.update_one({"_id": m["_id"]}, {"$push": {"events": ev}})

    m2 = db.match.find_one({"_id": m["_id"]})
    return serialize_doc(m2)

@app.post("/matches/{match_id}/end")
def end_match(match_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    m = db.match.find_one({"_id": to_object_id(match_id)})
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    winner = None
    if m["home_score"] > m["away_score"]:
        winner = m["home_team_id"]
    elif m["away_score"] > m["home_score"]:
        winner = m["away_team_id"]
    db.match.update_one({"_id": m["_id"]}, {"$set": {"ended_at": datetime.utcnow(), "winner_team_id": winner}})
    m2 = db.match.find_one({"_id": m["_id"]})
    return serialize_doc(m2)

@app.get("/matches/{match_id}")
def get_match(match_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    m = db.match.find_one({"_id": to_object_id(match_id)})
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    return serialize_doc(m)

# ----------------------
# Formations
# ----------------------
class FormationSave(BaseModel):
    team_id: str
    name: str = "Default"
    positions: List[dict]

@app.get("/formations/{team_id}")
def get_formation(team_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    doc = db.formation.find_one({"team_id": to_object_id(team_id)})
    if not doc:
        return {"team_id": team_id, "name": "Default", "positions": []}
    return serialize_doc(doc)

@app.post("/formations")
def save_formation(payload: FormationSave):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    data = payload.model_dump()
    data["team_id"] = to_object_id(data["team_id"])
    data["updated_at"] = datetime.utcnow()
    existing = db.formation.find_one({"team_id": data["team_id"]})
    if existing:
        db.formation.update_one({"_id": existing["_id"]}, {"$set": data})
        saved = db.formation.find_one({"_id": existing["_id"]})
    else:
        data["created_at"] = datetime.utcnow()
        _id = db.formation.insert_one(data).inserted_id
        saved = db.formation.find_one({"_id": _id})
    return serialize_doc(saved)

# ----------------------
# Leaderboards (teams & players)
# ----------------------
@app.get("/leaderboard/teams")
def leaderboard_teams(
    scope: Literal["global","country","city"] = "global",
    country: Optional[str] = None,
    city: Optional[str] = None,
    stat: Literal["goals","wins","points"] = "goals",
    limit: int = 20,
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    match_filter: Dict[str, Any] = {}
    team_filter: Dict[str, Any] = {}
    if scope in ("country", "city"):
        if country:
            team_filter["country"] = country
        if scope == "city" and city:
            team_filter["city"] = city
        team_ids = [t["_id"] for t in db.team.find(team_filter, {"_id": 1})]
        if not team_ids:
            return []
        match_filter["$or"] = [{"home_team_id": {"$in": team_ids}}, {"away_team_id": {"$in": team_ids}}]

    pipeline = [
        {"$match": match_filter},
        {"$project": {
            "home_team_id": 1,
            "away_team_id": 1,
            "home_score": 1,
            "away_score": 1,
            "events": 1,
        }},
        {"$project": {
            "pairs": [
                {"team": "$home_team_id", "goals_for": "$home_score", "goals_against": "$away_score",
                 "win": {"$cond": [{"$gt": ["$home_score", "$away_score"]}, 1, 0]},
                 "draw": {"$cond": [{"$eq": ["$home_score", "$away_score"]}, 1, 0]},
                },
                {"team": "$away_team_id", "goals_for": "$away_score", "goals_against": "$home_score",
                 "win": {"$cond": [{"$gt": ["$away_score", "$home_score"]}, 1, 0]},
                 "draw": {"$cond": [{"$eq": ["$home_score", "$away_score"]}, 1, 0]},
                },
            ]
        }},
        {"$unwind": "$pairs"},
        {"$group": {
            "_id": "$pairs.team",
            "goals": {"$sum": "$pairs.goals_for"},
            "wins": {"$sum": "$pairs.win"},
            "draws": {"$sum": "$pairs.draw"},
        }},
        {"$addFields": {"points": {"$add": [
            {"$multiply": ["$wins", 3]}, "$draws"
        ]}}},
        {"$sort": {stat: -1}},
        {"$limit": limit},
    ]
    rows = list(db.match.aggregate(pipeline))
    # join team details
    results = []
    for r in rows:
        team = db.team.find_one({"_id": r["_id"]})
        if not team:
            continue
        results.append({
            "team_id": str(r["_id"]),
            "team_name": team.get("name"),
            "country": team.get("country"),
            "city": team.get("city"),
            "goals": r.get("goals", 0),
            "wins": r.get("wins", 0),
            "points": r.get("points", 0),
        })
    return results

@app.get("/leaderboard/players")
def leaderboard_players(
    scope: Literal["global","country","city"] = "global",
    country: Optional[str] = None,
    city: Optional[str] = None,
    stat: Literal["goals","assists","yellow","red"] = "goals",
    limit: int = 20,
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    # Filter players by scope
    player_filter: Dict[str, Any] = {}
    if scope == "country" and country:
        player_filter["country"] = country
    if scope == "city" and city:
        player_filter["city"] = city
    player_ids = None
    if player_filter:
        player_ids = [p["_id"] for p in db.player.find(player_filter, {"_id": 1})]
        if not player_ids:
            return []

    # Unwind events across matches and count by player
    match_filter: Dict[str, Any] = {}
    pipeline = [
        {"$match": match_filter},
        {"$unwind": "$events"},
        {"$match": {"events.type": {"$in": ["goal", "assist", "yellow", "red"]}}},
    ]
    if stat == "goals":
        pipeline.append({"$match": {"events.type": "goal"}})
        key = "events.player_id"
    elif stat == "assists":
        pipeline.append({"$match": {"events.type": {"$in": ["assist", "goal"]}}})
        # count assister via secondary on goal and player on assist
        pipeline.extend([
            {"$project": {
                "pid": {
                    "$cond": [
                        {"$eq": ["$events.type", "goal"]}, "$events.secondary_player_id", "$events.player_id"
                    ]
                }
            }},
            {"$group": {"_id": "$pid", "count": {"$sum": 1}}},
        ])
        key = None
    else:
        pipeline.append({"$match": {"events.type": stat}})
        key = "events.player_id"

    if key:
        pipeline.extend([
            {"$group": {"_id": f"${key}", "count": {"$sum": 1}}},
        ])

    if player_ids is not None:
        pipeline.insert(0, {"$match": {}})  # placeholder no-op

    pipeline.extend([
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ])

    rows = list(db.match.aggregate(pipeline))

    # If scope filtered players, keep only those ids
    if player_ids is not None:
        rows = [r for r in rows if r["_id"] in set(player_ids)]

    results = []
    for r in rows:
        if r["_id"] is None:
            continue
        p = db.player.find_one({"_id": r["_id"]})
        if not p:
            continue
        team = db.team.find_one({"_id": p.get("team_id")}) if p.get("team_id") else None
        results.append({
            "player_id": str(r["_id"]),
            "player_name": p.get("name"),
            "team_name": team.get("name") if team else None,
            stat: r.get("count", 0),
            "country": p.get("country"),
            "city": p.get("city"),
        })
    return results


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
