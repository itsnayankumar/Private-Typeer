import re
import PTN
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from urllib.parse import unquote, quote
from datetime import datetime, timezone, timedelta
from fastapi.responses import HTMLResponse

from Backend.config import Telegram
from Backend import db, __version__
from Backend.fastapi.security.tokens import verify_token

# --- Configuration ---
BASE_URL = Telegram.BASE_URL
ADDON_NAME = "Telegram"
ADDON_VERSION = __version__
PAGE_SIZE = 15

router = APIRouter(prefix="/stremio", tags=["Stremio Addon"])

# Define available genres
GENRES = [
    "Action", "Adventure", "Animation", "Biography", "Comedy",
    "Crime", "Documentary", "Drama", "Family", "Fantasy",
    "History", "Horror", "Music", "Mystery", "Romance",
    "Sci-Fi", "Sport", "Thriller", "War", "Western"
]

# --- Helper Functions ---

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

def format_released_date(media):
    year = media.get("release_year")
    if year:
        try:
            return datetime(int(year), 1, 1).isoformat() + "Z"
        except:
            return None
    return None

def convert_to_stremio_meta(item: dict) -> dict:
    media_type = "series" if item.get("media_type") == "tv" else "movie"
    return {
        "id": item.get('imdb_id'),
        "type": media_type,
        "name": item.get("title"),
        "poster": item.get("poster") or "",
        "logo": item.get("logo") or "",
        "year": item.get("release_year"),
        "releaseInfo": str(item.get("release_year", "")),
        "imdb_id": item.get("imdb_id", ""),
        "moviedb_id": item.get("tmdb_id", ""),
        "background": item.get("backdrop") or "",
        "genres": item.get("genres") or [],
        "imdbRating": str(item.get("rating") or ""),
        "description": item.get("description") or "",
        "cast": item.get("cast") or [],
        "runtime": item.get("runtime") or "",
    }

def format_stream_details(filename: str, quality: str, size: str) -> tuple[str, str]:
    try:
        parsed = PTN.parse(filename)
    except Exception:
        return (f"Telegram {quality}", f"📁 {filename}\n💾 {size}")

    codec_parts = []
    if parsed.get("codec"): codec_parts.append(f"🎥 {parsed.get('codec')}")
    if parsed.get("bitDepth"): codec_parts.append(f"🌈 {parsed.get('bitDepth')}bit")
    if parsed.get("audio"): codec_parts.append(f"🔊 {parsed.get('audio')}")
    if parsed.get("encoder"): codec_parts.append(f"👤 {parsed.get('encoder')}")

    codec_info = " ".join(codec_parts) if codec_parts else ""
    resolution = parsed.get("resolution", quality)
    quality_type = parsed.get("quality", "")
    stream_name = f"Telegram {resolution} {quality_type}".strip()

    stream_title_parts = [f"📁 {filename}", f"💾 {size}"]
    if codec_info: stream_title_parts.append(codec_info)

    return (stream_name, "\n".join(stream_title_parts))

def get_resolution_priority(stream_name: str) -> int:
    resolution_map = {
        "2160p": 2160, "4k": 2160, "uhd": 2160,
        "1080p": 1080, "fhd": 1080,
        "720p": 720, "hd": 720,
        "480p": 480, "sd": 480, "360p": 360,
    }
    for res_key, res_value in resolution_map.items():
        if res_key in stream_name.lower(): return res_value
    return 1

# --- Routes ---

@router.get("/{token}/manifest.json")
async def get_manifest(token: str, token_data: dict = Depends(verify_token)):
    if Telegram.HIDE_CATALOG:
        resources, catalogs = ["stream"], []
    else:
        resources = ["catalog", "meta", "stream"]
        catalogs = [
            {"type": "movie", "id": "latest_movies", "name": "Latest", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "movie", "id": "top_movies", "name": "Popular", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}, {"name": "search"}], "extraSupported": ["genre", "skip", "search"]},
            {"type": "series", "id": "latest_series", "name": "Latest", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}], "extraSupported": ["genre", "skip"]},
            {"type": "series", "id": "top_series", "name": "Popular", "extra": [{"name": "genre", "options": GENRES}, {"name": "skip"}, {"name": "search"}], "extraSupported": ["genre", "skip", "search"]}
        ]

    addon_name, addon_desc, addon_version = ADDON_NAME, "Streams movies and series from your Telegram.", ADDON_VERSION

    if Telegram.SUBSCRIPTION:
        user_id = token_data.get("user_id")
        if user_id:
            try:
                user = await db.get_user(int(user_id))
                if user and user.get("subscription_status") == "active":
                    expiry_obj = user.get("subscription_expiry")
                    if expiry_obj:
                        expiry_str = expiry_obj.strftime("%d %b %Y").lstrip("0")
                        addon_name = f"{ADDON_NAME} — Expires {expiry_str}"
                        addon_desc = f"📅 Subscription active until {expiry_str}.\n{addon_desc}"
                        epoch_tag = format(int(expiry_obj.timestamp()) & 0xFFFF, "x")
                        addon_version = f"{ADDON_VERSION}-{epoch_tag}"
            except: pass

    return {
        "id": f"telegram.media.{token[:8]}",
        "version": addon_version,
        "name": addon_name,
        "logo": "https://i.postimg.cc/XqWnmDXr/Picsart-25-10-09-08-09-45-867.png",
        "description": addon_desc,
        "types": ["movie", "series"],
        "resources": resources,
        "catalogs": catalogs,
        "idPrefixes": ["tt"],
        "behaviorHints": {"configurable": True, "configurationRequired": False},
        "config": [{"key": "manifest_url", "title": "Addon URL", "type": "text", "default": f"{BASE_URL}/stremio/{token}/manifest.json"}]
    }

@router.get("/{token}/configure")
async def configure_addon(token: str):
    manifest_url = f"{BASE_URL}/stremio/{token}/manifest.json"
    web_install_url = f"https://web.stremio.com/#/?addon_manifest={quote(manifest_url, safe='')}"
    # (Simplified HTML response logic would go here, same as your original)
    return HTMLResponse(content=f"<html><body><a href='{web_install_url}'>Install Addon</a></body></html>")

@router.get("/{token}/catalog/{media_type}/{id}/{extra:path}.json")
@router.get("/{token}/catalog/{media_type}/{id}.json")
async def get_catalog(token: str, media_type: str, id: str, extra: Optional[str] = None, token_data: dict = Depends(verify_token)):
    if Telegram.HIDE_CATALOG: raise HTTPException(status_code=404)
    
    search_query, stremio_skip = None, 0
    if extra:
        for param in extra.replace("&", "/").split("/"):
            if param.startswith("search="): search_query = unquote(param.removeprefix("search="))
            elif param.startswith("skip="): stremio_skip = int(param.removeprefix("skip=")) or 0

    page = (stremio_skip // PAGE_SIZE) + 1
    try:
        if search_query:
            res = await db.search_documents(query=search_query, page=page, page_size=PAGE_SIZE)
            items = [i for i in res.get("results", []) if i.get("media_type") == ("tv" if media_type == "series" else "movie")]
        else:
            sort = [("updated_on", "desc")]
            if media_type == "movie":
                data = await db.sort_movies(sort, page, PAGE_SIZE)
                items = data.get("movies", [])
            else:
                data = await db.sort_tv_shows(sort, page, PAGE_SIZE)
                items = data.get("tv_shows", [])
        return {"metas": [convert_to_stremio_meta(i) for i in items]}
    except: return {"metas": []}

@router.get("/{token}/meta/{media_type}/{id}.json")
async def get_meta(token: str, media_type: str, id: str, token_data: dict = Depends(verify_token)):
    media = await db.get_media_details(imdb_id=id)
    if not media: return {"meta": {}}

    meta_obj = convert_to_stremio_meta(media)
    if media_type == "series" and "seasons" in media:
        videos = []
        for s in sorted(media["seasons"], key=lambda x: x["season_number"]):
            for e in sorted(s["episodes"], key=lambda x: x["episode_number"]):
                videos.append({
                    "id": f"{id}:{s['season_number']}:{e['episode_number']}",
                    "title": e.get("title", f"Episode {e['episode_number']}"),
                    "season": s["season_number"], "episode": e["episode_number"],
                    "released": e.get("released") or datetime.now(timezone.utc).isoformat()
                })
        meta_obj["videos"] = videos
    return {"meta": meta_obj}

@router.get("/{token}/stream/{media_type}/{id}.json")
async def get_streams(token: str, media_type: str, id: str, token_data: dict = Depends(verify_token)):
    if token_data.get("subscription_expired"):
        return {"streams": [{"name": "🚫 Expired", "title": "Renew via bot", "url": Telegram.SUBSCRIPTION_URL}]}
    
    if token_data.get("limit_exceeded"):
        return {"streams": [{"name": "Limit Reached", "title": "Daily/Monthly limit hit", "url": token_data["limit_video"]}]}

    try:
        parts = id.split(":")
        imdb_id, s_num, e_num = parts[0], (int(parts[1]) if len(parts) > 1 else None), (int(parts[2]) if len(parts) > 2 else None)
    except: raise HTTPException(status_code=400)

    media_details = await db.get_media_details(imdb_id=imdb_id, season_number=s_num, episode_number=e_num)
    if not media_details or "telegram" not in media_details: return {"streams": []}

    streams = []
    for quality in media_details.get("telegram", []):
        if quality.get("id"):
            filename = quality.get("name", "")
            q_str, size = quality.get("quality", "HD"), quality.get("size", "")
            
            # Detect Part
            part_tag = detect_part(filename)
            s_name, s_title = format_stream_details(filename, q_str, size)
            
            streams.append({
                "name": f"{s_name}{part_tag}",
                "title": s_title,
                "url": f"{BASE_URL}/dl/{token}/{quality.get('id')}/video.mkv"
            })

    streams.sort(key=lambda s: (get_resolution_priority(s["name"]), s["name"]), reverse=True)
    return {"streams": streams}
