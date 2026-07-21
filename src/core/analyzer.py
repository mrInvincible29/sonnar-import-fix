"""
Score analysis and decision making logic for Sonarr imports.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Optional, Tuple

from ..api.sonarr_client import SonarrClient
from ..config.loader import ConfigLoader

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    """Represents an import decision with reasoning."""

    action: str  # 'force_import', 'remove', 'keep', 'wait', 'monitor'
    grab_score: Optional[int]
    current_score: Optional[int]
    score_difference: Optional[int]
    reasoning: str
    grab_formats: List[str]
    current_formats: List[str]
    missing_formats: List[str]
    extra_formats: List[str]
    is_private_tracker: bool
    validated_import_candidate: Optional[Dict] = None


class FormatAnalysis(NamedTuple):
    """Analysis of custom format differences."""

    total_score: int
    format_names: List[str]


class ScoreAnalyzer:
    """
    Analyzes custom format scores and makes import decisions.
    """

    def __init__(self, config: ConfigLoader, sonarr_client: SonarrClient):
        """
        Initialize score analyzer.

        Args:
            config: Configuration loader
            sonarr_client: Sonarr API client
        """
        self.config = config
        self.sonarr_client = sonarr_client
        self.app_type = getattr(sonarr_client, "app_type", "sonarr")
        self.force_import_threshold = config.get("decisions.force_import_threshold", 10)
        self.private_trackers = config.get("trackers.private", [])
        self.public_trackers = config.get("trackers.public", [])

    def analyze_custom_formats(
        self, custom_formats_list: List[Dict], series_id: int
    ) -> FormatAnalysis:
        """
        Analyze custom formats and calculate total score.

        Args:
            custom_formats_list: List of custom format objects
            series_id: Sonarr series ID for score lookup

        Returns:
            FormatAnalysis with total score and format names
        """
        if not custom_formats_list:
            return FormatAnalysis(0, [])

        # Use cached version for production, regular method for tests
        from unittest.mock import MagicMock

        if hasattr(self.sonarr_client, "cache") and not isinstance(
            self.sonarr_client, MagicMock
        ):
            format_scores = self.sonarr_client.get_custom_format_scores_cached(
                series_id
            )
        else:
            format_scores = self.sonarr_client.get_custom_format_scores(series_id)
        total_score = 0
        format_names = []

        for cf in custom_formats_list:
            cf_id = cf.get("id")
            cf_name = cf.get("name", "Unknown")
            format_names.append(cf_name)

            if cf_id in format_scores:
                score = format_scores[cf_id]
                total_score += score
                logger.debug(f"Format '{cf_name}' (ID: {cf_id}): {score} points")

        return FormatAnalysis(total_score, format_names)

    def find_grab_info(
        self,
        history: List[Dict],
        download_id: Optional[str] = None,
        series_id: Optional[int] = None,
    ) -> Tuple[Optional[int], List[str]]:
        """
        Find detailed grab information from history.

        Args:
            history: Episode history entries
            download_id: Specific download ID to match (optional)
            series_id: Series ID for score calculation (optional)

        Returns:
            Tuple of (score, format_names)
        """
        for event in history:
            if event.get("eventType") == "grabbed":
                # Match specific download_id if provided
                if download_id and not self._download_ids_match(
                    event.get("downloadId"), download_id
                ):
                    continue

                # Get score and formats from history
                score = event.get("customFormatScore", 0)
                formats = event.get("customFormats", [])

                if series_id and not score and formats:
                    # Calculate score if not in history
                    analysis = self.analyze_custom_formats(formats, series_id)
                    score = analysis.total_score

                format_names = [cf.get("name", "Unknown") for cf in formats]
                return score, format_names

        return None, []

    @staticmethod
    def _download_ids_match(left: Optional[str], right: Optional[str]) -> bool:
        """Compare Arr download IDs without relying on hash casing."""
        if left is None or right is None:
            return left == right
        return str(left).casefold() == str(right).casefold()

    def _matching_grab_events(
        self, history: List[Dict], download_id: Optional[str]
    ) -> List[Dict]:
        """Return grabbed history rows for a specific download."""
        return [
            event
            for event in history
            if event.get("eventType") == "grabbed"
            and (
                not download_id
                or self._download_ids_match(event.get("downloadId"), download_id)
            )
        ]

    def _history_owner_id(self, event: Dict) -> Optional[int]:
        """Extract the owning series/movie ID from a history event."""
        key = "movieId" if self.app_type == "radarr" else "seriesId"
        return event.get(key)

    def _candidate_matches_target(
        self, candidate: Dict, profile_id: int, content_id: int
    ) -> bool:
        """Validate a manual-import candidate against the queued target."""
        if self.app_type == "radarr":
            return (candidate.get("movie") or {}).get("id") == profile_id

        if (candidate.get("series") or {}).get("id") != profile_id:
            return False
        return any(
            episode.get("id") == content_id
            for episode in (candidate.get("episodes") or [])
        )

    def _target_description(
        self, queue_item: Dict, profile_id: Optional[int], content_id: Optional[int]
    ) -> str:
        """Build stable target identifiers for action and blocker logs."""
        queue_id = queue_item.get("id")
        download_id = queue_item.get("downloadId")
        if self.app_type == "radarr":
            target = f"movieId={profile_id}"
        else:
            target = f"seriesId={profile_id} episodeId={content_id}"
        return f"queueId={queue_id} downloadId={download_id} {target}"

    def _exception_decision(
        self,
        queue_item: Dict,
        profile_id: Optional[int],
        content_id: Optional[int],
        blocker: str,
        current_score: Optional[int],
        current_formats: List[str],
    ) -> Decision:
        """Leave contradictory or incomplete evidence queued and log why."""
        reasoning = (
            f"{self._target_description(queue_item, profile_id, content_id)}: "
            f"{blocker}; leaving item queued"
        )
        logger.warning(f"Stuck import unresolved: {reasoning}")
        return Decision(
            action="monitor",
            grab_score=None,
            current_score=current_score,
            score_difference=None,
            reasoning=reasoning,
            grab_formats=[],
            current_formats=current_formats,
            missing_formats=[],
            extra_formats=[],
            is_private_tracker=False,
        )

    def _analyze_cross_content_mismatch(
        self,
        queue_item: Dict,
        profile_id: int,
        content_id: int,
        wrong_owner_id: int,
        global_history: List[Dict],
        current_score: int,
        current_formats: List[str],
    ) -> Decision:
        """Validate and score a candidate belonging to the queued target."""
        download_id = queue_item.get("downloadId")
        output_path = queue_item.get("outputPath")
        try:
            candidates = self.sonarr_client.get_manual_import_candidates(
                download_id, output_path, profile_id
            )
        except Exception as e:
            return self._exception_decision(
                queue_item,
                profile_id,
                content_id,
                f"manual-import candidate lookup failed: {e}",
                current_score,
                current_formats,
            )

        matching = [
            candidate
            for candidate in candidates
            if self._candidate_matches_target(candidate, profile_id, content_id)
        ]
        if len(matching) != 1:
            return self._exception_decision(
                queue_item,
                profile_id,
                content_id,
                f"found {len(matching)} matching manual-import candidates, expected exactly 1",
                current_score,
                current_formats,
            )

        candidate = matching[0]
        candidate_score = candidate.get("customFormatScore")
        scores = (candidate_score, current_score)
        if any(
            not isinstance(score, int) or isinstance(score, bool) for score in scores
        ):
            return self._exception_decision(
                queue_item,
                profile_id,
                content_id,
                f"nonnumeric score evidence candidate={candidate_score!r} current={current_score!r}",
                current_score,
                current_formats,
            )

        candidate_formats = [
            custom_format.get("name", "Unknown")
            for custom_format in candidate.get("customFormats", [])
        ]
        score_difference = candidate_score - current_score
        action = (
            "force_import"
            if score_difference >= self.force_import_threshold
            else "keep"
        )
        owner_label = "movieId" if self.app_type == "radarr" else "seriesId"
        mismatch_label = "cross-movie" if self.app_type == "radarr" else "cross-series"
        outcome = (
            "force import validated candidate"
            if action == "force_import"
            else "queue-only cleanup; download remains in client"
        )
        reasoning = (
            f"{mismatch_label} mismatch: grabbed {owner_label}={wrong_owner_id} "
            f"differs from queue {owner_label}={profile_id}; candidate score="
            f"{candidate_score}, current score={current_score}, diff={score_difference}; "
            f"{outcome}"
        )
        logger.info(
            f"Stuck import decision: "
            f"{self._target_description(queue_item, profile_id, content_id)} "
            f"grabScore={candidate_score} currentScore={current_score} "
            f"action={action} reason={reasoning}"
        )

        candidate_formats_set = set(candidate_formats)
        current_formats_set = set(current_formats)
        indexer = self._find_indexer_from_history(global_history, download_id)
        return Decision(
            action=action,
            grab_score=candidate_score,
            current_score=current_score,
            score_difference=score_difference,
            reasoning=reasoning,
            grab_formats=candidate_formats,
            current_formats=current_formats,
            missing_formats=list(candidate_formats_set - current_formats_set),
            extra_formats=list(current_formats_set - candidate_formats_set),
            is_private_tracker=self.is_private_tracker(indexer),
            validated_import_candidate=(
                candidate if action == "force_import" else None
            ),
        )

    def _resolve_history_fallback(
        self,
        queue_item: Dict,
        history: List[Dict],
        profile_id: Optional[int],
        content_id: Optional[int],
        current_score: int,
        current_formats: List[str],
    ) -> Tuple[List[Dict], Optional[Decision]]:
        """Resolve absent target-scoped history through global download history."""
        download_id = queue_item.get("downloadId")
        if self._matching_grab_events(history, download_id) or not download_id:
            return history, None

        try:
            global_history = self.sonarr_client.get_history_for_download(download_id)
        except Exception as e:
            return history, self._exception_decision(
                queue_item,
                profile_id,
                content_id,
                f"download history lookup failed: {e}",
                current_score,
                current_formats,
            )
        matching_grabs = self._matching_grab_events(global_history, download_id)
        if not matching_grabs:
            return history, self._exception_decision(
                queue_item,
                profile_id,
                content_id,
                "No grabbed history returned for download ID",
                current_score,
                current_formats,
            )

        owners = {
            owner_id
            for owner_id in map(self._history_owner_id, matching_grabs)
            if owner_id is not None
        }
        if owners == {profile_id}:
            return global_history, None

        if len(owners) == 1 and profile_id is not None and content_id is not None:
            if len(matching_grabs) != 1:
                return history, self._exception_decision(
                    queue_item,
                    profile_id,
                    content_id,
                    f"multiple grabbed history rows ({len(matching_grabs)}), possible season pack",
                    current_score,
                    current_formats,
                )
            wrong_owner_id = next(iter(owners))
            return history, self._analyze_cross_content_mismatch(
                queue_item,
                profile_id,
                content_id,
                wrong_owner_id,
                global_history,
                current_score,
                current_formats,
            )

        return history, self._exception_decision(
            queue_item,
            profile_id,
            content_id,
            f"conflicting or missing owner IDs in grabbed history: {sorted(owners)}",
            current_score,
            current_formats,
        )

    def get_current_file_details(
        self, content_id: int, profile_id: Optional[int] = None
    ) -> Tuple[Optional[int], List[str]]:
        """Get current file score and formats while preserving the legacy return type."""
        score, formats, _ = self._get_current_file_evidence(content_id, profile_id)
        return score, formats

    def _get_current_file_evidence(
        self, content_id: int, profile_id: Optional[int] = None
    ) -> Tuple[Optional[int], List[str], Optional[str]]:
        """
        Get current file details plus an explicit API/evidence error.

        Args:
            content_id: Sonarr episode ID or Radarr movie ID
            profile_id: Series/movie ID for score calculation (optional)

        Returns:
            Tuple of (score, format_names, error). A missing file is not an error.
        """
        try:
            if self.app_type == "radarr":
                response = self.sonarr_client._make_request(
                    "GET", f"/movie/{content_id}"
                )
                content = response.json()
                file_id_key = "movieFileId"
            else:
                response = self.sonarr_client._make_request(
                    "GET", f"/episode/{content_id}"
                )
                content = response.json()
                file_id_key = "episodeFileId"

            if not content.get("hasFile"):
                return None, [], None

            file_id = content.get(file_id_key)
            if not file_id:
                return None, [], "content reports an existing file without a file ID"

            # Get file details
            file_data = self.sonarr_client.get_episode_file(file_id)
            if not file_data:
                return None, [], f"file details unavailable for file ID {file_id}"

            score = file_data.get("customFormatScore", 0)
            formats = file_data.get("customFormats", [])

            if profile_id and not score and formats:
                analysis = self.analyze_custom_formats(formats, profile_id)
                score = analysis.total_score

            format_names = [cf.get("name", "Unknown") for cf in formats]
            return score, format_names, None

        except Exception as e:
            label = "movie" if self.app_type == "radarr" else "episode"
            logger.error(
                f"Failed to get current file details for {label} {content_id}: {e}"
            )
            return None, [], str(e)

    def is_private_tracker(self, indexer: str) -> bool:
        """
        Check if indexer is a private tracker.

        Args:
            indexer: Indexer name

        Returns:
            True if private tracker, False otherwise
        """
        if not indexer:
            return False

        indexer_lower = indexer.lower()
        for tracker in self.private_trackers:
            if tracker.lower() in indexer_lower:
                return True

        return False

    def is_public_tracker(self, indexer: str) -> bool:
        """
        Check if indexer is a public tracker.

        Args:
            indexer: Indexer name

        Returns:
            True if public tracker, False otherwise
        """
        if not indexer:
            return False

        indexer_lower = indexer.lower()
        for tracker in self.public_trackers:
            if tracker.lower() in indexer_lower:
                return True

        return False

    def analyze_queue_item(self, queue_item: Dict) -> Decision:
        """
        Analyze a queue item and determine the appropriate action.

        Args:
            queue_item: Queue item from Sonarr

        Returns:
            Decision object with action and reasoning
        """
        if self.app_type == "radarr":
            movie = queue_item.get("movie", {})
            content_id = movie.get("id")
            profile_id = content_id  # movie ID used for profile lookup
        else:
            episode = queue_item.get("episode", {})
            content_id = episode.get("id")
            series = queue_item.get("series", {})
            profile_id = series.get("id") if series else None
        download_id = queue_item.get("downloadId")

        # Check for "matched by ID" case - force import when content is known but name mismatch
        matched_pattern = (
            "matched to movie by id"
            if self.app_type == "radarr"
            else "matched to series by id"
        )
        status_messages = queue_item.get("statusMessages", [])
        for msg in status_messages:
            message_text = str(msg.get("messages", [])).lower()
            if matched_pattern in message_text:
                label = "Movie" if self.app_type == "radarr" else "Series"
                logger.info("   🆔 Release matched by ID - forcing import")
                return Decision(
                    action="force_import",
                    reasoning=f"{label} identified by ID despite name mismatch",
                    grab_score=None,
                    current_score=None,
                    score_difference=None,
                    grab_formats=[],
                    current_formats=[],
                    missing_formats=[],
                    extra_formats=[],
                    is_private_tracker=False,
                )

        # Get history
        history = (
            self.sonarr_client.get_history_for_episode(content_id) if content_id else []
        )

        # Get current file information
        current_score, current_formats, current_file_error = (
            self._get_current_file_evidence(content_id, profile_id)
        )

        if current_file_error:
            return self._exception_decision(
                queue_item,
                profile_id,
                content_id,
                f"current file lookup failed: {current_file_error}",
                current_score,
                current_formats,
            )

        # Treat no current file as score 0 for decision making
        if current_score is None:
            current_score = 0

        history, fallback_decision = self._resolve_history_fallback(
            queue_item,
            history,
            profile_id,
            content_id,
            current_score,
            current_formats,
        )
        if fallback_decision is not None:
            return fallback_decision

        # Find grab information
        grab_score, grab_formats = self.find_grab_info(history, download_id, profile_id)

        # Determine indexer from history
        indexer = self._find_indexer_from_history(history, download_id)
        is_private = self.is_private_tracker(indexer)
        is_public = self.is_public_tracker(indexer)
        is_unknown = not is_private and not is_public

        # Calculate format differences
        grab_formats_set = set(grab_formats)
        current_formats_set = set(current_formats)
        missing_formats = list(grab_formats_set - current_formats_set)
        extra_formats = list(current_formats_set - grab_formats_set)

        # Log analysis details
        self._log_analysis_details(
            queue_item,
            grab_score,
            current_score,
            grab_formats,
            current_formats,
            indexer,
        )

        # Make decision
        decision = self._make_decision(
            grab_score=grab_score,
            current_score=current_score,
            is_private_tracker=is_private,
            is_public_tracker=is_public,
            is_unknown_tracker=is_unknown,
            grab_formats=grab_formats,
            current_formats=current_formats,
            missing_formats=missing_formats,
            extra_formats=extra_formats,
            indexer=indexer,
        )

        return Decision(
            action=decision["action"],
            grab_score=grab_score,
            current_score=current_score,
            score_difference=(
                grab_score - current_score
                if grab_score is not None and current_score is not None
                else None
            ),
            reasoning=decision["reasoning"],
            grab_formats=grab_formats,
            current_formats=current_formats,
            missing_formats=missing_formats,
            extra_formats=extra_formats,
            is_private_tracker=is_private,
        )

    def _find_indexer_from_history(
        self, history: List[Dict], download_id: Optional[str]
    ) -> str:
        """Find indexer name from history events."""
        for event in history:
            if event.get("eventType") == "grabbed":
                if not download_id or self._download_ids_match(
                    event.get("downloadId"), download_id
                ):
                    data = event.get("data", {})
                    return data.get("indexer", "")
        return ""

    def _log_analysis_details(
        self,
        queue_item: Dict,
        grab_score: Optional[int],
        current_score: Optional[int],
        grab_formats: List[str],
        current_formats: List[str],
        indexer: str,
    ):
        """Log detailed analysis information."""
        if self.app_type == "radarr":
            movie = queue_item.get("movie", {})
            title = movie.get("title", "Unknown")
            year = movie.get("year", "")
            label = f"{title} ({year})" if year else title
        else:
            episode = queue_item.get("episode", {})
            series = queue_item.get("series", {})
            label = (
                f"{series.get('title', 'Unknown')} "
                f"S{episode.get('seasonNumber', 0):02d}"
                f"E{episode.get('episodeNumber', 0):02d}"
            )

        logger.debug(
            f"Analyzing: {label} | grab={grab_score} current={current_score} | {indexer}"
        )
        logger.debug(f"   Title: {queue_item.get('title', 'N/A')}")

        if grab_formats:
            logger.debug(f"   Grab Formats: {', '.join(grab_formats)}")
        if current_formats:
            logger.debug(f"   Current Formats: {', '.join(current_formats)}")

        # Show format differences
        if grab_formats and current_formats:
            missing = set(grab_formats) - set(current_formats)
            extra = set(current_formats) - set(grab_formats)

            if missing:
                logger.debug(f"   Missing from current: {', '.join(missing)}")
            if extra:
                logger.debug(f"   Extra in current: {', '.join(extra)}")

    def _make_decision(
        self,
        grab_score: Optional[int],
        current_score: Optional[int],
        is_private_tracker: bool,
        is_public_tracker: bool,
        is_unknown_tracker: bool,
        grab_formats: List[str],
        current_formats: List[str],
        missing_formats: List[str],
        extra_formats: List[str],
        indexer: str,
    ) -> Dict[str, str]:
        """
        Make import decision based on analysis.

        Returns:
            Dictionary with 'action' and 'reasoning' keys
        """
        if grab_score is None or current_score is None:
            return {
                "action": "monitor",
                "reasoning": f"Unable to determine scores (grab: {grab_score}, current: {current_score})",
            }

        score_diff = grab_score - current_score

        if score_diff >= self.force_import_threshold:
            reasoning = f"Grab score ({grab_score}) is {score_diff} points higher than current file ({current_score})"
            if missing_formats:
                reasoning += (
                    f". Missing formats: {', '.join(list(missing_formats)[:3])}"
                )

            logger.info(f"   Force import: {reasoning}")
            return {"action": "force_import", "reasoning": reasoning}

        elif score_diff < -self.force_import_threshold:
            if is_private_tracker:
                reasoning = f"Private tracker protection - keeping despite lower score (diff: {score_diff})"
                logger.debug(f"   Keep: {reasoning}")
                return {"action": "keep", "reasoning": reasoning}
            elif is_public_tracker:
                reasoning = (
                    f"Public tracker with lower score "
                    f"(grab: {grab_score}, current: {current_score}, diff: {score_diff})"
                )
                logger.debug(f"   Remove: {reasoning}")
                return {"action": "remove", "reasoning": reasoning}
            else:  # is_unknown_tracker
                reasoning = f"Unknown tracker '{indexer}' - keeping for safety despite lower score (diff: {score_diff})"
                logger.warning(f"   ⚠️ Action: Keep (unknown tracker) - {reasoning}")
                logger.warning(
                    f"   💡 Consider adding '{indexer}' to either private or public tracker list in config"
                )
                return {"action": "keep", "reasoning": reasoning}

        else:
            reasoning = f"Score difference ({score_diff}) within tolerance threshold ({self.force_import_threshold})"
            logger.debug(f"   Wait: {reasoning}")
            return {"action": "wait", "reasoning": reasoning}

    def detect_repeated_grabs(self, episode_id: int) -> List[Dict]:
        """
        Detect multiple grabs for the same episode indicating import issues.

        Args:
            episode_id: Sonarr episode ID

        Returns:
            List of unimported grab events
        """
        try:
            history = self.sonarr_client.get_history_for_episode(episode_id, limit=50)

            grabs = [e for e in history if e["eventType"] == "grabbed"]
            imports = [e for e in history if e["eventType"] == "downloadFolderImported"]

            if len(grabs) <= len(imports) + 1:
                return []  # Normal ratio

            # Find grabs without corresponding imports
            unimported_grabs = []

            for grab in grabs:
                download_id = grab.get("downloadId")

                # Check if this grab has a corresponding import
                has_import = any(
                    self._download_ids_match(imp.get("downloadId"), download_id)
                    for imp in imports
                )

                if not has_import:
                    unimported_grabs.append(grab)

            return unimported_grabs

        except Exception as e:
            logger.error(
                f"Error detecting repeated grabs for episode {episode_id}: {e}"
            )
            return []
