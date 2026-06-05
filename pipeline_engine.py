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

# --- PROXY CONFIGURATION (Authenticated with your Webshare credentials) ---
# Proxy 1: 209.127.138.10:5784
# (If you ever want to use Proxy 2, just change the IP to 38.154.185.97 and port to 6370)
PROXIES = {
    "http": "http://hvwewdoi:ibae046jb71v@209.127.138.10:5784",
    "https": "http://hvwewdoi:ibae046jb71v@209.127.138.10:5784"
}

# --- NBA API ANTI-BLOCKING SPOOFED HEADERS ---
headers = {
    'Host': 'stats.nba.com',
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
    'Upgrade-Insecure-Requests': '1',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nba.com/',
    'Origin': 'https://www.nba.com'
}

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
        retry_delay = 3.0
        for attempt in range(max_retries):
            try:
                # FIXED: Now routing through authenticated Webshare proxy to mask cloud IPs
                dash = playerdashboardbyyearoveryear.PlayerDashboardByYearOverYear(
                    player_id=player_id, 
                    timeout=25, 
                    headers=headers,
                    proxy=PROXIES
                )
                yoy_dict = dash.by_year_player_dashboard.get_dict()
                
                all_time_array = []
                processed_seasons = set()
                has_played_this_season = False
                new_calculated_price = 1.00 
                
                if yoy_dict and yoy_dict['data']:
                    headers_list = yoy_dict['headers']
                    h_map = {header: i for i, header in enumerate(headers_list)}
                    
                    for row in reversed(yoy_dict['data']):
                        season_label = row[h_map['GROUP_VALUE']]
                        
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
                            
                            if season_label == TARGET_SEASON:
                                new_calculated_price = season_price
                                has_played_this_season = True

                # 3. MODIFY ARRAYS: Apply conditional timeline mutations
                if not has_played_this_season:
                    day_array = [{"x": t, "y": new_calculated_price} for t in ["9:30 AM", "11:30 AM", "1:30 PM", "3:30 PM", "4:00 PM"]]
                    week_array = [{"x": d, "y": new_calculated_price} for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]
                    month_array = [{"x": w, "y": new_calculated_price} for w in ["Week 1", "Week 2", "Week 3", "Week 4"]]
                    year_array = [{"x": m, "y": new_calculated_price} for m in ["Oct", "Dec", "Feb", "Apr", "Jun"]]
                    if not all_time_array:
                        all_time_array = [{"x": TARGET_SEASON, "y": new_calculated_price}]
                else:
                    current_time_str = datetime.now().strftime("%I:%M %p")
                    current_day_str = datetime.now().strftime("%a")
                    
                    day_array = existing_history.get("day", [])
                    if not isinstance(day_array, list): day_array = []
                    day_array.append({"x": current_time_str, "y": new_calculated_price})
                    if len(day_array) > 15: day_array.pop(0)
                    
                    week_array = existing_history.get("week", [])
                    if not isinstance(week_array, list): week_array = []
                    if not week_array or week_array[-1].get("x") != current_day_str:
                        week_array.append({"x": current_day_str, "y": new_calculated_price})
                    if len(week_array) > 7: week_array.pop(0)
                    
                    month_array = existing_history.get("month") or [{"x": w, "y": new_calculated_price} for w in ["Week 1", "Week 2", "Week 3", "Week 4"]]
                    year_array = existing_history.get("year") or [{"x": m, "y": new_calculated_price} for m in ["Oct", "Dec", "Feb", "Apr", "Jun"]]

                history_payload = {
                    "day": day_array,
                    "week": week_array,
                    "month": month_array,
                    "year": year_array,
                    "all_time": all_time_array
                }
                
                market_cap = round(new_calculated_price * shares, 2)

                # 4. WRITE: Transmit structural updates to Supabase
                supabase.table('players').update({
                    "current_price": new_calculated_price,
                    "market_cap": market_cap,
                    "past_price_history": history_payload
                }).eq('id', player_id).execute()
                
                print(f" Success! (${new_calculated_price})")
                
                # A slightly longer human-like jitter delay so we don't burn the proxy IP fast
                time.sleep(random.uniform(2.0, 4.0)) 
                break
                
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f" Failed after max connectivity retries: {e}")
                else:
                    wait_time = retry_delay * (attempt + 1) * random.uniform(1.5, 2.5)
                    print(f" Proxy lag/Timeout. Cooldown for {round(wait_time, 1)}s...", end="", flush=True)
                    time.sleep(wait_time)

    print(f"--- Pipeline Execution Cycle Completed Successfully ---")

if __name__ == "__main__":
    run_pipeline_cycle()
