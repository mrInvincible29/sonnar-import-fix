#!/usr/bin/env python3
"""
Sonarr Import Monitor - Main entry point

A production-ready solution that automatically fixes Sonarr's import scoring issues
by monitoring the queue, comparing grab vs import scores, and taking appropriate actions.
"""

import sys
import argparse
import logging
from pathlib import Path

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.config.loader import ConfigLoader, ConfigurationError
from src.core.monitor import SonarrImportMonitor
from src.utils.logger import setup_logging


def create_argument_parser() -> argparse.ArgumentParser:
    """Create and configure argument parser."""
    
    parser = argparse.ArgumentParser(
        description='Sonarr Import Monitor - Fix stuck imports automatically',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run monitoring continuously with webhook
  python main.py --webhook

  # Run once and exit (useful for cron jobs)
  python main.py --once

  # Dry run to see what would happen without making changes
  python main.py --dry-run --once

  # Test specific episode analysis
  python main.py --test "SAKAMOTO DAYS" 1 19

  # Use custom config file and environment file
  python main.py --config config/production.yaml --env .env.production

  # Run with debug logging
  python main.py --verbose --webhook

Security:
  Configure your Sonarr webhook with authentication:
  - URL: http://your-server:8090/webhook/sonarr
  - Method: POST
  - Header: X-Webhook-Secret = <your-webhook-secret>

Environment Variables:
  SONARR_URL          - Sonarr server URL (required)
  SONARR_API_KEY      - Sonarr API key (required) 
  WEBHOOK_SECRET      - Webhook authentication secret
  WEBHOOK_PORT        - Webhook server port (default: 8090)
  FORCE_IMPORT_THRESHOLD - Score difference threshold (default: 10)
  LOG_LEVEL           - Logging level (DEBUG, INFO, WARNING, ERROR)
        """
    )

    # Configuration options
    parser.add_argument(
        '--config', '-c',
        help='Path to YAML configuration file (default: config.yaml)'
    )
    
    parser.add_argument(
        '--env',
        help='Path to environment file (default: .env)'
    )

    # Execution modes
    mode_group = parser.add_mutually_exclusive_group()
    
    mode_group.add_argument(
        '--once',
        action='store_true',
        help='Run once and exit (useful for cron jobs)'
    )
    
    mode_group.add_argument(
        '--test', '-t',
        nargs=3,
        metavar=('SERIES', 'SEASON', 'EPISODE'),
        help='Test mode: analyze specific episode (e.g., --test "SAKAMOTO DAYS" 1 19)'
    )

    # Runtime options
    parser.add_argument(
        '--webhook', '-w',
        action='store_true',
        help='Enable webhook server for real-time Sonarr events'
    )
    
    parser.add_argument(
        '--dry-run', '-d',
        action='store_true',
        help='Dry run - show what would happen without making changes'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging (DEBUG level)'
    )
    
    parser.add_argument(
        '--test-config',
        action='store_true',
        help='Test configuration and connectivity, then exit'
    )

    return parser


def validate_arguments(args) -> bool:
    """
    Validate command line arguments.
    
    Args:
        args: Parsed arguments
        
    Returns:
        True if valid, False otherwise
    """
    # Validate test arguments
    if args.test:
        series_title, season, episode = args.test
        
        try:
            season = int(season)
            episode = int(episode)
            
            if season < 0 or episode < 0:
                print("Error: Season and episode numbers must be positive")
                return False
                
        except ValueError:
            print("Error: Season and episode must be valid integers")
            return False
    
    # Validate config file path
    if args.config and not Path(args.config).exists():
        print(f"Error: Configuration file not found: {args.config}")
        return False
    
    # Validate env file path
    if args.env and not Path(args.env).exists():
        print(f"Error: Environment file not found: {args.env}")
        return False
    
    return True


def main():
    """Main entry point."""
    
    # Parse command line arguments
    parser = create_argument_parser()
    args = parser.parse_args()
    
    # Validate arguments
    if not validate_arguments(args):
        sys.exit(1)
    
    try:
        # Load configuration
        print("🔧 Loading configuration...")
        config = ConfigLoader(config_path=args.config, env_file=args.env)
        
        # Setup logging
        log_level = 'DEBUG' if args.verbose else config.get('logging.level', 'INFO')
        log_format = config.get('logging.format', 'text')
        setup_logging(level=log_level, format_type=log_format)
        
        logger = logging.getLogger(__name__)
        
        # Log startup information
        logger.info("🚀 Starting Sonarr Import Monitor v2.0.0")
        logger.info(f"   Configuration loaded from: {args.config or 'default locations'}")
        
        # Show masked configuration for verification
        masked_config = config.get_masked_config_for_logging()
        logger.debug(f"Configuration: {masked_config}")
        
        # Initialize monitor
        monitor = SonarrImportMonitor(config)
        monitor.dry_run = args.dry_run
        
        if args.dry_run:
            logger.info("🔸 DRY RUN MODE - No changes will be made")
        
        # Test configuration if requested
        if args.test_config:
            logger.info("🧪 Testing configuration...")
            success = monitor.test_configuration()
            sys.exit(0 if success else 1)
        
        # Execute based on mode
        if args.test:
            # Test specific episode
            series_title, season, episode = args.test
            monitor.test_specific_episode(series_title, int(season), int(episode))
            
        elif args.once:
            # Run once and exit
            success = monitor.run_once()
            sys.exit(0 if success else 1)
            
        else:
            # Continuous monitoring
            success = monitor.run_continuous(enable_webhook=args.webhook)
            sys.exit(0 if success else 1)

    except ConfigurationError as e:
        print(f"❌ Configuration Error: {e}")
        print("\nPlease check your configuration file or environment variables.")
        print("Run with --help for more information.")
        sys.exit(1)
        
    except KeyboardInterrupt:
        print("\n👋 Interrupted by user")
        sys.exit(0)
        
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        
        # Show more details in verbose mode
        if args.verbose:
            import traceback
            traceback.print_exc()
        
        sys.exit(1)


if __name__ == "__main__":
    main()