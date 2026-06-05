import os
import sys
import time
import math
import random
from datetime import datetime
from nba_api.stats.static import players
from nba_api.stats.endpoints import playerdashboardbyyearoveryear
from postgrest import SyncPostgrestClient

# Database Credentials
SUPABASE_URL = "https://zvbkmmrxfmdyteypewjz.supabase.co/rest/v1/"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp2YmttbXJ4Zm1keXRleXBld2p6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk5ODk0OTksImV4cCI6MjA5NTU2NTQ5OX0.ihz33PtMmFlJrj-rup_fVmHuBbzQFOR9aYeER7ZFld0"

supabase = SyncPostgrestClient(SUPABASE_URL, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})

TARGET_SEASON = "2025-26"

def calculate_pure_box_price(pts, ast, reb, stl, blk, fg_pct, fg3m, min_pg, gp):
    """Your core mathematical evaluation formula."""
    if gp < 3 or min_pg < 5:
        return 1.00

    volume_score = (pts * 1.0) + (ast * 1.35) + (reb * 0.75) + (stl * 2.0) + (blk * 2.0) + (fg3m * 0.7)
    efficiency_bonus = fg_pct * 6.0
    raw_metric = volume_score + efficiency_bonus
    
    anchor_starter_score = 22.5
    scale_factor = raw_metric / anchor_starter_score

    if min_pg >= 25.0:
        minutes_modifier = 1.0
    else:
        minutes_modifier = math.sqrt(min_pg / 25.0)
        
    gp_modifier = 1.0 if gp >= 20 else (0.80 + (gp / 20.0) * 0.20)
    final_multiplier = scale_factor * minutes_modifier * gp_modifier

    market_anchor_price = 85.00
    calculated_price = market_anchor_price * final_multiplier

    if min_pg < 13.0:
        calculated_price = min(calculated_price, 35.00)
    elif min_pg < 20 and raw_metric < 15.0:
        calculated_price = min(calculated_price, 65.00)

    return max(1.00, round(calculated_price, 2))

def run_pipeline_cycle():
    print(f"--- Pipeline Execution Cycle Started: {datetime.now()} ---")
    active_players = players.get_active_players()
    total_players = len(active_players)
    
    for idx, player in enumerate(active_players):
        player_id = player['id']
        full_name = player['full_name']
        
        print(f"[{idx+1}/{total_players}] Syncing asset metrics for: {full_name}...", end="", flush=True)
        
        # 1. READ: Pull existing database record state
        try:
            db_query = supabase.table('players').select('current_price', 'past_price_history', 'shares_outstanding').eq('id', player_id).execute()
            if not db_query.data:
                print(" Profile omitted (not in database roster). Skipping.")
                continue
                
            player_row = db_query.data[0]
            shares = int(player_row.get('shares_outstanding', 25000000))
            existing_history = player_row.get('past_price_history') or {
                "day": [], "week": [], "month": [], "year": [], "all_time": []
            }
        except Exception as e:
            print(f" Database read block error: {e}")
            continue

        # 2. FETCH & PROCESS: Pull raw performance nodes from NBA API
        max_retries = 3
        retry_delay = 1.5
        for attempt in range(max_retries):
            try:
                dash = playerdashboardbyyearoveryear.PlayerDashboardByYearOverYear(player_id=player_id, timeout=12)
                yoy_dict = dash.by_year_player_dashboard.get_dict()
                
                all_time_array = []
                processed_seasons = set()
                has_played_this_season = False
                new_calculated_price = 1.00 # Base Default Fallback
                
                if yoy_dict and yoy_dict['data']:
                    headers = yoy_dict['headers']
                    h_map = {header: i for i, header in enumerate(headers)}
                    
                    for row in reversed(yoy_dict['data']):
                        season_label = row[h_map['GROUP_VALUE']]
                        
                        # Deduplicate trades
                        if season_label in processed_seasons:
                            continue
                            
                        h_gp = row[h_map['GP']]
                        if h_gp and h_gp > 0:
                            h_pts = row[h_map['PTS']] / h_gp
                            h_ast = row[h_map['AST']] / h_gp
                            h_reb = row[h_map['REB']] / h_gp
                            h_stl = row[h_map['STL']] / h_gp
                            h_blk = row[h_map['BLK']] / h_gp
                            h_fg3m = row[h_map['FG3M']] / h_gp
                            h_min = row[h_map['MIN']] / h_gp
                            h_fg_pct = row[h_map['FG_PCT']] if row[h_map['FG_PCT']] else 0.0
                            
                            season_price = calculate_pure_box_price(
                                h_pts, h_ast, h_reb, h_stl, h_blk, h_fg_pct, h_fg3m, h_min, h_gp
                            )
                            
                            all_time_array.append({"x": season_label, "y": season_price})
                            processed_seasons.add(season_label)
                            
                            # If row represents the active processing window, extract as new global base price
                            if season_label == TARGET_SEASON:
                                new_calculated_price = season_price
                                has_played_this_season = True

                # 3. MODIFY ARRAYS: Apply conditional timeline mutations
                if not has_played_this_season:
                    # Flatline Rule: No active season logs -> enforce absolute flat graph points
                    day_array = [{"x": t, "y": new_calculated_price} for t in ["9:30 AM", "11:30 AM", "1:30 PM", "3:30 PM", "4:00 PM"]]
                    week_array = [{"x": d, "y": new_calculated_price} for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]
                    month_array = [{"x": w, "y": new_calculated_price} for w in ["Week 1", "Week 2", "Week 3", "Week 4"]]
                    year_array = [{"x": m, "y": new_calculated_price} for m in ["Oct", "Dec", "Feb", "Apr", "Jun"]]
                    if not all_time_array:
                        all_time_array = [{"x": TARGET_SEASON, "y": new_calculated_price}]
                else:
                    # Active Appending Rule: Grab prior lists, inject new point, slice queue limits
                    current_time_str = datetime.now().strftime("%I:%M %p")
                    current_day_str = datetime.now().strftime("%a")
                    
                    # Update Intraday Ticker List
                    day_array = existing_history.get("day", [])
                    if not isinstance(day_array, list): day_array = []
                    day_array.append({"x": current_time_str, "y": new_calculated_price})
                    if len(day_array) > 15: day_array.pop(0)
                    
                    # Update Weekly Ticker List (Deduplicated by day name to prevent overflow script stacking)
                    week_array = existing_history.get("week", [])
                    if not isinstance(week_array, list): week_array = []
                    if not week_array or week_array[-1].get("x") != current_day_str:
                        week_array.append({"x": current_day_str, "y": new_calculated_price})
                    if len(week_array) > 7: week_array.pop(0)
                    
                    # Carry forward month/year frameworks or initialize defaults
                    month_array = existing_history.get("month") or [{"x": w, "y": new_calculated_price} for w in ["Week 1", "Week 2", "Week 3", "Week 4"]]
                    year_array = existing_history.get("year") or [{"x": m, "y": new_calculated_price} for m in ["Oct", "Dec", "Feb", "Apr", "Jun"]]

                # Assemble Unified Structural Payload Container
                history_payload = {
                    "day": day_array,
                    "week": week_array,
                    "month": month_array,
                    "year": year_array,
                    "all_time": all_time_array
                }
                
                # Recompute related tracking variables
                market_cap = round(new_calculated_price * shares, 2)

                # 4. WRITE: Transmit structural updates in a single network round-trip payload transaction
                supabase.table('players').update({
                    "current_price": new_calculated_price,
                    "market_cap": market_cap,
                    "past_price_history": history_payload
                }).eq('id', player_id).execute()
                
                print(f" Success! (${new_calculated_price} | Career Nodes: {len(all_time_array)})")
                time.sleep(0.6) # Standard NBA API connection request throttling buffer
                break
                
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f" Failed after max connectivity retries: {e}")
                else:
                    print(" Connection timeout, retrying channel...", end="", flush=True)
                    time.sleep(retry_delay)
                    retry_delay *= 2

    print(f"--- Pipeline Execution Cycle Completed Successfully ---")

if __name__ == "__main__":
    run_pipeline_cycle()
