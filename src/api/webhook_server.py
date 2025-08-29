"""
Secure webhook server with authentication and rate limiting.
"""

import hashlib
import hmac
import logging
import threading
from collections import defaultdict
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, Optional

from flask import Flask, jsonify, request

from ..config.loader import ConfigLoader
from ..utils.decorators import RateLimiter

logger = logging.getLogger(__name__)


class WebhookServer:
    """
    Secure Flask webhook server with authentication and rate limiting.
    """

    def __init__(self, monitor_instance, config: ConfigLoader):
        """
        Initialize webhook server.

        Args:
            monitor_instance: Main monitor instance for processing events
            config: Configuration loader
        """
        self.monitor = monitor_instance
        self.config = config

        # Flask app setup
        self.app = Flask(__name__)
        self.app.logger.disabled = True  # Disable Flask default logging

        # Suppress Werkzeug logging except for errors
        logging.getLogger("werkzeug").setLevel(logging.ERROR)

        # Security settings
        self.webhook_secret = config.get("webhook.secret")

        # Rate limiting
        self.rate_limiter = RateLimiter()
        self.max_requests_per_minute = 30

        # Event cache for grab/import correlation
        self.grab_cache: Dict[int, Dict[str, Any]] = {}  # episode_id -> grab_info

        # Metrics
        self.metrics = {
            "requests_total": 0,
            "requests_authenticated": 0,
            "requests_rejected": 0,
            "events_processed": defaultdict(int),
            "start_time": datetime.now(),
        }

        self.setup_routes()
        logger.info("Webhook server initialized with security features")

    def verify_webhook_signature(self, f: Callable) -> Callable:
        """
        Decorator to verify webhook signatures and rate limit.

        Args:
            f: Function to wrap

        Returns:
            Wrapped function with authentication
        """

        @wraps(f)
        def decorated_function(*args, **kwargs):
            self.metrics["requests_total"] += 1
            client_ip = request.remote_addr

            # Rate limiting check
            if not self.rate_limiter.is_allowed(
                client_ip, self.max_requests_per_minute, 60
            ):
                self.metrics["requests_rejected"] += 1
                logger.warning(f"Rate limit exceeded for IP {client_ip}")
                return (
                    jsonify(
                        {
                            "error": "Rate limit exceeded",
                            "limit": f"{self.max_requests_per_minute} requests per minute",
                        }
                    ),
                    429,
                )

            # Skip authentication for health endpoint
            if request.endpoint == "health":
                return f(*args, **kwargs)

            # Authenticate webhook request
            if not self._authenticate_request():
                self.metrics["requests_rejected"] += 1
                return jsonify({"error": "Authentication failed"}), 401

            self.metrics["requests_authenticated"] += 1
            return f(*args, **kwargs)

        return decorated_function

    def _authenticate_request(self) -> bool:
        """
        Authenticate incoming webhook request.

        Returns:
            True if authenticated, False otherwise
        """
        if not self.webhook_secret:
            logger.warning(
                "No webhook secret configured - allowing unauthenticated access"
            )
            return True

        # Method 1: Check X-Webhook-Secret header
        provided_secret = request.headers.get("X-Webhook-Secret")
        if provided_secret:
            if hmac.compare_digest(provided_secret, self.webhook_secret):
                return True
            else:
                logger.warning(f"Invalid webhook secret from {request.remote_addr}")
                return False

        # Method 2: Check HMAC signature
        signature_header = request.headers.get("X-Webhook-Signature")
        if signature_header:
            try:
                # Expected format: sha256=<hex_digest>
                if not signature_header.startswith("sha256="):
                    return False

                provided_signature = signature_header[7:]  # Remove 'sha256=' prefix

                # Calculate expected signature
                expected_signature = hmac.new(
                    self.webhook_secret.encode(), request.data, hashlib.sha256
                ).hexdigest()

                if hmac.compare_digest(provided_signature, expected_signature):
                    return True
                else:
                    logger.warning(f"Invalid HMAC signature from {request.remote_addr}")
                    return False

            except Exception as e:
                logger.error(f"Error validating HMAC signature: {e}")
                return False

        # No authentication method provided
        logger.warning(
            f"No authentication credentials provided from {request.remote_addr}"
        )
        return False

    def setup_routes(self):
        """Setup Flask routes with authentication."""

        @self.app.route("/health")
        @self.verify_webhook_signature
        def health():
            """Public health check endpoint."""
            uptime = datetime.now() - self.metrics["start_time"]

            return (
                jsonify(
                    {
                        "status": "healthy",
                        "service": "Sonarr Import Monitor Webhook",
                        "version": "2.0.0",
                        "timestamp": datetime.now().isoformat(),
                        "uptime_seconds": int(uptime.total_seconds()),
                        "cache_size": len(self.grab_cache),
                    }
                ),
                200,
            )

        @self.app.route("/metrics")
        @self.verify_webhook_signature
        def metrics():
            """Metrics endpoint for monitoring."""
            uptime = datetime.now() - self.metrics["start_time"]

            return (
                jsonify(
                    {
                        "uptime_seconds": int(uptime.total_seconds()),
                        "requests_total": self.metrics["requests_total"],
                        "requests_authenticated": self.metrics[
                            "requests_authenticated"
                        ],
                        "requests_rejected": self.metrics["requests_rejected"],
                        "rate_limit_per_minute": self.max_requests_per_minute,
                        "events_processed": dict(self.metrics["events_processed"]),
                        "active_grab_cache_size": len(self.grab_cache),
                        "webhook_secret_configured": bool(self.webhook_secret),
                    }
                ),
                200,
            )

        @self.app.route("/webhook/sonarr", methods=["POST"])
        @self.verify_webhook_signature
        def webhook_handler():
            """Main webhook endpoint for Sonarr events."""
            try:
                try:
                    data = request.json
                except Exception as json_error:
                    logger.error(f"Invalid JSON in webhook request: {json_error}")
                    return jsonify({"error": "Invalid JSON data"}), 400

                if not data:
                    return jsonify({"error": "No JSON data provided"}), 400

                event_type = data.get("eventType", "Unknown")
                self.metrics["events_processed"][event_type] += 1

                # Log webhook event
                self._log_webhook_event(event_type, data)

                # Route to appropriate handler
                handlers = {
                    "Test": self.handle_test,
                    "Grab": self.handle_grab,
                    "Download": self.handle_download,
                    "ManualInteractionRequired": self.handle_manual_interaction,
                    "HealthIssue": self.handle_health_issue,
                }

                handler = handlers.get(event_type)
                if handler:
                    return handler(data)
                else:
                    logger.info(f"📨 Unhandled webhook event: {event_type}")
                    return jsonify({"status": "ignored", "event_type": event_type}), 200

            except Exception as e:
                logger.error(f"Webhook processing error: {e}", exc_info=True)
                return jsonify({"error": "Internal server error"}), 500

        @self.app.route("/webhook/sonarr", methods=["GET"])
        @self.verify_webhook_signature
        def webhook_info():
            """Information endpoint about webhook configuration."""
            return (
                jsonify(
                    {
                        "service": "Sonarr Import Monitor Webhook",
                        "version": "2.0.0",
                        "webhook_path": "/webhook/sonarr",
                        "supported_events": [
                            "Test",
                            "Grab",
                            "Download",
                            "ManualInteractionRequired",
                            "HealthIssue",
                        ],
                        "authentication_required": bool(self.webhook_secret),
                        "rate_limit": f"{self.max_requests_per_minute} requests/minute",
                        "cache_size": len(self.grab_cache),
                        "supported_methods": ["POST"],
                        "content_type": "application/json",
                    }
                ),
                200,
            )

    def _log_webhook_event(self, event_type: str, data: Dict[str, Any]):
        """Log webhook event details."""
        series = data.get("series", {})
        episodes = data.get("episodes", [])

        series_title = series.get("title", "Unknown")

        if episodes:
            ep = episodes[0]
            episode_str = (
                f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}"
            )
        else:
            episode_str = "N/A"

        logger.info(
            f"📨 {event_type} webhook: {series_title} {episode_str} from {request.remote_addr}"
        )

    def handle_test(self, data: Dict[str, Any]) -> tuple:
        """Handle test webhook from Sonarr."""
        series = data.get("series", {})
        episodes = data.get("episodes", [])

        logger.info("🧪 Webhook test received and authenticated")
        logger.info(f"   Series: {series.get('title', 'Unknown')}")

        if episodes:
            ep = episodes[0]
            logger.info(
                f"   Episode: S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}"
            )

        return (
            jsonify(
                {
                    "status": "success",
                    "message": "Webhook test successful! Authentication working.",
                    "timestamp": datetime.now().isoformat(),
                    "authenticated": True,
                }
            ),
            200,
        )

    def handle_grab(self, data: Dict[str, Any]) -> tuple:
        """Handle grab event - cache for later comparison."""
        try:
            episodes = data.get("episodes", [])
            release = data.get("release", {})
            series = data.get("series", {})

            # Build grab info for caching
            grab_info = {
                "timestamp": datetime.now(),
                "download_id": data.get("downloadId"),
                "download_client": data.get("downloadClient"),
                "score": release.get("customFormatScore", 0),
                "formats": release.get("customFormats", []),
                "title": release.get("releaseTitle", "Unknown"),
                "indexer": release.get("indexer", "Unknown"),
                "series_title": series.get("title", "Unknown"),
            }

            logger.info(f"📥 Grab: {grab_info['series_title']}")
            logger.info(f"   Release: {grab_info['title'][:60]}...")
            logger.info(f"   Score: {grab_info['score']}")
            logger.info(f"   Download ID: {grab_info['download_id']}")

            # Cache grab info for each episode
            for episode in episodes:
                ep_id = episode["id"]
                self.grab_cache[ep_id] = grab_info

                # Schedule delayed import check
                delay = self.config.get("webhook.import_check_delay", 600)
                self._schedule_delayed_check(ep_id, grab_info["download_id"], delay)

            return (
                jsonify(
                    {
                        "status": "success",
                        "message": "Grab event cached for monitoring",
                        "episodes_cached": len(episodes),
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Error handling grab webhook: {e}")
            return jsonify({"error": str(e)}), 500

    def handle_download(self, data: Dict[str, Any]) -> tuple:
        """Handle successful import - check for score issues."""
        try:
            episodes = data.get("episodes", [])
            episode_file = data.get("episodeFile", {})
            series = data.get("series", {})
            import_score = episode_file.get("customFormatScore", 0)

            logger.info(f"📦 Import: {series.get('title', 'Unknown')}")
            logger.info(f"   Import score: {import_score}")

            for episode in episodes:
                ep_id = episode["id"]

                # Check if we have cached grab info
                if ep_id in self.grab_cache:
                    grab_info = self.grab_cache[ep_id]
                    grab_score = grab_info["score"]
                    score_diff = grab_score - import_score

                    logger.info(f"   Grab score: {grab_score}")
                    logger.info(f"   Score difference: {score_diff}")

                    # Check for unexpected score mismatch on successful import
                    threshold = self.config.get("decisions.force_import_threshold", 10)
                    if score_diff > threshold:
                        logger.warning(f"⚠️ Score mismatch on successful import!")
                        logger.warning(
                            f"   This shouldn't happen - investigate further"
                        )

                    # Clear from cache - successfully imported
                    del self.grab_cache[ep_id]
                    logger.debug(f"   Cleared episode {ep_id} from cache")

            return (
                jsonify(
                    {"status": "success", "message": "Import processed successfully"}
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Error handling download webhook: {e}")
            return jsonify({"error": str(e)}), 500

    def handle_manual_interaction(self, data: Dict[str, Any]) -> tuple:
        """Handle ManualInteractionRequired webhook."""
        try:
            download_id = data.get("downloadId")
            series = data.get("series", {})
            status_messages = data.get("downloadStatusMessages", [])

            logger.warning(
                f"⚠️ Manual interaction required: {series.get('title', 'Unknown')}"
            )

            # Log status messages for debugging
            for msg in status_messages:
                if msg.get("messages"):
                    logger.info(f"   Status: {msg.get('messages')}")

            # Schedule immediate check for this download
            if download_id:
                logger.info(f"   Scheduling immediate check for download {download_id}")
                self._schedule_immediate_check_by_download_id(download_id)

            return (
                jsonify(
                    {
                        "status": "success",
                        "message": "Manual interaction webhook received",
                        "download_id": download_id,
                    }
                ),
                200,
            )

        except Exception as e:
            logger.error(f"Error handling manual interaction webhook: {e}")
            return jsonify({"error": str(e)}), 500

    def handle_health_issue(self, data: Dict[str, Any]) -> tuple:
        """Handle health issue notifications."""
        issue_type = data.get("level", "Unknown")
        message = data.get("message", "No message")

        logger.warning(f"🏥 Health issue ({issue_type}): {message}")

        return (
            jsonify({"status": "acknowledged", "message": "Health issue logged"}),
            200,
        )

    def _schedule_delayed_check(self, episode_id: int, download_id: str, delay: int):
        """Schedule delayed import check."""
        timer = threading.Timer(
            delay, self._check_if_imported, args=[episode_id, download_id]
        )
        timer.daemon = True
        timer.start()

        logger.info(f"   Scheduled import check for episode {episode_id} in {delay}s")

    def _schedule_immediate_check(self, episode_id: int):
        """Schedule immediate queue check."""
        timer = threading.Timer(5, self.monitor.check_episode_queue, args=[episode_id])
        timer.daemon = True
        timer.start()

    def _schedule_immediate_check_by_download_id(self, download_id: str):
        """Schedule immediate queue check by download ID."""
        timer = threading.Timer(
            5, self.monitor.check_download_queue, args=[download_id]
        )
        timer.daemon = True
        timer.start()

    def _check_if_imported(self, episode_id: int, download_id: str):
        """Check if grab was imported after delay."""
        try:
            if episode_id not in self.grab_cache:
                logger.debug(
                    f"Episode {episode_id} already cleared from cache (likely imported)"
                )
                return

            grab_info = self.grab_cache[episode_id]
            logger.info(f"🔍 Checking delayed import for episode {episode_id}")
            logger.info(f"   Original grab: {grab_info['title'][:50]}...")

            # Check if still in queue
            queue = self.monitor.sonarr_client.get_queue()
            queue_item = self._find_queue_item(queue, episode_id, download_id)

            if queue_item:
                status = queue_item.get("status", "unknown")
                state = queue_item.get("trackedDownloadState", "unknown")

                logger.warning(
                    f"⏰ Download still in queue after {self.config.get('webhook.import_check_delay', 600)}s delay"
                )
                logger.info(f"   Status: {status}, State: {state}")

                if status == "completed" or state == "importPending":
                    logger.info(
                        "   Download completed but not importing - triggering analysis"
                    )
                    # Use monitor's analysis logic
                    if hasattr(self.monitor, "process_queue_item"):
                        self.monitor.process_queue_item(queue_item)
            else:
                # Check if imported silently (no webhook received)
                if self._was_imported_silently(episode_id, download_id):
                    logger.info(f"   Episode imported silently (no webhook)")
                    if episode_id in self.grab_cache:
                        del self.grab_cache[episode_id]
                else:
                    logger.warning(
                        f"   Episode never imported and not in queue - possible issue"
                    )

        except Exception as e:
            logger.error(f"Error checking delayed import: {e}")

    def _find_queue_item(
        self, queue: list, episode_id: int, download_id: str
    ) -> Optional[Dict]:
        """Find specific queue item by episode and download ID."""
        for item in queue:
            if item.get("downloadId") == download_id:
                episode = item.get("episode", {})
                if episode.get("id") == episode_id:
                    return item
        return None

    def _was_imported_silently(self, episode_id: int, download_id: str) -> bool:
        """Check if episode was imported without webhook notification."""
        try:
            history = self.monitor.sonarr_client.get_history_for_episode(
                episode_id, limit=10
            )

            # Look for recent import with matching download_id
            for event in history:
                if event.get("eventType") == "downloadFolderImported":
                    if event.get("downloadId") == download_id:
                        return True

            return False

        except Exception as e:
            logger.error(f"Error checking silent import: {e}")
            return False

    def start(self, host: str = "0.0.0.0", port: int = 8090):
        """
        Start the webhook server.

        Args:
            host: Host to bind to
            port: Port to listen on
        """
        logger.info(f"🚀 Starting secure webhook server on {host}:{port}")

        if self.webhook_secret:
            logger.info("🔒 Webhook authentication enabled")
            logger.info(
                "   Configure Sonarr with X-Webhook-Secret header or HMAC signatures"
            )
        else:
            logger.warning("⚠️ No webhook secret configured - authentication disabled!")

        try:
            self.app.run(
                host=host, port=port, debug=False, use_reloader=False, threaded=True
            )
        except Exception as e:
            logger.error(f"Failed to start webhook server: {e}")
            raise

    def shutdown(self):
        """Shutdown webhook server gracefully."""
        logger.info("Shutting down webhook server...")
        # Clear cache
        self.grab_cache.clear()
        logger.info("Webhook server shutdown complete")
