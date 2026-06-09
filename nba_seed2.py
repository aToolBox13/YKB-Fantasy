import os
import sys
import time
import math
import random
from datetime import datetime
import pytz
from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog
from postgrest import SyncPostgrestClient

# Database Connection Coordinates
SUPABASE_URL = "https://zvbkmmrxfmdyteypewjz.supabase.co/rest/v1/"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp2YmttbXJ4Zm1keXRleXBld2p6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk5ODk0OTksImV4cCI6MjA5NTU2NTQ5OX0.ihz33PtMmFlJrj-rup_fVmHuBbzQFOR9aYeER7ZFld0"

supabase = SyncPostgrestClient(SUPABASE_URL, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})

CURRENT_SEASON = "2025-26"
FORCE_MATCH_TEST = False  # Set to True locally to force full game recalculations

nba_headers = {
    'Host': 'stats.nba.com',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://www.nba.com',
    'Referer': 'https://www.nba.com/',
    'Connection': 'keep-alive',
}

def calculate_pure_box_price(pts, ast, reb, stl, blk, fg_pct, fg3m, min_pg):
    if min_pg < 5:
        return 1.00, 0.0, 1.0 # price, raw_metric, beta

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

    # Deriving investment risk metric 'beta' from minutes usage variance
    derived_beta = round(max(0.5, min(2.2, (raw_metric / 18.0))), 2)

    return max(1.00, round(calculated_price, 2)), raw_metric, derived_beta

def run_pipeline_cycle():
    pacific_tz = pytz.timezone('US/Pacific')
    pacific_now = datetime.now(pacific_tz)
    
    print(f"--- Gameday Processing Pipeline Initialized: {pacific_now.strftime('%Y-%m-%d %I:%M %p')} ---")
    
    active_players = players.get_active_players()
    total_players = len(active_players)
    today_str = pacific_now.strftime("%b %d, %Y")

    for idx, player in enumerate(active_players):
        player_id = player['id']
        full_name = player['full_name']
        
        # 1. READ TARGET RECORD (Targeting separated array history columns)
        try:
            db_query = supabase.table('players').select(
                'current_price', 'shares_outstanding',
                'history_day', 'history_week', 'history_month', 'history_year', 'history_all_time'
            ).eq('id', player_id).execute()
            
            if not db_query.data:
                continue
                
            player_row = db_query.data[0]
            shares = int(player_row.get('shares_outstanding') or 25000000)
            current_stored_price = float(player_row.get('current_price') or 10.00)
            
            # Safe array hydration
            h_day = player_row.get('history_day') or []
            h_week = player_row.get('history_week') or []
            h_month = player_row.get('history_month') or []
            h_year = player_row.get('history_year') or []
            h_all_time = player_row.get('history_all_time') or []
                
        except Exception as e:
            print(f"  Database sync read error for {full_name}: {e}")
            continue

        # 2. HARVEST RECENT GAME PERFORMANCE LOGS
        game_stats = None
        for attempt in range(2):
            try:
                log_fetch = playergamelog.PlayerGameLog(
                    player_id=player_id, season=CURRENT_SEASON, timeout=12, headers=nba_headers
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
                            "pts": latest_game[h_map['PTS']], "ast": latest_game[h_map['AST']],
                            "reb": latest_game[h_map['REB']], "stl": latest_game[h_map['STL']],
                            "blk": latest_game[h_map['BLK']], "fg3m": latest_game[h_map['FG3M']],
                            "team_abbreviation": latest_game[h_map['MATCHUP']].split(' ')[0],
                            "fg_pct": latest_game[h_map['FG_PCT']] if latest_game[h_map['FG_PCT']] else 0.0,
                            "min": float(latest_game[h_map['MIN']]) if latest_game[h_map['MIN']] else 0.0
                        }
                break
            except Exception:
                time.sleep(0.5)

        # 3. COMPUTE TRADING DATA METRICS
        team_logo_code = "NBA"
        if game_stats:
            new_price, raw_perf, beta = calculate_pure_box_price(
                game_stats["pts"], game_stats["ast"], game_stats["reb"],
                game_stats["stl"], game_stats["blk"], game_stats["fg_pct"],
                game_stats["fg3m"], game_stats["min"]
            )
            team_logo_code = game_stats["team_abbreviation"]
            
            # Analytical metric generation
            pe_ratio = round(max(8.5, min(99.9, (150.0 / max(1.0, raw_perf)))), 2)
            dividend_yield = round(max(0.00, min(8.50, (game_stats["ast"] * 0.4))), 2)
            print(f"[{idx+1}/{total_players}] {full_name}: GAME MATCH -> Volumetric Price: ${new_price}")
        else:
            # Market fluctuation drift logic
            market_drift = random.uniform(-1.00, 1.00)
            new_price = round(current_stored_price + market_drift, 2)
            if new_price < 1.00: new_price = 1.00
            
            beta = round(random.uniform(0.85, 1.45), 2)
            pe_ratio = round(random.uniform(14.0, 28.0), 2)
            dividend_yield = round(random.uniform(0.5, 3.2), 2)
            print(f"[{idx+1}/{total_players}] {full_name}: Market Variance -> Price: ${new_price} ({market_drift:+.2f})")

        # 4. MUTATE INDEPENDENT TIME-SERIES ARRAYS
        current_time_str = pacific_now.strftime("%I:%M %p")
        current_day_str = pacific_now.strftime("%a")
        
        h_day.append({"x": current_time_str, "y": new_price})
        if len(h_day) > 20: h_day.pop(0)

        if not h_week or h_week[-1].get("x") != current_day_str:
            h_week.append({"x": current_day_str, "y": new_price})
        else:
            h_week[-1]["y"] = new_price
        if len(h_week) > 7: h_week.pop(0)

        # Baseline padding fallback for broad-scale charts if empty
        if not h_month: h_month = [{"x": "W1", "y": new_price}]
        if not h_year: h_year = [{"x": "M1", "y": new_price}]
        if not h_all_time: h_all_time = [{"x": CURRENT_SEASON, "y": new_price}]

        # 5. EXECUTE DATABASE SYNC
        avatar_url = f"https://cdn.nba.com/headshots/nba/latest/1041x760/{player_id}.png"
        
        # Calculate trailing high/low bounds dynamically
        low_bound = new_price if not h_week else min([float(d['y']) for d in h_week] + [new_price])
        high_bound = new_price if not h_week else max([float(d['y']) for d in h_week] + [new_price])

        payload = {
            "current_price": new_price,
            "market_cap": round(new_price * shares, 2),
            "pe_ratio": pe_ratio,
            "dividend_yield": dividend_yield,
            "beta": beta,
            "high_52w": round(high_bound * 1.15, 2),
            "low_52w": round(low_bound * 0.85, 2),
            "avatar_url": avatar_url,
            "history_day": h_day,
            "history_week": h_week,
            "history_month": h_month,
            "history_year": h_year,
            "history_all_time": h_all_time
        }
        
        # Update team text field if it says 'NBA Asset pool' or 'NBA'
        if team_logo_code != "NBA":
            payload["team"] = team_logo_code

        try:
            supabase.table('players').update(payload).eq('id', player_id).execute()
        except Exception as e:
            print(f"  [⚠️ Sync Drop] Update refused for {full_name}: {e}")

        time.sleep(random.uniform(0.1, 0.4))

    print("--- Transaction Sync Block Concluded Successfully ---")

if __name__ == "__main__":
    run_pipeline_cycle()
