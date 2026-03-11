import re
import PTN
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from urllib.parse import unquote
from fastapi import APIRouter, HTTPException, Depends

from Backend.config import Telegram
from Backend import db, __version__
from Backend.fastapi.security.tokens import verify_token

# --- Configuration & Constants ---
BASE_URL = Telegram.BASE_URL
ADDON_NAME = "Telegram Pro"
ADDON_VERSION = __version__
PAGE_SIZE = 20

router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])

GENRES = [
    "Action", "Adventure", "Animation", "Biography", "Comedy",
    "Crime", "Documentary", "Drama", "Family", "Fantasy",
    "History", "Horror", "Music", "Mystery", "Romance",
    "Sci-Fi", "Sport", "Thriller", "War", "Western"
]

# --- Enhanced Helper Functions ---

def detect_part(filename: str) -> str:
    """
    Detects if a file is part of a split movie (e.g., Part 1, CD2, .1.)
    """
    # Pattern for Part 1, Pt 2, CD 3, etc.
    part_match = re.search(r'(?:part|pt|p|cd)\.?\s?(\d+)', filename, re.IGNORECASE)
    # Pattern for split files like movie.1.mkv
    dot_part_match = re.search(r'\.(\d+)\.(?:mkv|mp4|avi|webm)$', filename, re.IGNORECASE)
    
    if part_match:
        return f" | Part {part_match.group(1)}"
    elif dot_part_match:
        return f" | Part {dot_part_match.group(1)}"
    return ""

def get_resolution_priority(stream_name: str) -> int:
    resolution_map = {
        "2160p": 2160, "4k": 2160, "uhd": 2160,
        "1080p": 1080, "fhd": 1080,
        "720p": 720, "hd": 720,
        "480p": 480, "sd": 480,
    }
    for res_key, res_value in resolution_map.items():
        if res_key in stream_name.lower():
            return res_value
    return 1

def format_stream_details(filename: str, quality: str, size: str) -> tuple[str, str]:
    """
    Parses the filename to create a clean UI for Stremio.
    """
    try:
        parsed = PTN.parse(filename)
    except Exception:
        return (f"Telegram {quality}", f"📁 {filename}\n💾 {size}")

    codec_parts = []
    if parsed.get("codec"):
        codec_parts.append(f"🎥 {parsed.get('codec')}")
    if parsed.get("bitDepth"):
        codec_parts.append(f"🌈 {parsed.get('bitDepth')}bit")
    if parsed.get("audio"):
        codec_parts.append(f"🔊 {parsed.get('audio')}")
    
    codec_info = " ".join(codec_parts)
    resolution = parsed.get("resolution", quality)
    
    # Custom branding for your server
    stream_name = f"NKT {resolution}".strip()
    
    stream_title = f"📁 {filename}\n💾 {size}"
    if codec_info:
        stream_title += f"\n{codec_info}"
    
    return (stream_name, stream_title)

def convert_to_stremio_meta(item: dict) -> dict:
    media_type = "series" if item.get("media_type") == "tv" else "movie"
    return {
        "id": item.get('imdb_id'),
        "type": media_type,
        "name": item.get("title"),
        "poster": item.get("poster") or "",
        "background": item.get("backdrop") or "",
        "logo": item.get("logo") or "",
        "year": item.get("release_year"),
        "description": item.get("description") or "",
        "genres": item.get("genres") or [],
        "imdbRating": str(item.get("rating") or "0"),
        "releaseInfo": str(item.get("release_year", "")),
        "runtime": item.get("runtime") or "",
    }

# --- Core Routes ---

@router.get("/{token}/manifest.json")
async def get_manifest(token: str, token_data: dict = Depends(verify_token)):
    resources = ["stream"] if Telegram.HIDE_CATALOG else ["catalog", "meta", "stream"]
    
    catalogs = []
    if not Telegram.HIDE_CATALOG:
        for m_type in ["movie", "series"]:
            catalogs.append({
                "type": m_type,
                "id": f"latest_{m_type}",
                "name": f"Latest {m_type.capitalize()}",
                "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}, {"name": "search"}]
            })

    return {
        "id": "org.nkt.stremio",
        "version": ADDON_VERSION,
        "name": f"{ADDON_NAME} (NKT Edition)",
        "description": "Premium Telegram Media Server with Multi-Part Support",
        "types": ["movie", "series"],
        "resources": resources,
        "catalogs": catalogs,
        "idPrefixes": ["tt"]
    }

@router.get("/{token}/catalog/{media_type}/{id}/{extra:path}.json")
@router.get("/{token}/catalog/{media_type}/{id}.json")
async def get_catalog(token: str, media_type: str, id: str, extra: Optional[str] = None, token_data: dict = Depends(verify_token)):
    if Telegram.HIDE_CATALOG:
        raise HTTPException(status_code=404)

    search_query = None
    stremio_skip = 0
    
    if extra:
        for param in extra.replace("&", "/").split("/"):
            if param.startswith("search="):
                search_query = unquote(param.split("=")[1])
            elif param.startswith("skip="):
                stremio_skip = int(param.split("=")[1])

    page = (stremio_skip // PAGE_SIZE) + 1
    
    try:
        if search_query:
            data = await db.search_documents(query=search_query, page=page, page_size=PAGE_SIZE)
            items = [i for i in data.get("results", []) if i.get("media_type") == ("tv" if media_type == "series" else "movie")]
        else:
            sort_type = [("updated_on", "desc")]
            if media_type == "movie":
                data = await db.sort_movies(sort_type, page, PAGE_SIZE)
                items = data.get("movies", [])
            else:
                data = await db.sort_tv_shows(sort_type, page, PAGE_SIZE)
                items = data.get("tv_shows", [])
    except:
        return {"metas": []}

    return {"metas": [convert_to_stremio_meta(i) for i in items]}

@router.get("/{token}/meta/{media_type}/{id}.json")
async def get_meta(token: str, media_type: str, id: str, token_data: dict = Depends(verify_token)):
    media = await db.get_media_details(imdb_id=id)
    if not media:
        return {"meta": {}}

    meta_obj = convert_to_stremio_meta(media)
    
    if media_type == "series" and "seasons" in media:
        videos = []
        for season in sorted(media["seasons"], key=lambda s: s["season_number"]):
            for ep in sorted(season["episodes"], key=lambda e: e["episode_number"]):
                videos.append({
                    "id": f"{id}:{season['season_number']}:{ep['episode_number']}",
                    "title": ep.get("title", f"S{season['season_number']} E{ep['episode_number']}"),
                    "season": season["season_number"],
                    "episode": ep["episode_number"],
                    "released": ep.get("released") or datetime.now(timezone.utc).isoformat()
                })
        meta_obj["videos"] = videos

    return {"meta": meta_obj}

@router.get("/{token}/stream/{media_type}/{id}.json")
async def get_streams(token: str, media_type: str, id: str, token_data: dict = Depends(verify_token)):
    # 1. Check Usage Limits
    if token_data.get("limit_exceeded"):
        return {"streams": [{"name": "Limit Reached", "title": "Upgrade Plan", "url": token_data["limit_video"]}]}

    # 2. Parse ID
    parts = id.split(":")
    imdb_id, s_num, e_num = parts[0], (int(parts[1]) if len(parts) > 1 else None), (int(parts[2]) if len(parts) > 2 else None)

    # 3. Fetch from DB
    media_details = await db.get_media_details(imdb_id=imdb_id, season_number=s_num, episode_number=e_num)
    if not media_details or "telegram" not in media_details:
        return {"streams": []}

    # 4. Build Multi-Part Streams
    streams = []
    for file_info in media_details.get("telegram", []):
        if not file_info.get("id"): continue
        
        filename = file_info.get("name", "")
        quality_str = file_info.get("quality", "HD")
        size = file_info.get("size", "")

        # Use our detection helpers
        part_tag = detect_part(filename)
        display_name, description = format_stream_details(filename, quality_str, size)
        
        streams.append({
            "name": f"{display_name}{part_tag}",
            "title": description,
            "url": f"{BASE_URL}/dl/{token}/{file_info.get('id')}/video.mkv"
        })

    # 5. Smart Sort: Resolution first, then Part Order
    streams.sort(key=lambda s: (get_resolution_priority(s["name"]), s["name"]), reverse=True)
    
    return {"streams": streams}
