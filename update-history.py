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
    """Your exact pricing algorithm (Unchanged)"""
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
    elif min_pg < 20.0 and raw_metric < 15.0:
        calculated_price = min(calculated_price, 65.00)

    return max(1.00, round(calculated_price, 2))

def run_history_backfill():
    print("Starting Standalone Past Price History Generation Engine...")
    active_players = players.get_active_players()
    total_players = len(active_players)
    
    for idx, player in enumerate(active_players):
        player_id = player['id']
        full_name = player['full_name']
        
        print(f"[{idx+1}/{total_players}] Processing history arrays for: {full_name}...", end="", flush=True)
        
        # 1. READ: Verify base profile and pull the existing layout
        try:
            check = supabase.table('players').select('current_price', 'past_price_history').eq('id', player_id).execute()
            if not check.data:
                print(" Missing base profile in DB. Skipping.")
                continue
            
            current_price = float(check.data[0]['current_price'])
            existing_history = check.data[0].get('past_price_history') or {
                "day": [], "week": [], "month": [], "year": [], "all_time": []
            }
        except Exception as e:
            print(f" Supabase Fetch Error: {e}")
            continue

        max_retries = 3
        retry_delay = 2.0
        
        for attempt in range(max_retries):
            try:
                dash = playerdashboardbyyearoveryear.PlayerDashboardByYearOverYear(player_id=player_id, timeout=15)
                yoy_dict = dash.by_year_player_dashboard.get_dict()
                
                all_time_array = []
                processed_seasons = set() # Eliminates mid-season trade duplicate split rows
                has_played_this_season = False
                
                if yoy_dict and yoy_dict['data']:
                    headers = yoy_dict['headers']
                    h_map = {header: i for i, header in enumerate(headers)}
                    
                    # Map true career historical season nodes (Oldest -> Newest)
                    for row in reversed(yoy_dict['data']):
                        season_label = row[h_map['GROUP_VALUE']]
                        
                        # Guardrail: skip if we already generated a combined closing price entry for this season text
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
                            
                            season_calculated_price = calculate_pure_box_price(
                                h_pts, h_ast, h_reb, h_stl, h_blk, h_fg_pct, h_fg3m, h_min, h_gp
                            )
                            
                            all_time_array.append({"x": season_label, "y": season_calculated_price})
                            processed_seasons.add(season_label)
                            
                            if season_label == TARGET_SEASON:
                                has_played_this_season = True

                # 2. MODIFY: Dynamically update shorter time frames without blowing away prior entries
                if not has_played_this_season:
                    # Inactivity Rule: Forced structural horizontal lines if they aren't logging data
                    day_array = [{"x": t, "y": current_price} for t in ["9:30 AM", "11:30 AM", "1:30 PM", "3:30 PM", "4:00 PM"]]
                    week_array = [{"x": d, "y": current_price} for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]
                    month_array = [{"x": w, "y": current_price} for w in ["Week 1", "Week 2", "Week 3", "Week 4"]]
                    year_array = [{"x": m, "y": current_price} for m in ["Oct", "Dec", "Feb", "Apr", "Jun"]]
                    if not all_time_array:
                        all_time_array = [{"x": TARGET_SEASON, "y": current_price}]
                else:
                    # Active Rule: Pull current arrays, append live updates, and enforce queue capping bounds
                    current_time_str = time.strftime("%I:%M %p")
                    current_day_str = time.strftime("%a")
                    
                    # Update Day Ticker
                    day_array = existing_history.get("day", [])
                    if not isinstance(day_array, list): day_array = []
                    day_array.append({"x": current_time_str, "y": current_price})
                    if len(day_array) > 12: day_array.pop(0) # Keep a rolling window of recent intraday tracking intervals
                    
                    # Update Week Ticker
                    week_array = existing_history.get("week", [])
                    if not isinstance(week_array, list): week_array = []
                    # Avoid appending duplicate records on the exact same day if script runs multiple times
                    if not week_array or week_array[-1].get("x") != current_day_str:
                        week_array.append({"x": current_day_str, "y": current_price})
                    if len(week_array) > 7: week_array.pop(0)
                    
                    # Structural placeholders for Month & Year views during season backfill
                    month_array = existing_history.get("month") or [{"x": w, "y": current_price} for w in ["Week 1", "Week 2", "Week 3", "Week 4"]]
                    year_array = existing_history.get("year") or [{"x": m, "y": current_price} for m in ["Oct", "Dec", "Feb", "Apr", "Jun"]]

                # Bundle updated historical metrics mapping block
                history_payload = {
                    "day": day_array,
                    "week": week_array,
                    "month": month_array,
                    "year": year_array,
                    "all_time": all_time_array
                }
                
                # 3. WRITE: Update only the target jsonb category row
                supabase.table('players').update({
                    "past_price_history": history_payload
                }).eq('id', player_id).execute()
                
                print(f" Success! (All-Time Clean Nodes: {len(all_time_array)})")
                time.sleep(0.6)
                break
                
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f" Failed after max API retries: {e}")
                else:
                    print(" API timeout, retrying...", end="", flush=True)
                    time.sleep(retry_delay)
                    retry_delay *= 2

if __name__ == "__main__":
    run_history_backfill()
