import os
import sys
import time
import logging
from nba_api.live.nba.endpoints import scoreboard
from postgrest import SyncPostgrestClient

# 1. Set up professional logging for GitHub Actions
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 2. Securely fetch Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# FALLBACK: If environment variables are empty, add your strings here:
if not SUPABASE_URL or not SUPABASE_KEY:
    SUPABASE_URL = "https://your-project-id.supabase.co/rest/v1"  # Keep /rest/v1 at the end!
    SUPABASE_KEY = "your-anon-public-key"

if "your-project-id" in SUPABASE_URL:
    logger.error("CRITICAL: SUPABASE_URL or SUPABASE_KEY credentials are missing/unconfigured.")
    sys.exit(1)

# 3. Initialize direct database connection
try:
    supabase = SyncPostgrestClient(SUPABASE_URL, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
except Exception as e:
    logger.error(f"CRITICAL: Failed to initialize database client. Error: {e}")
    sys.exit(1)

# --- Helper Functions ---

def safe_int(value, default=0):
    """Safely cast API values to integers to prevent sudden crashes."""
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (ValueError, TypeError):
        return default

def fetch_live_scoreboard(retries=3, delay=5):
    """Fetches the live scoreboard with automatic retry logic if the network fails."""
    for attempt in range(retries):
        try:
            sb = scoreboard.ScoreBoard()
            return sb.games.get_dict()
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed to fetch NBA API: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    logger.error("All attempts to connect to the NBA API failed.")
    return None

# --- Main Engine ---

def pump_live_nba_stats():
    logger.info("Starting live NBA market pump...")
    
    games = fetch_live_scoreboard()
    
    if not games:
        logger.info("No active NBA games found right now.")
        return

    for game in games:
        game_id = game.get('gameId', 'Unknown')
        try:
            home_team = game.get('homeTeam', {}).get('teamTricode', 'Home')
            away_team = game.get('awayTeam', {}).get('teamTricode', 'Away')
            logger.info(f"Processing Game: {away_team} @ {home_team} (ID: {game_id})")
            
            home_players = game.get('homeTeam', {}).get('players', [])
            away_players = game.get('awayTeam', {}).get('players', [])
            all_players = home_players + away_players
            
            if not all_players:
                logger.warning(f"No player data found inside game {game_id}.")
                continue

            for player in all_players:
                player_name = "Unknown Player"
                try:
                    stats = player.get('statistics', {})
                    if not stats:
                        continue 
                        
                    minutes = safe_int(stats.get('minutesCalculated', 0))
                    
                    # Skip bench players to save database bandwidth
                    if minutes == 0:
                        continue

                    player_id = player.get('personId')
                    first_name = player.get('firstName', '')
                    last_name = player.get('familyName', '')
                    player_name = f"{first_name} {last_name}".strip()
                    
                    if not player_id:
                        continue

                    # Fire data directly via the custom Postgres RPC function call
                    supabase.rpc('update_player_stock_price', {
                        'p_player_id': safe_int(player_id),
                        'p_pts': safe_int(stats.get('points')),
                        'p_fgm': safe_int(stats.get('fieldGoalsMade')),
                        'p_fga': safe_int(stats.get('fieldGoalsAttempted')),
                        'p_fta': safe_int(stats.get('freeThrowsAttempted')),
                        'p_trb': safe_int(stats.get('reboundsTotal')),
                        'p_stl': safe_int(stats.get('steals')),
                        'p_ast': safe_int(stats.get('assists')),
                        'p_blk': safe_int(stats.get('blocks')),
                        'p_pf': safe_int(stats.get('foulsPersonal')),
                        'p_tov': safe_int(stats.get('turnovers')),
                        'p_mp': minutes
                    }).execute()
                    
                    logger.info(f"   -> Successfully updated market for: {player_name}")
                    
                except Exception as db_err:
                    logger.info(f"   Skipped {player_name}: Not seeded in DB or mismatch. ({db_err})")
                    
        except Exception as game_err:
            logger.error(f"Critical error processing game {game_id}: {game_err}")
            continue 

    logger.info("Market pump process fully complete.")

if __name__ == "__main__":
    pump_live_nba_stats()
