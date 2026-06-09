import os
import sys
import time
import math
import random
from datetime import datetime
import pytz  # Handles server-to-local timezone calibration
from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog
from postgrest import SyncPostgrestClient

# Database Credentials
SUPABASE_URL = "https://zvbkmmrxfmdyteypewjz.supabase.co/rest/v1/"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp2YmttbXJ4Zm1keXRleXBld2p6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk5ODk0OTksImV4cCI6MjA5NTU2NTQ5OX0.ihz33PtMmFlJrj-rup_fVmHuBbzQFOR9aYeER7ZFld0"

supabase = SyncPostgrestClient(SUPABASE_URL, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})

CURRENT_SEASON = "2025-26"

# 🛠️ TESTING OVERRIDE: Set to True to force price updates using the latest historical game row
FORCE_MATCH_TEST = True 

# --- PROXY POOL FOR ENVIRONMENT INJECTION ---
PROXY_POOL = [
    "http://hvwewdoi:ibae046jb71v@38.154.203.95:5863",
    "http://hvwewdoi:ibae046jb71v@198.105.121.200:6462",
    "http://hvwewdoi:ibae046jb71v@64.137.96.74:6641",
    "http://hvwewdoi:ibae046jb71v@209.127.138.10:5784",
    "http://hvwewdoi:ibae046jb71v@38.154.185.97:6370"
]
selected_proxy = random.choice(PROXY_POOL)
os.environ["HTTP_PROXY"] = selected_proxy
os.environ["HTTPS_PROXY"] = selected_proxy

def calculate_pure_box_price(pts, ast, reb, stl, blk, fg_pct, fg3m, min_pg, gp=1):
    """Evaluates raw performance stats from a game log to determine true asset price."""
    if min_pg < 5:
        return 1.00

    volume_score = (pts * 1.0) + (ast * 1.35) + (reb * 0.75) + (stl * 2.0) + (blk * 2.0) + (fg3m * 0.7)
    efficiency_bonus = fg_pct * 6.0
    raw_metric = volume_score + efficiency_bonus
    
    scale_factor = raw_metric / 22.5
    minutes_modifier = 1.0 if min_pg >= 25.0 else math.sqrt(min_pg / 25.0)
    
    calculated_price = 85.00 * scale_factor * minutes_modifier

    if min_pg < 13.0:
        calculated_price = min(calculated_price, 35.00)
    elif min_pg < 20 and raw_metric < 15.0:
        calculated_price = min(calculated_price, 65.00)

    return max(1.00, round(calculated_price, 2))

def run_pipeline_cycle():
    pacific_tz = pytz.timezone('US/Pacific')
    pacific_now = datetime.now(pacific_tz)
    
    print(f"--- Gameday Processing Pipeline Initialized (Pacific Time): {pacific_now.strftime('%Y-%m-%d %I:%M %p')} ---")
    if FORCE_MATCH_TEST:
        print("[⚠️ WARNING] FORCE_MATCH_TEST is enabled. Roster pricing calculations will run on historical baselines.")
        
    print("[DEBUG] Attempting to fetch active players from nba_api...")
    active_players = players.get_active_players()
    total_players = len(active_players)
    print(f"[DEBUG] Total active players found in nba_api: {total_players}")
    
    today_str = pacific_now.strftime("%b %d, %Y")
    print(f"[DEBUG] Target Pacific Sync Date String: '{today_str}'")

    if total_players == 0:
        print("[🚨 CRITICAL] The player roster array is empty! The API returned zero active players.")
        return

    for idx, player in enumerate(active_players):
        player_id = player['id']
        full_name = player['full_name']
        
        # 1. READ DATABASE ENTRY FROM SUPABASE
        try:
            db_query = supabase.table('players').select('current_price', 'past_price_history', 'shares_outstanding').eq('id', player_id).execute()
            if not db_query.data:
                if idx % 50 == 0:
                    print(f" [DEBUG] Player {full_name} ({player_id}) not found in your Supabase table. Skipping.")
                continue
                
            player_row = db_query.data[0]
            shares = int(player_row.get('shares_outstanding', 25000000))
            current_stored_price = float(player_row.get('current_price', 1.00))
            existing_history = player_row.get('past_price_history') or {
                "day": [], "week": [], "month": [], "year": [], "all_time": []
            }
        except Exception as e:
            print(f" Database Access Fault for {full_name}: {e}")
            continue

        # 2. HARVEST LIVE LOGS WITH RETRY PROTECTION
        game_stats = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                log_fetch = playergamelog.PlayerGameLog(
                    player_id=player_id, 
                    season=CURRENT_SEASON, 
                    season_type_all_star='Playoffs', 
                    timeout=12
                )
                log_dict = log_fetch.get_dict()
                data_rows = log_dict['resultSets'][0]['rowSet']
                
                if not data_rows:
                    log_fetch = playergamelog.PlayerGameLog(
                        player_id=player_id, 
                        season=CURRENT_SEASON, 
                        season_type_all_star='Regular Season', 
                        timeout=12
                    )
                    log_dict = log_fetch.get_dict()
                    data_rows = log_dict['resultSets'][0]['rowSet']

                if data_rows:
                    headers_list = log_dict['resultSets'][0]['headers']
                    h_map = {header: i for i, header in enumerate(headers_list)}
                    
                    latest_game = data_rows[0]
                    game_date = latest_game[h_map['GAME_DATE']]
                    
                    if FORCE_MATCH_TEST or (game_date.strip().lower() == today_str.strip().lower()):
                        game_stats = {
                            "pts": latest_game[h_map['PTS']],
                            "ast": latest_game[h_map['AST']],
                            "reb": latest_game[h_map['REB']],
                            "stl": latest_game[h_map['STL']],
                            "blk": latest_game[h_map['BLK']],
                            "fg3m": latest_game[h_map['FG3M']],
                            "fg_pct": latest_game[h_map['FG_PCT']] if latest_game[h_map['FG_PCT']] else 0.0,
                            "min": float(latest_game[h_map['MIN']]) if latest_game[h_map['MIN']] else 0.0
                        }
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f" [API ERROR] Failed to harvest data logs for {full_name}: {e}")
                time.sleep(1)

        # 3. VALUATION MATRIX ASSESSMENT
        if game_stats:
            new_price = calculate_pure_box_price(
                game_stats["pts"], game_stats["ast"], game_stats["reb"],
                game_stats["stl"], game_stats["blk"], game_stats["fg_pct"],
                game_stats["fg3m"], game_stats["min"]
            )
            print(f"[{idx+1}/{total_players}] {full_name} processed! Price: ${current_stored_price} -> ${new_price}")
        else:
            new_price = current_stored_price
            if idx % 50 == 0:
                print(f"[{idx+1}/{total_players}] {full_name} marked idle. Price conserved.")

        # 4. STRUCTURAL TIMELINE ARRAY PROCESSING
        current_time_str = pacific_now.strftime("%I:%M %p")
        current_day_str = pacific_now.strftime("%a")
        
        day_array = existing_history.get("day") or []
        if not isinstance(day_array, list): day_array = []
        day_array.append({"x": current_time_str, "y": new_price})
        if len(day_array) > 15: day_array.pop(0)

        week_array = existing_history.get("week") or []
        if not isinstance(week_array, list): week_array = []
        if not week_array or week_array[-1].get("x") != current_day_str:
            week_array.append({"x": current_day_str, "y": new_price})
        else:
            week_array[-1]["y"] = new_price
        if len(week_array) > 7: week_array.pop(0)
        
        month_array = existing_history.get("month") or [{"x": "W1", "y": new_price}]
        year_array = existing_history.get("year") or [{"x": "M1", "y": new_price}]
        all_time_array = existing_history.get("all_time") or [{"x": CURRENT_SEASON, "y": new_price}]

        history_payload = {
            "day": day_array,
            "week": week_array,
            "month": month_array,
            "year": year_array,
            "all_time": all_time_array
        }

        # 5. DATABASE TRANSIT SYNC
        try:
            supabase.table('players').update({
                "current_price": new_price,
                "market_cap": round(new_price * shares, 2),
                "past_price_history": history_payload
            }).eq('id', player_id).execute()
        except Exception as e:
            print(f" Sync failure on payload execution for {full_name}: {e}")

        time.sleep(random.uniform(1.0, 2.2))

    print("--- Gameday Processing Pipeline Cycle Completed Successfully ---")

if __name__ == "__main__":
    run_pipeline_cycle()
