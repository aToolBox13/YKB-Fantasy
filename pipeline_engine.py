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

# --- SYSTEM ENVIRONMENT INJECTION ---
# This forces Python's core networking layer to route everything through the proxy automatically
proxy_string = "http://hvwewdoi:ibae046jb71v@38.154.185.97:6370"
os.environ["HTTP_PROXY"] = proxy_string
os.environ["HTTPS_PROXY"] = proxy_string

def calculate_pure_box_price(pts, ast, reb, stl, blk, fg_pct, fg3m, min_pg, gp):
    if gp < 3 or min_pg < 5:
        return 1.00
    volume_score = (pts * 1.0) + (ast * 1.35) + (reb * 0.75) + (stl * 2.0) + (blk * 2.0) + (fg3m * 0.7)
    efficiency_bonus = fg_pct * 6.0
    raw_metric = volume_score + efficiency_bonus
    scale_factor = raw_metric / 22.5
    minutes_modifier = 1.0 if min_pg >= 25.0 else math.sqrt(min_pg / 25.0)
    gp_modifier = 1.0 if gp >= 20 else (0.80 + (gp / 20.0) * 0.20)
    calculated_price = 85.00 * scale_factor * minutes_modifier * gp_modifier
    if min_pg < 13.0:
        calculated_price = min(calculated_price, 35.00)
    elif min_pg < 20 and raw_metric < 15.0:
        calculated_price = min(calculated_price, 65.00)
    return max(1.00, round(calculated_price, 2))

def run_pipeline_cycle():
    print(f"--- Pipeline Execution Started via Proxy Routing ---")
    active_players = players.get_active_players()
    total_players = len(active_players)
    
    for idx, player in enumerate(active_players):
        player_id = player['id']
        full_name = player['full_name']
        
        print(f"[{idx+1}/{total_players}] Syncing: {full_name}...", end="", flush=True)
        
        try:
            db_query = supabase.table('players').select('current_price', 'past_price_history', 'shares_outstanding').eq('id', player_id).execute()
            if not db_query.data:
                print(" Skipped.")
                continue
            player_row = db_query.data[0]
            shares = int(player_row.get('shares_outstanding', 25000000))
            existing_history = player_row.get('past_price_history') or {"day": [], "week": [], "month": [], "year": [], "all_time": []}
        except Exception as e:
            print(f" DB Error: {e}")
            continue

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Clean call: no internal proxy parameter needed because of lines 19-21
                dash = playerdashboardbyyearoveryear.PlayerDashboardByYearOverYear(player_id=player_id, timeout=15)
                yoy_dict = dash.by_year_player_dashboard.get_dict()
                
                all_time_array = []
                processed_seasons = set()
                has_played_this_season = False
                new_calculated_price = 1.00 
                
                if yoy_dict and yoy_dict['data']:
                    h_map = {header: i for i, header in enumerate(yoy_dict['headers'])}
                    for row in reversed(yoy_dict['data']):
                        season_label = row[h_map['GROUP_VALUE']]
                        if season_label in processed_seasons: continue
                        h_gp = row[h_map['GP']]
                        if h_gp and h_gp > 0:
                            season_price = calculate_pure_box_price(
                                row[h_map['PTS']]/h_gp, row[h_map['AST']]/h_gp, row[h_map['REB']]/h_gp,
                                row[h_map['STL']]/h_gp, row[h_map['BLK']]/h_gp, row[h_map['TRACKING_REB_PCT'] if 'TRACKING_REB_PCT' in h_map else h_map['FG_PCT']],
                                row[h_map['FG3M']]/h_gp, row[h_map['MIN']]/h_gp, h_gp
                            )
                            all_time_array.append({"x": season_label, "y": season_price})
                            processed_seasons.add(season_label)
                            if season_label == TARGET_SEASON:
                                new_calculated_price = season_price
                                has_played_this_season = True

                if not has_played_this_season:
                    day_array = [{"x": t, "y": new_calculated_price} for t in ["9:30 AM", "11:30 AM", "1:30 PM", "3:30 PM", "4:00 PM"]]
                    week_array = [{"x": d, "y": new_calculated_price} for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]
                    month_array = [{"x": w, "y": new_calculated_price} for w in ["Week 1", "Week 2", "Week 3", "Week 4"]]
                    year_array = [{"x": m, "y": new_calculated_price} for m in ["Oct", "Dec", "Feb", "Apr", "Jun"]]
                    if not all_time_array: all_time_array = [{"x": TARGET_SEASON, "y": new_calculated_price}]
                else:
                    day_array = existing_history.get("day", [])
                    day_array.append({"x": datetime.now().strftime("%I:%M %p"), "y": new_calculated_price})
                    if len(day_array) > 15: day_array.pop(0)
                    week_array = existing_history.get("week", [])
                    if not week_array or week_array[-1].get("x") != datetime.now().strftime("%a"):
                        week_array.append({"x": datetime.now().strftime("%a"), "y": new_calculated_price})
                    if len(week_array) > 7: week_array.pop(0)
                    month_array = existing_history.get("month") or [{"x": w, "y": new_calculated_price} for w in ["Week 1", "Week 2", "Week 3", "Week 4"]]
                    year_array = existing_history.get("year") or [{"x": m, "y": new_calculated_price} for m in ["Oct", "Dec", "Feb", "Apr", "Jun"]]

                supabase.table('players').update({
                    "current_price": new_calculated_price,
                    "market_cap": round(new_calculated_price * shares, 2),
                    "past_price_history": {"day": day_array, "week": week_array, "month": month_array, "year": year_array, "all_time": all_time_array}
                }).eq('id', player_id).execute()
                
                print(f" Success! (${new_calculated_price})")
                time.sleep(random.uniform(1.5, 3.0)) 
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f" Skipped (API Block).")
                else:
                    time.sleep(3)

if __name__ == "__main__":
    run_pipeline_cycle()
