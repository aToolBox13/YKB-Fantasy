import os
import sys
import time
from nba_api.stats.static import players
from nba_api.stats.endpoints import playerprofilev2
from supabase import create_client, Client

# Fetch Supabase credentials
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: Please set your SUPABASE_URL and SUPABASE_KEY environment variables.")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def calculate_balanced_price(pts, ast, reb, stl, blk, fg_pct, fg3m):
    """
    Calculates stock price based on custom priority weights.
    Floor: Exactly $1.00.
    Ceiling: None (Superstars can naturally scale past $100 if stats merit it).
    """
    # 1. High Priority Core Metrics
    core_score = (pts * 1.0) + (ast * 1.5) + (reb * 0.8)
    
    # 2. Defensive Impact Metrics
    defense_score = (stl * 2.0) + (blk * 2.0)
    
    # 3. Efficiency & Shooting Metrics (Lower Priority)
    # fg_pct comes from API as a decimal (e.g., 0.485 for 48.5%)
    efficiency_score = (fg_pct * 15.0) + (fg3m * 0.75)
    
    total_score = core_score + defense_score + efficiency_score
    
    # Base price calculation starting at your $1.00 floor
    # Superstars with a ~40-45 point overall score will naturally land around $80-$100+
    calculated_price = 1.00 + (total_score * 2.0)
    
    # Enforce strict $1.00 floor, no ceiling
    return max(1.00, round(calculated_price, 2))

def seed_dynamic_market():
    print("Fetching active NBA player directory...")
    active_players = players.get_active_players()
    print(f"Found {len(active_players)} active players. Fetching stats and applying custom market formula...")
    
    for idx, player in enumerate(active_players):
        player_id = player['id']  # Official NBA Person ID
        full_name = player['full_name']
        
        try:
            # Check for existing records to prevent unique key constraint errors
            check = supabase.table('players').select('id').eq('id', player_id).execute()
            if check.data:
                print(f"[{idx+1}/{len(active_players)}] Skipping {full_name} (Already in DB)")
                continue

            # Fetch player regular season career profile
            profile = playerprofilev2.PlayerProfileV2(player_id=player_id)
            career_dict = profile.career_totals_regular_season.get_dict()
            
            # Absolute baseline defaults if player is a rookie with no career rows
            pts, ast, reb, stl, blk, fg_pct, fg3m = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
            
            if career_dict and career_dict['data']:
                headers = career_dict['headers']
                data_row = career_dict['data'][0]
                
                # Dynamic index mapping based on NBA API headers to avoid position mismatches
                h_map = {header: i for i, header in enumerate(headers)}
                gp = data_row[h_map['GP']]
                
                if gp and gp > 0:
                    pts = data_row[h_map['PTS']] / gp
                    ast = data_row[h_map['AST']] / gp
                    reb = data_row[h_map['REB']] / gp
                    stl = data_row[h_map['STL']] / gp
                    blk = data_row[h_map['BLK']] / gp
                    fg3m = data_row[h_map['FG3M']] / gp
                    # FG_PCT is already an average/ratio, no need to divide by games played
                    fg_pct = data_row[h_map['FG_PCT']] if data_row[h_map['FG_PCT']] else 0.0

            # Determine custom baseline price with no ceiling limit
            starting_price = calculate_balanced_price(pts, ast, reb, stl, blk, fg_pct, fg3m)
            
            player_data = {
                "id": player_id,  # Database ID explicitly set to matching NBA Person ID
                "name": full_name,
                "team": "NBA", 
                "current_price": starting_price,
                "status": "Active",
                "past_price_history": [starting_price]
            }
            
            supabase.table('players').insert(player_data).execute()
            print(f"[{idx+1}/{len(active_players)}] Seeded {full_name} (ID: {player_id}) -> Price: ${starting_price}")
            
            # Standard rate limit delay to keep the NBA API connection stable
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Error processing {full_name}: {e}")
            time.sleep(2)
            continue

if __name__ == "__main__":
    seed_dynamic_market()
