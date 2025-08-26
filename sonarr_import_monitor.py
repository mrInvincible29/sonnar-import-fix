#!/usr/bin/env python3
"""
Sonarr Import Monitor - Automatically fixes stuck imports by comparing grab vs import scores
"""

import requests
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import argparse
import yaml
import sys
import threading
from flask import Flask, request, jsonify

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WebhookServer:
    """Flask server to handle Sonarr webhook events"""
    
    def __init__(self, monitor):
        self.monitor = monitor
        self.app = Flask(__name__)
        
        # Suppress Flask logging except for errors
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        
        # Cache to store grab events for comparison
        self.grab_cache = {}  # episode_id -> grab_info
        
        # Setup routes
        self.setup_routes()
        
    def setup_routes(self):
        """Setup webhook routes"""
        
        @self.app.route(f"/sonarr/webhook", methods=['POST'])
        def webhook_handler():
            try:
                data = request.json
                if not data:
                    return jsonify({'error': 'No JSON data'}), 400
                
                event_type = data.get('eventType', 'Unknown')
                
                handlers = {
                    'Test': self.handle_test,
                    'Grab': self.handle_grab,
                    'Download': self.handle_download,
                    'ImportFailed': self.handle_import_failed
                }
                
                handler = handlers.get(event_type)
                if handler:
                    return handler(data)
                else:
                    logger.info(f"📨 Unhandled webhook event: {event_type}")
                    return jsonify({'status': 'ignored'}), 200
                    
            except Exception as e:
                logger.error(f"Webhook error: {e}")
                return jsonify({'error': str(e)}), 500
                
        @self.app.route("/sonarr/webhook", methods=['GET'])
        def webhook_info():
            """Info endpoint for webhook configuration"""
            return jsonify({
                'service': 'Sonarr Import Monitor',
                'webhook_path': '/sonarr/webhook',
                'supported_events': ['Test', 'Grab', 'Download', 'ImportFailed'],
                'cache_size': len(self.grab_cache)
            })
    
    def handle_test(self, data):
        """Handle test webhook from Sonarr"""
        series = data.get('series', {})
        episodes = data.get('episodes', [])
        
        logger.info("🧪 Webhook test received")
        logger.info(f"   Series: {series.get('title', 'Unknown')}")
        if episodes:
            ep = episodes[0]
            logger.info(f"   Episode: S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}")
        
        return jsonify({'status': 'test successful', 'message': 'Webhook is working!'}), 200
    
    def handle_grab(self, data):
        """Handle grab event - cache for later comparison"""
        try:
            episodes = data.get('episodes', [])
            release = data.get('release', {})
            series = data.get('series', {})
            
            grab_info = {
                'timestamp': datetime.now(),
                'download_id': data.get('downloadId'),
                'download_client': data.get('downloadClient'),
                'score': release.get('customFormatScore', 0),
                'formats': release.get('customFormats', []),
                'title': release.get('releaseTitle', 'Unknown'),
                'indexer': release.get('indexer', 'Unknown'),
                'series_title': series.get('title', 'Unknown')
            }
            
            logger.info(f"📥 Grab webhook: {grab_info['series_title']}")
            logger.info(f"   Release: {grab_info['title']}")
            logger.info(f"   Score: {grab_info['score']}")
            logger.info(f"   Download ID: {grab_info['download_id']}")
            
            # Cache grab info for each episode
            for episode in episodes:
                ep_id = episode['id']
                self.grab_cache[ep_id] = grab_info
                
                # Schedule delayed import check
                delay = self.monitor.config.get('webhook', {}).get('import_check_delay', 600)
                timer = threading.Timer(
                    delay,
                    self.check_if_imported,
                    args=[ep_id, grab_info['download_id']]
                )
                timer.daemon = True
                timer.start()
                
                logger.info(f"   Scheduled import check for episode {ep_id} in {delay}s")
            
            return jsonify({'status': 'grab cached', 'episodes': len(episodes)}), 200
            
        except Exception as e:
            logger.error(f"Error handling grab webhook: {e}")
            return jsonify({'error': str(e)}), 500
    
    def handle_download(self, data):
        """Handle successful import - check for score issues"""
        try:
            episodes = data.get('episodes', [])
            episode_file = data.get('episodeFile', {})
            series = data.get('series', {})
            import_score = episode_file.get('customFormatScore', 0)
            
            logger.info(f"📦 Import webhook: {series.get('title', 'Unknown')}")
            logger.info(f"   Import score: {import_score}")
            
            for episode in episodes:
                ep_id = episode['id']
                
                if ep_id in self.grab_cache:
                    grab_info = self.grab_cache[ep_id]
                    grab_score = grab_info['score']
                    score_diff = grab_score - import_score
                    
                    logger.info(f"   Grab score: {grab_score}")
                    logger.info(f"   Score difference: {score_diff}")
                    
                    # Check for score mismatch (shouldn't happen on successful import)
                    threshold = self.monitor.config['decisions']['force_import_threshold']
                    if score_diff > threshold:
                        logger.warning(f"⚠️ Score mismatch on successful import!")
                        logger.warning(f"   This shouldn't happen - investigate")
                    
                    # Clear from cache - successfully imported
                    del self.grab_cache[ep_id]
                    logger.info(f"   Cleared episode {ep_id} from cache")
            
            return jsonify({'status': 'import processed'}), 200
            
        except Exception as e:
            logger.error(f"Error handling download webhook: {e}")
            return jsonify({'error': str(e)}), 500
    
    def handle_import_failed(self, data):
        """Handle import failure notification"""
        try:
            episodes = data.get('episodes', [])
            series = data.get('series', {})
            message = data.get('message', 'Unknown error')
            
            logger.warning(f"❌ Import failed webhook: {series.get('title', 'Unknown')}")
            logger.warning(f"   Message: {message}")
            
            # Trigger immediate queue check for affected episodes
            for episode in episodes:
                ep_id = episode['id']
                logger.info(f"   Triggering queue check for episode {ep_id}")
                
                # Schedule immediate check (in a separate thread)
                timer = threading.Timer(5, self.monitor.check_episode_queue, args=[ep_id])
                timer.daemon = True
                timer.start()
            
            return jsonify({'status': 'import failure processed'}), 200
            
        except Exception as e:
            logger.error(f"Error handling import failed webhook: {e}")
            return jsonify({'error': str(e)}), 500
    
    def check_if_imported(self, episode_id, download_id):
        """Check if grab was imported after delay"""
        try:
            if episode_id not in self.grab_cache:
                return  # Already imported and cleared from cache
            
            grab_info = self.grab_cache[episode_id]
            logger.info(f"🔍 Checking delayed import for episode {episode_id}")
            logger.info(f"   Original grab: {grab_info['title']}")
            
            # Check if still in Sonarr queue
            queue = self.monitor.get_queue()
            queue_item = None
            
            for item in queue:
                if item.get('downloadId') == download_id:
                    episode = item.get('episode', {})
                    if episode.get('id') == episode_id:
                        queue_item = item
                        break
            
            if queue_item:
                status = queue_item.get('status', 'unknown')
                state = queue_item.get('trackedDownloadState', 'unknown')
                
                logger.warning(f"⏰ Download still in queue after delay")
                logger.info(f"   Status: {status}")
                logger.info(f"   State: {state}")
                
                if status == 'completed' or state == 'importPending':
                    logger.info("   Download completed but not importing - forcing import")
                    success = self.monitor.force_import(queue_item)
                    if success:
                        # Clear from cache on successful force import
                        if episode_id in self.grab_cache:
                            del self.grab_cache[episode_id]
            else:
                # Not in queue - check if it imported silently
                if self.was_imported(episode_id, download_id):
                    logger.info(f"   Episode was imported (not via webhook)")
                    if episode_id in self.grab_cache:
                        del self.grab_cache[episode_id]
                else:
                    logger.warning(f"   Episode never imported and not in queue")
                    
        except Exception as e:
            logger.error(f"Error checking delayed import: {e}")
    
    def was_imported(self, episode_id, download_id):
        """Check if episode was imported by checking recent history"""
        try:
            history = self.monitor.get_history_for_episode(episode_id, limit=10)
            
            # Look for recent import with matching download_id
            for event in history:
                if event.get('eventType') == 'downloadFolderImported':
                    if event.get('downloadId') == download_id:
                        return True
            return False
            
        except Exception as e:
            logger.error(f"Error checking if imported: {e}")
            return False
    
    def start(self, host='0.0.0.0', port=8090):
        """Start the webhook server"""
        logger.info(f"🚀 Starting webhook server on {host}:{port}")
        self.app.run(host=host, port=port, debug=False, use_reloader=False)

class SonarrImportMonitor:
    def __init__(self, config_path: Optional[str] = None):
        """Initialize the monitor with configuration"""
        if config_path:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
        else:
            # Default configuration
            config = {
                'sonarr': {
                    'url': 'http://REDACTED_URL',
                    'api_key': 'REDACTED_API_KEY'
                },
                'monitoring': {
                    'interval': 60,
                    'stuck_threshold': 300,
                    'score_tolerance': 50,
                    'detect_repeated_grabs': True
                },
                'webhook': {
                    'enabled': False,
                    'host': '0.0.0.0',
                    'port': 8090,
                    'import_check_delay': 600
                },
                'trackers': {
                    'private': ['beyondhd', 'bhd', 'privatehd'],
                    'public': ['nyaa', 'animetosho', 'rarbg', '1337x', 'animeTosho']
                },
                'decisions': {
                    'force_import_threshold': 10,
                    'remove_public_failures': True,
                    'protect_private_ratio': True
                }
            }
        
        self.config = config
        self.sonarr_url = config['sonarr']['url']
        self.api_key = config['sonarr']['api_key']
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json"
        }
        self.dry_run = False  # Will be set by command line args
        
        # Cache for Sonarr configuration
        self.custom_formats = {}
        self.quality_profiles = {}
        self.series_profile_map = {}
        self._load_sonarr_config()
        
        # Webhook server
        self.webhook_server = None
    
    def _load_sonarr_config(self):
        """Load custom formats and quality profiles from Sonarr"""
        try:
            logger.info("🔧 Loading Sonarr configuration...")
            self.custom_formats = self._fetch_custom_formats()
            self.quality_profiles = self._fetch_quality_profiles()
            self._build_series_profile_map()
            logger.info(f"✓ Loaded {len(self.custom_formats)} custom formats and {len(self.quality_profiles)} quality profiles")
        except Exception as e:
            logger.warning(f"Failed to load Sonarr configuration: {e}")
    
    def _fetch_custom_formats(self) -> Dict[int, Dict]:
        """Fetch all custom formats from Sonarr"""
        url = f"{self.sonarr_url}/api/v3/customformat"
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                formats = response.json()
                return {cf['id']: cf for cf in formats}
        except Exception as e:
            logger.error(f"Failed to fetch custom formats: {e}")
        return {}
    
    def _fetch_quality_profiles(self) -> Dict[int, Dict]:
        """Fetch all quality profiles from Sonarr"""
        url = f"{self.sonarr_url}/api/v3/qualityprofile"
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                profiles = response.json()
                return {profile['id']: profile for profile in profiles}
        except Exception as e:
            logger.error(f"Failed to fetch quality profiles: {e}")
        return {}
    
    def _build_series_profile_map(self):
        """Build a map of series to their quality profiles"""
        try:
            url = f"{self.sonarr_url}/api/v3/series"
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                series_list = response.json()
                for series in series_list:
                    self.series_profile_map[series['id']] = series.get('qualityProfileId')
        except Exception as e:
            logger.error(f"Failed to build series profile map: {e}")
    
    def get_custom_format_scores(self, series_id: int) -> Dict[int, int]:
        """Get custom format scores for a series based on its quality profile"""
        profile_id = self.series_profile_map.get(series_id)
        if not profile_id or profile_id not in self.quality_profiles:
            return {}
        
        profile = self.quality_profiles[profile_id]
        scores = {}
        
        for format_item in profile.get('formatItems', []):
            format_id = format_item.get('format')
            score = format_item.get('score', 0)
            scores[format_id] = score
        
        return scores
    
    def analyze_custom_formats(self, custom_formats_list: List[Dict], series_id: int) -> Tuple[int, List[str]]:
        """Analyze custom formats and return total score and format names"""
        if not custom_formats_list:
            return 0, []
        
        format_scores = self.get_custom_format_scores(series_id)
        total_score = 0
        format_names = []
        
        for cf in custom_formats_list:
            cf_id = cf.get('id')
            cf_name = cf.get('name', 'Unknown')
            format_names.append(cf_name)
            
            if cf_id in format_scores:
                total_score += format_scores[cf_id]
        
        return total_score, format_names
        
    def get_queue(self) -> List[Dict]:
        """Get current download queue with full details"""
        url = f"{self.sonarr_url}/api/v3/queue"
        params = {
            "pageSize": 1000,
            "includeUnknownSeriesItems": True,
            "includeSeries": True,
            "includeEpisode": True
        }
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            if response.status_code == 200:
                data = response.json()
                return data.get('records', [])
        except Exception as e:
            logger.error(f"Failed to get queue: {e}")
        return []
    
    def get_series_by_title(self, title: str) -> Optional[Dict]:
        """Find series by title"""
        url = f"{self.sonarr_url}/api/v3/series"
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                series_list = response.json()
                for series in series_list:
                    if title.lower() in series['title'].lower():
                        return series
        except Exception as e:
            logger.error(f"Failed to get series: {e}")
        return None
    
    def get_episode_info(self, series_id: int, season: int, episode: int) -> Optional[Dict]:
        """Get specific episode information"""
        url = f"{self.sonarr_url}/api/v3/episode"
        params = {"seriesId": series_id}
        try:
            response = requests.get(url, headers=self.headers, params=params)
            if response.status_code == 200:
                episodes = response.json()
                for ep in episodes:
                    if ep['seasonNumber'] == season and ep['episodeNumber'] == episode:
                        return ep
        except Exception as e:
            logger.error(f"Failed to get episode: {e}")
        return None
    
    def get_history_for_episode(self, episode_id: int, limit: int = 50) -> List[Dict]:
        """Get history for specific episode"""
        url = f"{self.sonarr_url}/api/v3/history"
        params = {
            "pageSize": limit,
            "episodeId": episode_id,
            "sortKey": "date",
            "sortDirection": "descending"
        }
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            if response.status_code == 200:
                data = response.json()
                return data.get('records', [])
        except Exception as e:
            logger.error(f"Failed to get history: {e}")
        return []
    
    def find_grab_score(self, history: List[Dict], download_id: Optional[str] = None) -> Optional[int]:
        """Find the grab score from history"""
        for event in history:
            if event.get('eventType') == 'grabbed':
                # If we have a download_id, try to match it
                if download_id and event.get('downloadId') == download_id:
                    return event.get('customFormatScore', 0)
                # Otherwise, return the most recent grab score
                elif not download_id:
                    return event.get('customFormatScore', 0)
        return None
    
    def find_detailed_grab_info(self, history: List[Dict], download_id: Optional[str] = None, series_id: Optional[int] = None) -> Tuple[Optional[int], List[str]]:
        """Find detailed grab information including custom formats"""
        for event in history:
            if event.get('eventType') == 'grabbed':
                # If we have a download_id, try to match it
                if download_id and event.get('downloadId') == download_id:
                    score = event.get('customFormatScore', 0)
                    formats = event.get('customFormats', [])
                    format_names = [cf.get('name', 'Unknown') for cf in formats]
                    return score, format_names
                # Otherwise, return the most recent grab score
                elif not download_id:
                    score = event.get('customFormatScore', 0)
                    formats = event.get('customFormats', [])
                    format_names = [cf.get('name', 'Unknown') for cf in formats]
                    return score, format_names
        return None, []
    
    def get_current_file_details(self, episode_id: int, series_id: Optional[int] = None) -> Tuple[Optional[int], List[str]]:
        """Get current file's custom format score and format names"""
        try:
            # Get episode details
            url = f"{self.sonarr_url}/api/v3/episode/{episode_id}"
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                episode = response.json()
                if episode.get('hasFile'):
                    # Get the episode file details
                    episode_file_id = episode.get('episodeFileId')
                    if episode_file_id:
                        file_url = f"{self.sonarr_url}/api/v3/episodefile/{episode_file_id}"
                        file_response = requests.get(file_url, headers=self.headers)
                        if file_response.status_code == 200:
                            file_data = file_response.json()
                            score = file_data.get('customFormatScore', 0)
                            formats = file_data.get('customFormats', [])
                            format_names = [cf.get('name', 'Unknown') for cf in formats]
                            return score, format_names
        except Exception as e:
            logger.error(f"Failed to get current file details: {e}")
        return None, []
    
    def get_current_file_score(self, episode_id: int) -> Optional[int]:
        """Get the current file's custom format score"""
        # Get episode details
        url = f"{self.sonarr_url}/api/v3/episode/{episode_id}"
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                episode = response.json()
                if episode.get('hasFile'):
                    # Get the episode file details
                    episode_file_id = episode.get('episodeFileId')
                    if episode_file_id:
                        file_url = f"{self.sonarr_url}/api/v3/episodefile/{episode_file_id}"
                        file_response = requests.get(file_url, headers=self.headers)
                        if file_response.status_code == 200:
                            file_data = file_response.json()
                            return file_data.get('customFormatScore', 0)
        except Exception as e:
            logger.error(f"Failed to get current file score: {e}")
        return None
    
    def is_private_tracker(self, indexer: str) -> bool:
        """Check if the indexer is a private tracker"""
        if not indexer:
            return False
        
        indexer_lower = indexer.lower()
        for tracker in self.config['trackers']['private']:
            if tracker in indexer_lower:
                return True
        return False
    
    def force_import(self, queue_item: Dict) -> bool:
        """Force import of a queue item"""
        if self.dry_run:
            logger.info("   🔸 DRY RUN: Would force import")
            return True
            
        # Get manual import details
        download_id = queue_item.get('downloadId')
        if not download_id:
            logger.warning(f"No download ID for queue item")
            return False
        
        # Get manual import info
        url = f"{self.sonarr_url}/api/v3/manualimport"
        params = {
            "downloadId": download_id
        }
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            if response.status_code == 200:
                import_items = response.json()
                
                if not import_items:
                    logger.warning(f"No manual import items found for download {download_id}")
                    return False
                
                # Force import each item
                for item in import_items:
                    item['quality'] = queue_item.get('quality', item.get('quality'))
                    item['episodeIds'] = [queue_item['episode']['id']]
                    
                    # Update manual import
                    import_url = f"{self.sonarr_url}/api/v3/manualimport"
                    import_response = requests.put(
                        import_url,
                        headers=self.headers,
                        json=[item]
                    )
                    
                    if import_response.status_code == 200:
                        logger.info(f"✅ Successfully forced import for {queue_item['title']}")
                        return True
                    else:
                        logger.error(f"Failed to force import: {import_response.text}")
                        
        except Exception as e:
            logger.error(f"Failed to force import: {e}")
        
        return False
    
    def remove_from_queue(self, queue_item: Dict, delete_files: bool = True) -> bool:
        """Remove item from queue and optionally delete files"""
        if self.dry_run:
            logger.info(f"   🔸 DRY RUN: Would remove from queue (delete_files={delete_files})")
            return True
            
        queue_id = queue_item.get('id')
        if not queue_id:
            return False
        
        url = f"{self.sonarr_url}/api/v3/queue/{queue_id}"
        params = {
            "removeFromClient": delete_files,
            "blocklist": False
        }
        
        try:
            response = requests.delete(url, headers=self.headers, params=params)
            if response.status_code == 200:
                logger.info(f"🗑️ Removed from queue: {queue_item['title']}")
                return True
        except Exception as e:
            logger.error(f"Failed to remove from queue: {e}")
        
        return False
    
    def check_episode_queue(self, episode_id: int):
        """Check queue for specific episode and take action if needed"""
        try:
            queue = self.get_queue()
            
            for item in queue:
                episode = item.get('episode', {})
                if episode.get('id') == episode_id:
                    logger.info(f"🔍 Found episode {episode_id} in queue")
                    
                    action, grab_score, current_score, reasoning = self.analyze_queue_item(item)
                    
                    if action == "force_import":
                        success = self.force_import(item)
                        if success:
                            logger.info("   ✅ Force import successful")
                        else:
                            logger.warning("   ❌ Failed to force import")
                    elif action == "remove":
                        success = self.remove_from_queue(item, delete_files=True)
                        if success:
                            logger.info("   ✅ Removed from queue")
                        else:
                            logger.warning("   ❌ Failed to remove from queue")
                    break
            else:
                logger.info(f"Episode {episode_id} not found in current queue")
                
        except Exception as e:
            logger.error(f"Error checking episode queue: {e}")
    
    def detect_repeated_grabs(self, episode_id: int) -> List[Dict]:
        """Detect multiple grabs indicating import issues"""
        try:
            history = self.get_history_for_episode(episode_id, limit=50)
            
            grabs = [e for e in history if e['eventType'] == 'grabbed']
            imports = [e for e in history if e['eventType'] == 'downloadFolderImported']
            
            if len(grabs) > len(imports) + 1:
                # More grabs than imports - something is stuck
                unimported_grabs = []
                
                for grab in grabs:
                    download_id = grab.get('downloadId')
                    
                    # Check if this grab has a corresponding import
                    has_import = any(
                        imp.get('downloadId') == download_id 
                        for imp in imports
                    )
                    
                    if not has_import:
                        unimported_grabs.append(grab)
                
                return unimported_grabs
            
            return []
            
        except Exception as e:
            logger.error(f"Error detecting repeated grabs: {e}")
            return []
    
    def check_all_repeated_grabs(self):
        """Check for repeated grabs across all recent episodes"""
        try:
            logger.info("🔍 Checking for repeated grab patterns...")
            
            # Get recent history
            url = f"{self.sonarr_url}/api/v3/history"
            params = {
                'pageSize': 200,
                'sortKey': 'date',
                'sortDirection': 'descending'
            }
            
            response = requests.get(url, headers=self.headers, params=params)
            if response.status_code == 200:
                records = response.json().get('records', [])
                
                # Group by episode
                episodes_with_grabs = {}
                for record in records:
                    if record.get('eventType') == 'grabbed':
                        episode_id = record.get('episode', {}).get('id')
                        if episode_id:
                            if episode_id not in episodes_with_grabs:
                                episodes_with_grabs[episode_id] = []
                            episodes_with_grabs[episode_id].append(record)
                
                # Check episodes with multiple grabs
                problem_episodes = []
                for ep_id, grabs in episodes_with_grabs.items():
                    if len(grabs) > 1:  # Multiple grabs
                        unimported = self.detect_repeated_grabs(ep_id)
                        if unimported:
                            problem_episodes.append({
                                'episode_id': ep_id,
                                'total_grabs': len(grabs),
                                'unimported_grabs': unimported
                            })
                
                if problem_episodes:
                    logger.warning(f"Found {len(problem_episodes)} episodes with repeated grab issues")
                    
                    for episode in problem_episodes:
                        ep_id = episode['episode_id']
                        total = episode['total_grabs']
                        unimported = len(episode['unimported_grabs'])
                        
                        logger.warning(f"  Episode {ep_id}: {total} grabs, {unimported} never imported")
                        
                        # Check if any are currently in queue
                        self.check_episode_queue(ep_id)
                else:
                    logger.info("✅ No repeated grab issues found")
                    
        except Exception as e:
            logger.error(f"Error checking repeated grabs: {e}")
    
    def analyze_queue_item(self, item: Dict) -> Tuple[str, Optional[int], Optional[int], str]:
        """Analyze a queue item and determine action needed"""
        episode = item.get('episode', {})
        episode_id = episode.get('id')
        series = item.get('series', {})
        series_id = series.get('id') if series else None
        
        # Get history for this episode
        history = self.get_history_for_episode(episode_id)
        
        # Find grab score and formats
        download_id = item.get('downloadId')
        grab_score, grab_formats = self.find_detailed_grab_info(history, download_id, series_id)
        
        # Get current file score and formats
        current_score, current_formats = self.get_current_file_details(episode_id, series_id)
        
        # Determine indexer
        indexer = None
        for event in history:
            if event.get('eventType') == 'grabbed' and event.get('downloadId') == download_id:
                data = event.get('data', {})
                indexer = data.get('indexer', '')
                break
        
        # Log analysis
        logger.info(f"\n📊 Analyzing: {series.get('title', 'Unknown')} S{episode.get('seasonNumber', 0):02d}E{episode.get('episodeNumber', 0):02d}")
        logger.info(f"   Title: {item.get('title', 'N/A')}")
        logger.info(f"   Status: {item.get('status', 'Unknown')}")
        logger.info(f"   Grab Score: {grab_score}")
        if grab_formats:
            logger.info(f"   Grab Formats: {', '.join(grab_formats)}")
        logger.info(f"   Current File Score: {current_score}")
        if current_formats:
            logger.info(f"   Current Formats: {', '.join(current_formats)}")
        logger.info(f"   Indexer: {indexer}")
        
        # Show format differences
        if grab_formats and current_formats:
            missing_formats = set(grab_formats) - set(current_formats)
            extra_formats = set(current_formats) - set(grab_formats)
            if missing_formats:
                logger.info(f"   📉 Missing from current: {', '.join(missing_formats)}")
            if extra_formats:
                logger.info(f"   📈 Extra in current: {', '.join(extra_formats)}")
        
        # Determine action and reasoning
        action = "monitor"
        reasoning = ""
        
        if grab_score is not None and current_score is not None:
            score_diff = grab_score - current_score
            threshold = self.config['decisions']['force_import_threshold']
            
            if score_diff >= threshold:
                action = "force_import"
                reasoning = f"Grab score ({grab_score}) is {score_diff} points higher than current file ({current_score})"
                if grab_formats and current_formats:
                    missing = set(grab_formats) - set(current_formats)
                    if missing:
                        reasoning += f". Missing formats: {', '.join(list(missing)[:3])}"
                logger.info(f"   ⚡ Action: Force import - {reasoning}")
            elif score_diff < -threshold:
                if self.is_private_tracker(indexer):
                    action = "keep"
                    reasoning = f"Private tracker protection - keeping despite lower score (diff: {score_diff})"
                    logger.info(f"   ⏸️ Action: Keep - {reasoning}")
                else:
                    action = "remove"
                    reasoning = f"Public tracker with lower score (grab: {grab_score}, current: {current_score}, diff: {score_diff})"
                    logger.info(f"   🗑️ Action: Remove - {reasoning}")
            else:
                action = "wait"
                reasoning = f"Score difference ({score_diff}) within tolerance threshold ({threshold})"
                logger.info(f"   ⏳ Action: Wait - {reasoning}")
        else:
            reasoning = f"Unable to determine scores (grab: {grab_score}, current: {current_score})"
            logger.info(f"   ❓ Action: Monitor - {reasoning}")
        
        return action, grab_score, current_score, reasoning
    
    def test_episode(self, series_title: str, season: int, episode: int):
        """Test analysis for a specific episode"""
        logger.info(f"\n{'='*70}")
        logger.info(f"🧪 TEST MODE: Analyzing {series_title} S{season:02d}E{episode:02d}")
        logger.info(f"{'='*70}")
        
        # Find series
        series = self.get_series_by_title(series_title)
        if not series:
            logger.error(f"❌ Series '{series_title}' not found")
            return
        
        logger.info(f"✓ Found series: {series['title']} (ID: {series['id']})")
        
        # Get episode info
        ep_info = self.get_episode_info(series['id'], season, episode)
        if not ep_info:
            logger.error(f"❌ Episode S{season:02d}E{episode:02d} not found")
            return
        
        episode_id = ep_info['id']
        logger.info(f"✓ Episode ID: {episode_id}")
        logger.info(f"  Has File: {ep_info.get('hasFile', False)}")
        
        # Get current file score and formats
        current_score, current_formats = self.get_current_file_details(episode_id, series['id'])
        logger.info(f"  Current File Score: {current_score}")
        if current_formats:
            logger.info(f"  Current File Formats: {', '.join(current_formats)}")
        
        # Check if episode is in queue
        queue = self.get_queue()
        queue_item = None
        for item in queue:
            if item.get('episode', {}).get('id') == episode_id:
                queue_item = item
                break
        
        if queue_item:
            logger.info(f"\n📥 Episode is currently in queue")
            action, grab_score, current, reasoning = self.analyze_queue_item(queue_item)
            logger.info(f"\n🎯 DECISION: {action.upper()}")
            logger.info(f"   Reasoning: {reasoning}")
            
            if self.dry_run:
                logger.info(f"\n🔸 DRY RUN: No actions will be taken")
        else:
            logger.info(f"\n✨ Episode is not in queue")
        
        # Analyze history
        history = self.get_history_for_episode(episode_id, limit=20)
        
        if history:
            logger.info(f"\n📜 Recent History Analysis:")
            
            grab_events = []
            import_events = []
            
            for event in history:
                event_type = event.get('eventType', '')
                cf_score = event.get('customFormatScore', 0)
                custom_formats = event.get('customFormats', [])
                format_names = [cf.get('name', 'Unknown') for cf in custom_formats]
                
                if event_type == 'grabbed':
                    grab_events.append({
                        'date': event.get('date', ''),
                        'score': cf_score,
                        'title': event.get('sourceTitle', 'N/A'),
                        'indexer': event.get('data', {}).get('indexer', 'Unknown'),
                        'formats': format_names
                    })
                elif event_type in ['downloadFolderImported', 'downloadIgnored']:
                    import_events.append({
                        'date': event.get('date', ''),
                        'score': cf_score,
                        'title': event.get('sourceTitle', 'N/A'),
                        'type': event_type,
                        'formats': format_names
                    })
            
            if grab_events:
                logger.info(f"\n  📥 Recent Grabs:")
                for idx, grab in enumerate(grab_events[:3], 1):
                    logger.info(f"     {idx}. Score: {grab['score']} | {grab['indexer']} | {grab['date'][:19]}")
                    logger.info(f"        {grab['title']}")
                    if grab['formats']:
                        logger.info(f"        Formats: {', '.join(grab['formats'])}")
            
            if import_events:
                logger.info(f"\n  📦 Recent Import Attempts:")
                for idx, imp in enumerate(import_events[:3], 1):
                    status = "✓ Imported" if imp['type'] == 'downloadFolderImported' else "✗ Ignored"
                    logger.info(f"     {idx}. Score: {imp['score']} | {status} | {imp['date'][:19]}")
                    logger.info(f"        {imp['title']}")
                    if imp['formats']:
                        logger.info(f"        Formats: {', '.join(imp['formats'])}")
            
            # Show score discrepancies
            if grab_events and import_events:
                logger.info(f"\n  🔍 Score Analysis:")
                recent_grab = grab_events[0]
                recent_import = import_events[0]
                diff = recent_grab['score'] - recent_import['score']
                
                logger.info(f"     Most recent grab score: {recent_grab['score']}")
                logger.info(f"     Most recent import score: {recent_import['score']}")
                logger.info(f"     Difference: {diff}")
                
                # Show format differences
                if recent_grab['formats'] and recent_import['formats']:
                    grab_formats_set = set(recent_grab['formats'])
                    import_formats_set = set(recent_import['formats'])
                    missing_formats = grab_formats_set - import_formats_set
                    extra_formats = import_formats_set - grab_formats_set
                    
                    if missing_formats:
                        logger.info(f"     📉 Missing during import: {', '.join(missing_formats)}")
                    if extra_formats:
                        logger.info(f"     📈 Added during import: {', '.join(extra_formats)}")
                
                if abs(diff) >= self.config['decisions']['force_import_threshold']:
                    logger.info(f"     ⚠️ Significant score mismatch detected!")
                    logger.info(f"     This would trigger automatic action if in queue")
    
    def process_stuck_imports(self):
        """Process all stuck imports in the queue"""
        queue = self.get_queue()
        
        stuck_items = []
        for item in queue:
            # Check if item is stuck
            if item.get('trackedDownloadState') == 'importPending':
                stuck_items.append(item)
            elif item.get('status') == 'completed' and item.get('trackedDownloadStatus') == 'warning':
                stuck_items.append(item)
            elif item.get('statusMessages'):
                # Check for warning messages
                for msg in item['statusMessages']:
                    if 'already' in str(msg.get('messages', [])).lower():
                        stuck_items.append(item)
                        break
        
        if not stuck_items:
            logger.info("✨ No stuck imports found in queue")
            return
        
        logger.info(f"\n🔍 Found {len(stuck_items)} stuck imports in queue")
        
        for item in stuck_items:
            action, grab_score, current_score, reasoning = self.analyze_queue_item(item)
            
            if action == "force_import":
                success = self.force_import(item)
                if success:
                    if not self.dry_run:
                        logger.info("   ✅ Import forced successfully")
                else:
                    logger.warning("   ❌ Failed to force import")
                    
            elif action == "remove":
                success = self.remove_from_queue(item, delete_files=True)
                if success:
                    if not self.dry_run:
                        logger.info("   ✅ Removed from queue")
                else:
                    logger.warning("   ❌ Failed to remove from queue")
    
    def start_webhook_server(self):
        """Start webhook server in background thread"""
        webhook_config = self.config.get('webhook', {})
            
        host = webhook_config.get('host', '0.0.0.0')
        port = webhook_config.get('port', 8090)
        
        logger.info(f"🌐 Starting webhook server on {host}:{port}")
        
        self.webhook_server = WebhookServer(self)
        
        # Start in background thread
        webhook_thread = threading.Thread(
            target=self.webhook_server.start,
            args=(host, port)
        )
        webhook_thread.daemon = True
        webhook_thread.start()
        
        # Give server time to start
        time.sleep(2)
        logger.info(f"✅ Webhook server started - configure Sonarr to use:")
        logger.info(f"   URL: http://{host}:{port}/sonarr/webhook")
        logger.info(f"   Method: POST")
        logger.info(f"   Events: On Grab, On Import, On Import Failed")
    
    def run(self, once: bool = False, webhook: bool = False):
        """Run the monitoring loop"""
        mode = "DRY RUN" if self.dry_run else "LIVE"
        features = []
        
        if webhook:
            features.append("Webhook")
        if self.config.get('monitoring', {}).get('detect_repeated_grabs', True):
            features.append("Repeated Grab Detection")
            
        feature_str = f" [{', '.join(features)}]" if features else ""
        
        logger.info(f"🚀 Starting Sonarr Import Monitor [{mode}]{feature_str}")
        logger.info(f"   Server: {self.sonarr_url}")
        logger.info(f"   Interval: {self.config['monitoring']['interval']}s")
        logger.info(f"   Force Import Threshold: {self.config['decisions']['force_import_threshold']}")
        
        if self.dry_run:
            logger.info(f"   🔸 DRY RUN MODE - No changes will be made")
        
        # Start webhook server if requested
        if webhook:
            self.start_webhook_server()
        
        while True:
            try:
                logger.info(f"\n{'='*60}")
                logger.info(f"🔄 Checking at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                
                # 1. Check current queue for stuck imports
                self.process_stuck_imports()
                
                # 2. Check for repeated grab patterns (every other cycle to reduce load)
                if hasattr(self, '_cycle_count'):
                    self._cycle_count += 1
                else:
                    self._cycle_count = 1
                    
                if self._cycle_count % 2 == 0 and self.config.get('monitoring', {}).get('detect_repeated_grabs', True):
                    self.check_all_repeated_grabs()
                
                if once:
                    break
                
                # Wait for next interval
                time.sleep(self.config['monitoring']['interval'])
                
            except KeyboardInterrupt:
                logger.info("\n👋 Monitoring stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                if not once:
                    time.sleep(30)  # Wait before retrying

def main():
    parser = argparse.ArgumentParser(
        description='Monitor and fix Sonarr stuck imports',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run monitoring continuously
  python sonarr_import_monitor.py
  
  # Run with webhook server for real-time events
  python sonarr_import_monitor.py --webhook
  
  # Run once and exit
  python sonarr_import_monitor.py --once
  
  # Dry run - see what would happen without making changes
  python sonarr_import_monitor.py --dry-run --once
  
  # Test specific episode
  python sonarr_import_monitor.py --test "SAKAMOTO DAYS" 1 19
  
  # Test with custom config and webhook
  python sonarr_import_monitor.py --config config.yaml --webhook
        """
    )
    parser.add_argument('--config', '-c', help='Path to configuration file')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--dry-run', '-d', action='store_true', help='Dry run - show what would happen without making changes')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('--webhook', '-w', action='store_true', help='Enable webhook server for real-time Sonarr events')
    parser.add_argument('--test', '-t', nargs=3, metavar=('SERIES', 'SEASON', 'EPISODE'),
                       help='Test mode: analyze specific episode (e.g., --test "SAKAMOTO DAYS" 1 19)')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    monitor = SonarrImportMonitor(args.config)
    monitor.dry_run = args.dry_run
    
    if args.test:
        # Test mode for specific episode
        series_title = args.test[0]
        season = int(args.test[1])
        episode = int(args.test[2])
        monitor.test_episode(series_title, season, episode)
    else:
        # Normal monitoring mode
        monitor.run(once=args.once, webhook=args.webhook)

if __name__ == "__main__":
    main()