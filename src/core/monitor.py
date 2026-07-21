"""
Main monitoring logic for Sonarr Import Monitor.
"""

import logging
import signal
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..api.sonarr_client import SonarrClient
from ..api.webhook_server import WebhookServer
from ..config.loader import ConfigLoader
from .analyzer import ScoreAnalyzer

logger = logging.getLogger(__name__)


class SonarrImportMonitor:
    """
    Main monitoring class that orchestrates all components.
    """

    def __init__(self, config: ConfigLoader):
        """
        Initialize the monitor.

        Args:
            config: Configuration loader instance
        """
        self.config = config
        self.app_type = config.get("app.type", "sonarr")
        self.dry_run = False  # Set by CLI args
        self.running = False

        # Initialize components
        self.sonarr_client = SonarrClient(config, app_type=self.app_type)
        self.analyzer = ScoreAnalyzer(config, self.sonarr_client)

        # Webhook server (initialized on demand)
        self.webhook_server: Optional[WebhookServer] = None
        self.webhook_thread: Optional[threading.Thread] = None

        # Statistics
        self.stats: Dict[str, Any] = {
            "start_time": datetime.now(),
            "cycles_completed": 0,
            "items_processed": 0,
            "forced_imports": 0,
            "items_removed": 0,
            "errors_encountered": 0,
        }

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        app_label = "Radarr" if self.app_type == "radarr" else "Sonarr"
        logger.debug(f"{app_label} Import Monitor initialized")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logger.info(f"Received {signal_name} signal, shutting down gracefully...")
        self.shutdown()

    def test_configuration(self) -> bool:
        """
        Test configuration and connectivity.

        Returns:
            True if all tests pass, False otherwise
        """
        logger.info("🧪 Testing configuration and connectivity...")

        try:
            app_label = "Radarr" if self.app_type == "radarr" else "Sonarr"
            content_label = "movies" if self.app_type == "radarr" else "series"

            # Test connection
            if not self.sonarr_client.test_connection():
                logger.error(f"❌ Failed to connect to {app_label}")
                return False

            logger.info(f"✅ {app_label} connection successful")

            # Test configuration loading
            try:
                custom_formats = self.sonarr_client.fetch_custom_formats()
                quality_profiles = self.sonarr_client.fetch_quality_profiles()
                content_map = self.sonarr_client.build_series_profile_map()

                logger.info(f"✅ Loaded {len(custom_formats)} custom formats")
                logger.info(f"✅ Loaded {len(quality_profiles)} quality profiles")
                logger.info(
                    f"✅ Built profile map for {len(content_map)} {content_label}"
                )

            except Exception as e:
                logger.error(f"❌ Failed to load {app_label} configuration: {e}")
                return False

            # Test webhook configuration if enabled (Sonarr only)
            webhook_enabled = self.config.get("webhook.enabled", True)
            if webhook_enabled and self.app_type != "radarr":
                webhook_secret = self.config.get("webhook.secret")
                if webhook_secret:
                    logger.info("✅ Webhook authentication configured")
                else:
                    logger.warning("⚠️ Webhook enabled but no secret configured")

            logger.info("🎉 All tests passed!")
            return True

        except Exception as e:
            logger.error(f"❌ Configuration test failed: {e}")
            return False

    def start_webhook_server(self) -> bool:
        """
        Start the webhook server in a background thread.

        Returns:
            True if started successfully, False otherwise
        """
        if self.app_type == "radarr":
            logger.info("Webhook not supported for Radarr - polling only")
            return False

        if self.webhook_server:
            logger.warning("Webhook server already running")
            return True

        try:
            webhook_config = self.config.get("webhook", {})
            host = webhook_config.get("host", "0.0.0.0")
            port = webhook_config.get("port", 8090)

            # Initialize webhook server
            self.webhook_server = WebhookServer(self, self.config)

            # Start in background thread
            self.webhook_thread = threading.Thread(
                target=self.webhook_server.start, args=(host, port), daemon=True
            )

            self.webhook_thread.start()

            # Give server time to start
            time.sleep(2)

            logger.info(f"✅ Webhook server started on {host}:{port}")
            logger.info(
                f"   Configure Sonarr webhook URL: http://{host}:{port}/webhook/sonarr"
            )

            return True

        except Exception as e:
            logger.error(f"❌ Failed to start webhook server: {e}")
            return False

    def process_stuck_imports(self) -> Dict[str, int]:
        """
        Process all stuck imports in the queue.

        Returns:
            Statistics about processed items
        """
        logger.debug("Checking for stuck imports...")

        queue = self._get_queue_smart()
        if not queue:
            logger.debug("Queue is empty")
            return {"processed": 0, "forced": 0, "removed": 0}

        # Identify stuck items
        stuck_items = self._identify_stuck_items(queue)

        if not stuck_items:
            logger.debug("No stuck imports found")
            return {"processed": 0, "forced": 0, "removed": 0}

        logger.info(f"Found {len(stuck_items)} stuck import(s)")

        results = {"processed": 0, "forced": 0, "removed": 0}

        for item in stuck_items:
            try:
                result = self.process_queue_item(item)
                results["processed"] += 1

                if result == "forced_import":
                    results["forced"] += 1
                elif result == "removed":
                    results["removed"] += 1

            except Exception as e:
                logger.error(f"Error processing queue item: {e}")
                self.stats["errors_encountered"] += 1

        return results

    def _identify_stuck_items(self, queue: List[Dict]) -> List[Dict]:
        """Identify items that appear to be stuck."""
        stuck_items = []

        for item in queue:
            # Check various stuck conditions
            if item.get("trackedDownloadState") == "importPending":
                stuck_items.append(item)
            elif item.get("trackedDownloadState") == "importBlocked":
                stuck_items.append(item)
            elif (
                item.get("status") == "completed"
                and item.get("trackedDownloadStatus") == "warning"
            ):
                stuck_items.append(item)
            elif item.get("statusMessages"):
                # Check for warning messages indicating stuck state
                for msg in item.get("statusMessages", []):
                    message_text = str(msg.get("messages", [])).lower()
                    if any(
                        keyword in message_text
                        for keyword in [
                            "already",
                            "exists",
                            "duplicate",
                            "matched to series by id",
                            "matched to movie by id",
                        ]
                    ):
                        stuck_items.append(item)
                        break

        return stuck_items

    def process_queue_item(self, queue_item: Dict) -> str:
        """
        Process a single queue item.

        Args:
            queue_item: Queue item from Sonarr

        Returns:
            Action taken ('forced_import', 'removed', 'kept', 'error')
        """
        try:
            # Analyze the item
            decision = self.analyzer.analyze_queue_item(queue_item)
            self._log_stuck_item_decision(queue_item, decision)

            # Execute the decision
            if decision.action == "force_import":
                if decision.validated_import_candidate is None:
                    success = self._execute_force_import(queue_item)
                else:
                    success = self._execute_force_import(
                        queue_item, decision.validated_import_candidate
                    )
                if success:
                    self.stats["forced_imports"] += 1
                    return "forced_import"
                else:
                    return "error"

            elif decision.action == "remove":
                success = self._execute_removal(queue_item)
                if success:
                    self.stats["items_removed"] += 1
                    return "removed"
                else:
                    return "error"

            elif decision.action == "keep":
                success = self._execute_keep_action(queue_item)
                if success:
                    logger.info(f"   Kept in download client: {decision.reasoning}")
                    return "kept"
                else:
                    return "error"

            elif decision.action == "wait":
                logger.info(f"   Waiting: {decision.reasoning}")
                return "kept"

            else:
                logger.warning(f"   Monitoring: {decision.reasoning}")
                return "monitored"

        except Exception as e:
            logger.error(f"Error processing queue item: {e}")
            self.stats["errors_encountered"] += 1
            return "error"

    def _log_stuck_item_decision(self, queue_item: Dict, decision) -> None:
        """Emit one visible result line for every stuck queue item."""
        queue_id = queue_item.get("id")
        download_id = queue_item.get("downloadId")
        if self.app_type == "radarr":
            movie_id = queue_item.get("movie", {}).get("id")
            target = f"movieId={movie_id}"
        else:
            series_id = queue_item.get("series", {}).get("id")
            episode_id = queue_item.get("episode", {}).get("id")
            target = f"seriesId={series_id} episodeId={episode_id}"

        message = (
            f"Stuck item result: queueId={queue_id} downloadId={download_id} "
            f"{target} grabScore={decision.grab_score} "
            f"currentScore={decision.current_score} action={decision.action} "
            f"reason={decision.reasoning}"
        )
        if decision.action == "monitor":
            logger.warning(message)
        else:
            logger.info(message)

    def _get_content_id(self, queue_item: Dict) -> Optional[int]:
        """Extract content ID (series or movie) from a queue item."""
        if self.app_type == "radarr":
            content = queue_item.get("movie", {})
        else:
            content = queue_item.get("series", {})
        return content.get("id") if content else None

    def _execute_force_import(
        self, queue_item: Dict, validated_candidate: Optional[Dict] = None
    ) -> bool:
        """Execute force import action."""
        if self.dry_run:
            logger.info("   🔸 DRY RUN: Would force import")
            return True

        download_id = queue_item.get("downloadId")
        quality = queue_item.get("quality")
        output_path = queue_item.get("outputPath")
        content_id = self._get_content_id(queue_item)

        if not download_id:
            logger.error("   Missing download ID for force import")
            return False

        if validated_candidate is None:
            success, command_id = self.sonarr_client.force_import(
                download_id,
                quality,
                output_path=output_path,
                content_id=content_id,
            )
        else:
            success, command_id = self.sonarr_client.force_import_candidate(
                validated_candidate,
                quality,
            )

        if success:
            logger.info("   Forced import OK")
            if command_id:
                self._verify_import_and_cleanup(
                    download_id,
                    queue_item.get("id"),
                    command_id,
                    remove_from_client_on_stuck=validated_candidate is None,
                )
        elif command_id == -1:
            # No import candidates from any method - item is permanently unimportable
            logger.warning(
                "   No import candidates available - removing unimportable item from queue"
            )
            queue_id = queue_item.get("id")
            if queue_id:
                removed = self.sonarr_client.remove_from_queue(
                    queue_id, remove_from_client=True, blocklist=False
                )
                if removed:
                    logger.info("   Removed unimportable item")
                    return True
                else:
                    logger.error("   ❌ Failed to remove unimportable item from queue")
        else:
            logger.error("   ❌ Failed to force import")

        return success

    def _is_queue_item_still_stuck(self, queue_id: int) -> bool:
        """Check if a queue item is still in the queue with a stuck state."""
        try:
            queue = self.sonarr_client.get_queue()
            for item in queue:
                if item.get("id") == queue_id:
                    state = item.get("trackedDownloadState", "")
                    return state in ("importPending", "importBlocked")
            return False  # Item no longer in queue - import worked
        except Exception as e:
            logger.debug(f"Error checking queue item status: {e}")
            return False

    def _verify_import_and_cleanup(
        self,
        download_id: str,
        queue_id: Optional[int],
        command_id: int,
        remove_from_client_on_stuck: bool = True,
    ):
        """Wait for import command to process and clean up stuck items."""
        logger.debug(f"   Waiting for import command {command_id} to process...")
        time.sleep(8)

        logger.debug("   Checking for post-import queue cleanup...")
        if self.sonarr_client.cleanup_post_import_queue_item(download_id):
            logger.debug("   Cleaned up stuck queue item after import")
            return

        if queue_id and self._is_queue_item_still_stuck(queue_id):
            if remove_from_client_on_stuck:
                logger.warning(
                    "   Import command accepted but item still stuck - removing "
                    "from queue and download client"
                )
            else:
                logger.warning(
                    "   Validated cross-content import command accepted but item "
                    "still stuck - queue-only cleanup; download remains in client"
                )
            removed = self.sonarr_client.remove_from_queue(
                queue_id,
                remove_from_client=remove_from_client_on_stuck,
                blocklist=False,
            )
            if removed:
                logger.info("   Removed still-stuck item")
            else:
                logger.error("   ❌ Failed to remove still-stuck item")

    def _execute_removal(self, queue_item: Dict) -> bool:
        """Execute removal action."""
        if self.dry_run:
            logger.info("   🔸 DRY RUN: Would remove from queue")
            return True

        queue_id = queue_item.get("id")
        if not queue_id:
            logger.error("   Missing queue ID for removal")
            return False

        # Remove from client and queue (but don't blocklist)
        success = self.sonarr_client.remove_from_queue(
            queue_id, remove_from_client=True, blocklist=False
        )

        if success:
            logger.debug("   Removed from queue")
        else:
            logger.error("   ❌ Failed to remove from queue")

        return success

    def _execute_keep_action(self, queue_item: Dict) -> bool:
        """Execute keep action - remove from Sonarr queue but keep in download client."""
        if self.dry_run:
            logger.info(
                "   🔸 DRY RUN: Would remove from Sonarr queue but keep in download client"
            )
            return True

        queue_id = queue_item.get("id")
        if not queue_id:
            logger.error("   Missing queue ID for keep action")
            return False

        # Remove from Sonarr queue only, keep in download client (don't blocklist)
        success = self.sonarr_client.remove_from_queue(
            queue_id, remove_from_client=False, blocklist=False
        )

        if success:
            logger.info("   Removed from queue, kept in download client")
        else:
            logger.error("   ❌ Failed to remove from Sonarr queue")

        return success

    def check_repeated_grabs(self) -> int:
        """
        Check for repeated grab patterns across all episodes.

        Returns:
            Number of problematic episodes found
        """
        logger.debug("Checking for repeated grab patterns...")

        try:
            # Get recent history
            response = self.sonarr_client._make_request(
                "GET",
                "/history",
                params={
                    "pageSize": 200,
                    "sortKey": "date",
                    "sortDirection": "descending",
                },
            )

            records = response.json().get("records", [])

            # Group by content (episode or movie)
            content_key = "movie" if self.app_type == "radarr" else "episode"
            episodes_with_grabs = defaultdict(list)
            for record in records:
                if record.get("eventType") == "grabbed":
                    content_id = record.get(content_key, {}).get("id")
                    if content_id:
                        episodes_with_grabs[content_id].append(record)

            # Check episodes with multiple grabs
            problem_episodes = 0
            for ep_id, grabs in episodes_with_grabs.items():
                if len(grabs) > 1:
                    unimported = self.analyzer.detect_repeated_grabs(ep_id)
                    if unimported:
                        problem_episodes += 1
                        logger.warning(
                            f"  Episode {ep_id}: {len(grabs)} grabs, {len(unimported)} unimported"
                        )

                        # Check if any are currently in queue
                        self.check_episode_queue(ep_id)

            if problem_episodes == 0:
                logger.debug("No repeated grab issues found")
            else:
                logger.warning(
                    f"Found {problem_episodes} episodes with repeated grab issues"
                )

            return problem_episodes

        except Exception as e:
            logger.error(f"Error checking repeated grabs: {e}")
            return 0

    def check_episode_queue(self, episode_id: int):
        """
        Check queue for specific episode and process if found.

        Args:
            episode_id: Sonarr episode ID
        """
        try:
            queue = self._get_queue_smart()
            content_key = "movie" if self.app_type == "radarr" else "episode"

            for item in queue:
                item_id = item.get(content_key, {}).get("id")
                if item_id == episode_id:
                    label = "movie" if self.app_type == "radarr" else "episode"
                    logger.debug(f"Found {label} {episode_id} in queue")
                    self.process_queue_item(item)
                    return

            label = "Movie" if self.app_type == "radarr" else "Episode"
            logger.debug(f"{label} {episode_id} not found in current queue")

        except Exception as e:
            logger.error(f"Error checking episode queue: {e}")

    def check_download_queue(self, download_id: str):
        """
        Check queue for specific download ID and process if found.

        Args:
            download_id: Sonarr download ID
        """
        try:
            queue = self._get_queue_smart()

            for item in queue:
                if item.get("downloadId") == download_id:
                    logger.debug(f"Found download {download_id} in queue")
                    self.process_queue_item(item)
                    return

            logger.debug(f"Download {download_id} not found in current queue")

        except Exception as e:
            logger.error(f"Error checking download queue: {e}")

    def test_specific_episode(self, series_title: str, season: int, episode: int):
        """
        Test analysis for a specific episode.

        Args:
            series_title: Series title to search for
            season: Season number
            episode: Episode number
        """
        logger.info(f"\n{'='*70}")
        logger.info(
            f"🧪 TEST MODE: Analyzing {series_title} S{season:02d}E{episode:02d}"
        )
        logger.info(f"{'='*70}")

        try:
            # Find series
            series = self.sonarr_client.get_series_by_title(series_title)
            if not series:
                logger.error(f"❌ Series '{series_title}' not found")
                return

            logger.info(f"✓ Found series: {series['title']} (ID: {series['id']})")

            # Get episode info
            ep_info = self.sonarr_client.get_episode_info(series["id"], season, episode)
            if not ep_info:
                logger.error(f"❌ Episode S{season:02d}E{episode:02d} not found")
                return

            episode_id = ep_info["id"]
            logger.info(f"✓ Episode ID: {episode_id}")
            logger.info(f"  Has File: {ep_info.get('hasFile', False)}")

            # Get current file details
            current_score, current_formats = self.analyzer.get_current_file_details(
                episode_id, series["id"]
            )
            logger.info(f"  Current File Score: {current_score}")
            if current_formats:
                logger.info(f"  Current Formats: {', '.join(current_formats)}")

            self._test_content_queue_and_history(episode_id, "episode", "Episode")

        except Exception as e:
            logger.error(f"Error testing episode: {e}")

    def _get_queue_smart(self) -> List[Dict]:
        """Get queue using cached version in production, uncached in tests."""
        from unittest.mock import MagicMock

        if hasattr(self.sonarr_client, "cache") and not isinstance(
            self.sonarr_client, MagicMock
        ):
            return self.sonarr_client.get_queue_cached()
        return self.sonarr_client.get_queue()

    def _test_content_queue_and_history(
        self, content_id: int, queue_key: str, label: str
    ):
        """Check queue and show history for test mode content."""
        queue = self._get_queue_smart()
        queue_item = None
        for item in queue:
            if item.get(queue_key, {}).get("id") == content_id:
                queue_item = item
                break

        if queue_item:
            logger.info(f"\n📥 {label} is currently in queue")
            decision = self.analyzer.analyze_queue_item(queue_item)
            logger.info(f"\n🎯 DECISION: {decision.action.upper()}")
            logger.info(f"   Reasoning: {decision.reasoning}")
            if self.dry_run:
                logger.info("\n🔸 DRY RUN: No actions will be taken")
        else:
            logger.info(f"\n✨ {label} is not in queue")
            history = self.sonarr_client.get_history_for_episode(content_id, limit=10)
            if history:
                self._show_history_analysis(history, content_id, content_id)

    def test_specific_movie(self, movie_title: str):
        """
        Test analysis for a specific movie.

        Args:
            movie_title: Movie title to search for
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"🧪 TEST MODE: Analyzing movie '{movie_title}'")
        logger.info(f"{'='*70}")

        try:
            movie = self.sonarr_client.get_series_by_title(movie_title)
            if not movie:
                logger.error(f"❌ Movie '{movie_title}' not found")
                return

            movie_id = movie["id"]
            year = movie.get("year", "")
            logger.info(f"✓ Found movie: {movie['title']} ({year}) (ID: {movie_id})")
            logger.info(f"  Has File: {movie.get('hasFile', False)}")

            current_score, current_formats = self.analyzer.get_current_file_details(
                movie_id, movie_id
            )
            logger.info(f"  Current File Score: {current_score}")
            if current_formats:
                logger.info(f"  Current Formats: {', '.join(current_formats)}")

            self._test_content_queue_and_history(movie_id, "movie", "Movie")

        except Exception as e:
            logger.error(f"Error testing movie: {e}")

    def _show_history_analysis(
        self, history: List[Dict], episode_id: int, series_id: int
    ):
        """Show detailed history analysis for testing."""
        logger.info(f"\n📜 Recent History Analysis:")

        grab_events = []
        import_events = []

        for event in history:
            event_type = event.get("eventType", "")

            if event_type == "grabbed":
                grab_events.append(event)
            elif event_type in ["downloadFolderImported", "downloadIgnored"]:
                import_events.append(event)

        # Show recent grabs
        if grab_events:
            logger.info(f"\n  📥 Recent Grabs:")
            for idx, grab in enumerate(grab_events[:3], 1):
                score = grab.get("customFormatScore", 0)
                indexer = grab.get("data", {}).get("indexer", "Unknown")
                date = grab.get("date", "")
                title = grab.get("sourceTitle", "N/A")

                logger.info(f"     {idx}. Score: {score} | {indexer} | {date[:19]}")
                logger.info(f"        {title[:60]}...")

                formats = grab.get("customFormats", [])
                if formats:
                    format_names = [cf.get("name", "Unknown") for cf in formats]
                    logger.info(f"        Formats: {', '.join(format_names)}")

        # Show recent imports
        if import_events:
            logger.info(f"\n  📦 Recent Import Attempts:")
            for idx, imp in enumerate(import_events[:3], 1):
                score = imp.get("customFormatScore", 0)
                status = (
                    "✓ Imported"
                    if imp["eventType"] == "downloadFolderImported"
                    else "✗ Ignored"
                )
                date = imp.get("date", "")

                logger.info(f"     {idx}. Score: {score} | {status} | {date[:19]}")

                formats = imp.get("customFormats", [])
                if formats:
                    format_names = [cf.get("name", "Unknown") for cf in formats]
                    logger.info(f"        Formats: {', '.join(format_names)}")

        # Compare most recent grab vs import
        if grab_events and import_events:
            recent_grab = grab_events[0]
            recent_import = import_events[0]

            grab_score = recent_grab.get("customFormatScore", 0)
            import_score = recent_import.get("customFormatScore", 0)
            diff = grab_score - import_score

            logger.info(f"\n  🔍 Score Analysis:")
            logger.info(f"     Most recent grab score: {grab_score}")
            logger.info(f"     Most recent import score: {import_score}")
            logger.info(f"     Difference: {diff}")

            threshold = self.config.get("decisions.force_import_threshold", 10)
            if abs(diff) >= threshold:
                logger.info(f"     ⚠️ Significant score mismatch detected!")

    def run_continuous(self, enable_webhook: bool = False):
        """
        Run continuous monitoring loop.

        Args:
            enable_webhook: Whether to start webhook server
        """
        interval = self.config.get("monitoring.interval", 60)
        detect_repeated_grabs = self.config.get(
            "monitoring.detect_repeated_grabs", True
        )

        mode = "DRY RUN" if self.dry_run else "LIVE"
        features = []

        if enable_webhook and self.app_type != "radarr":
            features.append("Webhook")
            if not self.start_webhook_server():
                logger.error("Failed to start webhook server")
                return False

        if detect_repeated_grabs:
            features.append("Repeated Grab Detection")

        feature_str = f" [{', '.join(features)}]" if features else ""

        app_label = "Radarr" if self.app_type == "radarr" else "Sonarr"
        logger.info(f"🚀 Starting {app_label} Import Monitor [{mode}]{feature_str}")
        prefix = "radarr" if self.app_type == "radarr" else "sonarr"
        logger.info(
            f"   Server: {self.config.get(f'{prefix}.url')} | "
            f"Interval: {interval}s | "
            f"Threshold: {self.config.get('decisions.force_import_threshold')}"
            f"{' | DRY RUN' if self.dry_run else ''}"
        )

        self.running = True
        cycle_count = 0

        try:
            while self.running:
                cycle_count += 1
                logger.debug(
                    f"Cycle {cycle_count} at {datetime.now().strftime('%H:%M:%S')}"
                )

                try:
                    # Process stuck imports
                    results = self.process_stuck_imports()
                    self.stats["cycles_completed"] += 1
                    self.stats["items_processed"] += results["processed"]

                    # Check repeated grabs (every other cycle to reduce load)
                    if cycle_count % 2 == 0 and detect_repeated_grabs:
                        self.check_repeated_grabs()

                    # Log stats periodically
                    if cycle_count % 10 == 0:
                        self._log_statistics()

                except Exception as e:
                    logger.error(f"Error in monitoring cycle: {e}")
                    self.stats["errors_encountered"] += 1

                # Sleep until next cycle
                if self.running:
                    time.sleep(interval)

        except KeyboardInterrupt:
            logger.info("\n👋 Monitoring stopped by user")
        except Exception as e:
            logger.error(f"Critical error in monitoring loop: {e}")
        finally:
            self.shutdown()

        return True

    def run_once(self):
        """Run a single monitoring cycle."""
        logger.debug("Running single monitoring cycle...")

        try:
            results = self.process_stuck_imports()

            if self.config.get("monitoring.detect_repeated_grabs", True):
                self.check_repeated_grabs()

            logger.debug("Single cycle completed")
            self._log_statistics()

            return True

        except Exception as e:
            logger.error(f"Error in single cycle: {e}")
            return False

    def _log_statistics(self):
        """Log current statistics."""
        uptime = datetime.now() - self.stats["start_time"]

        logger.info(
            f"Stats: {uptime} uptime | "
            f"{self.stats['cycles_completed']} cycles | "
            f"{self.stats['items_processed']} processed | "
            f"{self.stats['forced_imports']} imported | "
            f"{self.stats['items_removed']} removed | "
            f"{self.stats['errors_encountered']} errors"
        )

    def shutdown(self):
        """Shutdown the monitor gracefully."""
        logger.debug("Shutting down...")

        self.running = False

        # Shutdown webhook server
        if self.webhook_server:
            try:
                self.webhook_server.shutdown()
            except Exception:
                pass

        # Clear caches
        self.sonarr_client.clear_cache()

        logger.debug("Shutdown complete")
        self._log_statistics()
