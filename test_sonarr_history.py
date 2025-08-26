#!/usr/bin/env python3
"""
Test script to check Sonarr history and queue for specific episodes
"""

import requests
import json
from datetime import datetime
from typing import Dict, List, Optional

# Sonarr Configuration
SONARR_URL = "http://REDACTED_URL"
API_KEY = "REDACTED_API_KEY"

# Headers for API requests
HEADERS = {
    "X-Api-Key": API_KEY,
    "Content-Type": "application/json"
}

def get_series_by_title(title: str) -> Optional[Dict]:
    """Find series by title"""
    url = f"{SONARR_URL}/api/v3/series"
    response = requests.get(url, headers=HEADERS)
    
    if response.status_code == 200:
        series_list = response.json()
        for series in series_list:
            if title.lower() in series['title'].lower():
                return series
    return None

def get_episode_info(series_id: int, season: int, episode: int) -> Optional[Dict]:
    """Get specific episode information"""
    url = f"{SONARR_URL}/api/v3/episode"
    params = {"seriesId": series_id}
    response = requests.get(url, headers=HEADERS, params=params)
    
    if response.status_code == 200:
        episodes = response.json()
        for ep in episodes:
            if ep['seasonNumber'] == season and ep['episodeNumber'] == episode:
                return ep
    return None

def get_history(episode_id: Optional[int] = None, page_size: int = 100) -> List[Dict]:
    """Get history for specific episode or all"""
    url = f"{SONARR_URL}/api/v3/history"
    params = {
        "pageSize": page_size,
        "sortKey": "date",
        "sortDirection": "descending"
    }
    
    if episode_id:
        params["episodeId"] = episode_id
    
    response = requests.get(url, headers=HEADERS, params=params)
    
    if response.status_code == 200:
        data = response.json()
        return data.get('records', [])
    return []

def get_queue() -> List[Dict]:
    """Get current download queue"""
    url = f"{SONARR_URL}/api/v3/queue"
    params = {
        "pageSize": 100,
        "includeUnknownSeriesItems": True,
        "includeSeries": True,
        "includeEpisode": True
    }
    
    response = requests.get(url, headers=HEADERS, params=params)
    
    if response.status_code == 200:
        data = response.json()
        return data.get('records', [])
    return []

def analyze_episode_history(series_title: str, season: int, episode: int):
    """Analyze history for a specific episode"""
    print(f"\n{'='*60}")
    print(f"Analyzing: {series_title} S{season:02d}E{episode:02d}")
    print(f"{'='*60}")
    
    # Find series
    series = get_series_by_title(series_title)
    if not series:
        print(f"❌ Series '{series_title}' not found")
        return
    
    print(f"✓ Found series: {series['title']} (ID: {series['id']})")
    
    # Get episode info
    ep_info = get_episode_info(series['id'], season, episode)
    if not ep_info:
        print(f"❌ Episode S{season:02d}E{episode:02d} not found")
        return
    
    episode_id = ep_info['id']
    print(f"✓ Episode ID: {episode_id}")
    print(f"  Status: {ep_info.get('hasFile', False) and 'Downloaded' or 'Missing'}")
    
    # Get history for this episode
    history = get_history(episode_id)
    
    if history:
        print(f"\n📜 History (Last {len(history)} events):")
        for idx, event in enumerate(history[:10], 1):  # Show last 10 events
            event_type = event.get('eventType', 'Unknown')
            date = event.get('date', '')
            source_title = event.get('sourceTitle', 'N/A')
            
            # Extract quality and custom format info
            quality = event.get('quality', {})
            quality_name = quality.get('quality', {}).get('name', 'Unknown')
            
            # Get custom format scores if available
            custom_formats = event.get('customFormats', [])
            cf_score = event.get('customFormatScore', 0)
            
            print(f"\n  {idx}. {event_type} - {date[:19]}")
            print(f"     Source: {source_title}")
            print(f"     Quality: {quality_name}")
            print(f"     CF Score: {cf_score}")
            
            if custom_formats:
                print(f"     Custom Formats: {', '.join([cf.get('name', '') for cf in custom_formats])}")
            
            # Show grab/import specific data
            if event_type == 'grabbed':
                print(f"     📥 Grab Score: {cf_score}")
                if 'data' in event:
                    data = event['data']
                    print(f"     Indexer: {data.get('indexer', 'N/A')}")
                    print(f"     Release Group: {data.get('releaseGroup', 'N/A')}")
            
            elif event_type == 'downloadFolderImported':
                print(f"     📦 Import Score: {cf_score}")
    else:
        print("No history found for this episode")

def check_queue_for_series(series_title: str):
    """Check if series has items in queue"""
    print(f"\n🔍 Checking queue for {series_title}...")
    
    queue = get_queue()
    relevant_items = []
    
    for item in queue:
        if 'series' in item and item['series']:
            if series_title.lower() in item['series']['title'].lower():
                relevant_items.append(item)
    
    if relevant_items:
        print(f"Found {len(relevant_items)} items in queue:")
        for item in relevant_items:
            episode = item.get('episode', {})
            status = item.get('status', 'Unknown')
            title = item.get('title', 'N/A')
            
            print(f"\n  • S{episode.get('seasonNumber', 0):02d}E{episode.get('episodeNumber', 0):02d}")
            print(f"    Title: {title}")
            print(f"    Status: {status}")
            
            # Check for warnings/errors
            if 'statusMessages' in item and item['statusMessages']:
                print(f"    ⚠️ Messages:")
                for msg in item['statusMessages']:
                    print(f"      - {msg.get('title', ''): {msg.get('messages', [])}}")
            
            # Show trackedDownloadStatus
            if 'trackedDownloadStatus' in item:
                print(f"    Download Status: {item['trackedDownloadStatus']}")
            if 'trackedDownloadState' in item:
                print(f"    Download State: {item['trackedDownloadState']}")
    else:
        print("No items in queue for this series")

def main():
    """Main function to analyze specific episodes"""
    
    print("🔧 Sonarr Import Analysis Tool")
    print(f"Server: {SONARR_URL}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Test connection
    try:
        response = requests.get(f"{SONARR_URL}/api/v3/system/status", headers=HEADERS)
        if response.status_code == 200:
            print("✓ Connected to Sonarr successfully\n")
        else:
            print(f"❌ Connection failed: {response.status_code}")
            return
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return
    
    # Episodes to check
    episodes_to_check = [
        ("SAKAMOTO DAYS", 1, 18),
        ("SAKAMOTO DAYS", 1, 19),
        ("Sword of the Demon Hunter", 1, 20),
    ]
    
    # Check history for each episode
    for series_title, season, episode in episodes_to_check:
        analyze_episode_history(series_title, season, episode)
    
    # Check queue for these series
    print(f"\n{'='*60}")
    print("QUEUE STATUS")
    print(f"{'='*60}")
    
    check_queue_for_series("SAKAMOTO DAYS")
    check_queue_for_series("Sword of the Demon Hunter")
    
    # Get overall queue status
    queue = get_queue()
    print(f"\n📊 Queue Summary: {len(queue)} total items")
    
    # Show any items with warnings
    warning_items = [item for item in queue if item.get('trackedDownloadState') == 'importPending' 
                     or (item.get('statusMessages') and len(item['statusMessages']) > 0)]
    
    if warning_items:
        print(f"\n⚠️ Items with warnings/pending imports: {len(warning_items)}")
        for item in warning_items[:5]:  # Show first 5
            if 'series' in item and item['series']:
                episode = item.get('episode', {})
                print(f"  • {item['series']['title']} S{episode.get('seasonNumber', 0):02d}E{episode.get('episodeNumber', 0):02d}")

if __name__ == "__main__":
    main()