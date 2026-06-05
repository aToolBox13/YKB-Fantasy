import os
import sys
import time
import math
from nba_api.stats.static import players
from nba_api.stats.endpoints import playerdashboardbyyearoveryear
from postgrest import SyncPostgrestClient

# Hardcoded Supabase credentials
SUPABASE_URL = "https://zvbkmmrxfmdyteypewjz.supabase.co/rest/v1/"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp2YmttbXJ4Zm1keXRleXBld2p6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk5ODk0OTksImV4cCI6MjA5NTU2NTQ5OX0.ihz33PtMmFlJrj-rup_fVmHuBbzQFOR9aYeER7ZFld0"

supabase = SyncPostgrestClient(SUPABASE_URL, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})

TARGET_SEASON = "2025-26"

def calculate_pure_box_price(pts, ast, reb, stl, blk, fg_pct, fg3m, min_pg, gp):
    """
    Open-Ended Uncapped Engine.
    Establishes a solid baseline starter value ($75-$100), then scales upwards
    linearly without a maximum roof.
    """
    # Injury / Non-rotation baseline
    if gp < 3 or min_pg < 5:
        return 1.00

    # 1. Primary Volumetric Core Production
    volume_score = (pts * 1.0) + (ast * 1.35) + (reb * 0.75) + (stl * 2.0) + (blk * 2.0) + (fg3m * 0.7)
    
    # Efficiency scaling factor to help dominant interior finishers (like Giannis)
    efficiency_bonus = fg_pct * 6.0
    raw_metric = volume_score + efficiency_bonus
    
    # 2. Linear Scaling Anchor Matrix
    # 22.5 represents a standard everyday starter (e.g., 14 PTS, 4 REB, 3 AST).
    # Anyone hitting exactly 22.5 scales out to an anchor factor of 1.0.
    anchor_starter_score = 22.5
    scale_factor = raw_metric / anchor_starter_score

    # 3. Forgiving Performance Volume Multipliers
    # Completely prevents small minute drops (like playing 27-29 MPG) from destroying value
    if min_pg >= 25.0:
        minutes_modifier = 1.0
    else:
        minutes_modifier = math.sqrt(min_pg / 25.0)
        
    # High-impact performers retain their price scaling even during injury-shortened years
    gp_modifier = 1.0 if gp >= 20 else (0.80 + (gp / 20.0) * 0.20)
    
    final_multiplier = scale_factor * minutes_modifier * gp_modifier

    # 4. Pure Market Value Generation
    # Standard starters sit right in your $75 - $110 sweet spot.
    # Superstars will generate multipliers of 2.5x to 3.5x, scaling cleanly to $250, $300, or more.
    market_anchor_price = 85.00
    calculated_price = market_anchor_price * final_multiplier

    # 5. Low-Usage Structural Guardrails
    if min_pg < 13.0:
        calculated_price = min(calculated_price, 35.00)
    elif min_pg < 20.0 and raw_metric < 15.0:
        calculated_price = min(calculated_price, 65.00)

    return max(1.00, round(calculated_price, 2))

def seed_dynamic_market():
    print(f"Initializing NBA database build context for Season: {TARGET_SEASON}")
    active_players = players.get_active_players()
    total_players = len(active_players)
    
    for idx, player in enumerate(active_players):
        player_id = player['id']  
        full_name = player['full_name']
        
        print(f"[{idx+1}/{total_players}] Processing: {full_name}...", end="", flush=True)
        
        try:
            check = supabase.table('players').select('id').eq('id', player_id).execute()
            if check.data:
                print(" Skipping (Already in DB)")
                continue
        except Exception as e:
            print(f" Database connection error: {e}")
            continue

        max_retries = 3
        retry_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                dash = playerdashboardbyyearoveryear.PlayerDashboardByYearOverYear(player_id=player_id, timeout=15)
                yoy_dict = dash.by_year_player_dashboard.get_dict()
                
                pts, ast, reb, stl, blk, fg_pct, fg3m, min_pg, gp_count = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0
                found_target_season = False
                
                if yoy_dict and yoy_dict['data']:
                    headers = yoy_dict['headers']
                    h_map = {header: i for i, header in enumerate(headers)}
                    
                    for row in yoy_dict['data']:
                        if row[h_map['GROUP_VALUE']] == TARGET_SEASON:
                            gp = row[h_map['GP']]
                            gp_count = gp
                            found_target_season = True
                            
                            if gp and gp > 0:
                                pts = row[h_map['PTS']] / gp
                                ast = row[h_map['AST']] / gp
                                reb = row[h_map['REB']] / gp
                                stl = row[h_map['STL']] / gp
                                blk = row[h_map['BLK']] / gp
                                fg3m = row[h_map['FG3M']] / gp
                                min_pg = row[h_map['MIN']] / gp
                                fg_pct = row[h_map['FG_PCT']] if row[h_map['FG_PCT']] else 0.0
                            break
                
                if not found_target_season:
                    starting_price = 1.00
                else:
                    starting_price = calculate_pure_box_price(pts, ast, reb, stl, blk, fg_pct, fg3m, min_pg, gp_count)
                
                player_data = {
                    "id": player_id,  
                    "name": full_name,
                    "team": "NBA",  
                    "current_price": starting_price,
                    "status": "Active",
                    "past_price_history": [starting_price]
                }
                
                supabase.table('players').insert(player_data).execute()
                print(f" Success! (Price: ${starting_price}) [GP: {gp_count}]")
                
                time.sleep(0.6)
                break 
                
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f" Failed after max attempts: {e}")
                else:
                    print(f" (Error. Retrying...)", end="", flush=True)
                    time.sleep(retry_delay)
                    retry_delay *= 2

if __name__ == "__main__":
    seed_dynamic_market()
