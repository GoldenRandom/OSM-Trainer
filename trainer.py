import os
import sys
import time
import random
import json
import logging
from playwright.sync_api import sync_playwright
import urllib.parse
import urllib.request

# ==========================================
# ADVISOR / SCRAPER LOGIC (Standalone)
# ==========================================
def scrape_squad(page):
    squad_data = {"data": []}
    def handle(res):
        if "api/v1/" in res.url and "players" in res.url and "transferplayers" not in res.url:
            try: squad_data["data"].append(res.json())
            except: pass
    page.on("response", handle)
    page.goto(f"{BASE_URL}/Squad", wait_until="domcontentloaded", timeout=60000)
    for _ in range(20):
        if squad_data["data"]: break
        page.wait_for_timeout(500)
    page.remove_listener("response", handle)
    players = []
    for chunk in squad_data["data"]:
        if isinstance(chunk, list): 
            players.extend(chunk)
        elif isinstance(chunk, dict):
            for key in ["players", "data", "result", "items"]:
                if key in chunk and isinstance(chunk[key], list):
                    players.extend(chunk[key])
                    break
    
    unique_players = {}
    for p in players:
        if isinstance(p, dict):
            pid = p.get("id") or p.get("name")
            if pid: unique_players[pid] = p
            
    return list(unique_players.values())

def scrape_market(page):
    market_data = {"data": []}
    def handle(res):
        if "api/v1/" in res.url and "transferplayers" in res.url:
            try: market_data["data"].append(res.json())
            except: pass
    page.on("response", handle)
    page.goto(f"{BASE_URL}/Transferlist", wait_until="domcontentloaded", timeout=60000)
    for _ in range(20):
        if market_data["data"]: break
        page.wait_for_timeout(500)
    page.remove_listener("response", handle)
    listings = []
    for chunk in market_data["data"]:
        if isinstance(chunk, list): 
            listings.extend(chunk)
        elif isinstance(chunk, dict):
            for key in ["players", "data", "result", "items"]:
                if key in chunk and isinstance(chunk[key], list):
                    listings.extend(chunk[key])
                    break
                    
    unique_listings = {}
    for p in listings:
        if isinstance(p, dict):
            pid = p.get("id") or p.get("name")
            if pid: unique_listings[pid] = p
            
    return list(unique_listings.values())

def get_osm_rating(p):
    pos = str(p.get("position", ""))
    att = p.get("statAtt", p.get("attack", 0))
    def_ = p.get("statDef", p.get("defense", 0))
    ovr = p.get("statOvr", p.get("overall", 0))
    if pos in ("1", "ATT", "ST", "LW", "RW"): return att if att > 0 else ovr
    if pos in ("3", "DEF", "CB", "LB", "RB", "4", "GK"): return def_ if def_ > 0 else ovr
    return round((att + def_) / 2) if att or def_ else ovr

def get_sell_candidates(squad):
    valid = sorted([get_osm_rating(p) for p in squad if get_osm_rating(p) > 0], reverse=True)
    top_11 = valid[:11]
    avg = round(sum(top_11)/max(len(top_11), 1), 1) if top_11 else 0
    sell = []
    by_pos = {"ATT": [], "MID": [], "DEF": [], "GK": []}
    for p in squad:
        sc = get_osm_rating(p)
        if sc <= 0: continue
        pos = str(p.get("position", ""))
        if pos in ("1", "ATT", "ST", "LW", "RW"): g = "ATT"
        elif pos in ("2", "MID", "CM", "CAM", "CDM"): g = "MID"
        elif pos in ("3", "DEF", "CB", "LB", "RB"): g = "DEF"
        elif pos in ("4", "GK"): g = "GK"
        else: g = "MID"
        by_pos[g].append((sc, p))
        
    for g, pos_players in by_pos.items():
        pos_players.sort(key=lambda x: x[0], reverse=True)
        
        # Safe limits to ensure we never sell our starting core
        keep_limit = 2 if g == "GK" else 5
        core_limit = 1 if g == "GK" else 3
        
        for i, (sc, p) in enumerate(pos_players):
            reason = ""
            # If we have a massive surplus, sell them
            if i >= keep_limit: 
                reason = f"Surplus ({g})"
            # If they aren't part of our vital core AND their rating is below 93% of our top 11 avg, sell them
            elif i >= core_limit and sc < avg * 0.93: 
                reason = f"Low rated ({sc} vs {avg})"
                
            if reason:
                pc = dict(p)
                pc["sell_reason"] = reason
                sell.append(pc)
    return sell

def recommend_transfers(squad, market):
    # 1. Group squad into broad positions and sort by rating
    by_pos = {"ATT": [], "MID": [], "DEF": [], "GK": []}
    for p in squad:
        sc = get_osm_rating(p)
        pos = str(p.get("position", ""))
        if pos in ("1", "ATT", "ST", "LW", "RW"): g = "ATT"
        elif pos in ("2", "MID", "CM", "CAM", "CDM"): g = "MID"
        elif pos in ("3", "DEF", "CB", "LB", "RB"): g = "DEF"
        elif pos in ("4", "GK"): g = "GK"
        else: g = "MID"
        
        spec = str(p.get("positionSpecific", g))
        if not spec or spec.isdigit(): spec = g
            
        by_pos[g].append({"rating": sc, "name": p.get("name"), "spec": spec, "group": g})
        
    for g in by_pos:
        by_pos[g].sort(key=lambda x: x["rating"], reverse=True)
        
    # 2. Extract starting 11 (4-3-3)
    starters = []
    def add_starters(g, count):
        for i in range(count):
            if i < len(by_pos[g]):
                starters.append(by_pos[g][i])
            else:
                starters.append({"rating": 0, "name": "Empty Slot", "spec": g, "group": g})
                
    add_starters("GK", 1)
    add_starters("DEF", 4)
    add_starters("MID", 3)
    add_starters("ATT", 3)
    
    # 3. Sort starters from weakest to strongest slot
    starters.sort(key=lambda x: x["rating"])
    
    # 4. Find the best market upgrade for the weakest slots
    buys = []
    market_used = set()
    
    for slot in starters:
        best_upgrade = None
        best_upgrade_listing = None
        
        for listing in market:
            p = listing.get("player", listing)
            pid = p.get("id") or p.get("name")
            if pid in market_used: continue
            
            sc = get_osm_rating(p)
            pos = str(p.get("position", ""))
            if pos in ("1", "ATT", "ST", "LW", "RW"): mg = "ATT"
            elif pos in ("2", "MID", "CM", "CAM", "CDM"): mg = "MID"
            elif pos in ("3", "DEF", "CB", "LB", "RB"): mg = "DEF"
            elif pos in ("4", "GK"): mg = "GK"
            else: mg = "MID"
                 
            if mg == slot["group"] and sc > slot["rating"]:
                if not best_upgrade or sc > best_upgrade["rating"]:
                    best_upgrade = {"rating": sc, "name": p.get("name"), "id": pid}
                    best_upgrade_listing = listing
                    
        if best_upgrade_listing:
            market_used.add(best_upgrade["id"])
            pc = dict(best_upgrade_listing.get("player", best_upgrade_listing))
            price = best_upgrade_listing.get("price", best_upgrade_listing.get("current_price", 0))
            if price > 0:
                pc["price_formatted"] = f"{round(price / 1000000, 1)}M"
            else:
                pc["price_formatted"] = "???"
            
            pc["upgrade_msg"] = f"Upgrade {slot['spec']} ({slot['name']} {slot['rating']}) ➡️ {pc.get('name')} ({best_upgrade['rating']})"
            buys.append(pc)
            
            if len(buys) >= 4:
                break
                
    profit_flips = []
    for listing in market:
        p = listing.get("player", listing)
        pid = p.get("id") or p.get("name")
        if pid in market_used: continue
        
        value = p.get("value", 0)
        price = listing.get("price", listing.get("current_price", 0))
        
        if value > 0 and price > 0:
            # Calculate max sell price based on standard OSM multipliers
            if value < 5000000: mult = 2.5
            elif value < 15000000: mult = 2.0
            elif value < 25000000: mult = 1.7
            elif value < 35000000: mult = 1.5
            else: mult = 1.3
            
            sell_for = value * mult
            profit = sell_for - price
            
            if profit >= 1000000: # Even 1M profit is good for cheap flips
                pc = dict(p)
                pc["buy_price"] = round(price / 1000000, 1)
                pc["sell_price"] = round(sell_for / 1000000, 1)
                pc["profit"] = round(profit / 1000000, 1)
                pc["roi"] = profit / price # Return on Investment!
                profit_flips.append(pc)
                    
    # Sort by highest ROI% (this naturally bubbles the cheapest, worst players to the top!)
    profit_flips.sort(key=lambda x: x["roi"], reverse=True)
                
    return {"buys": buys, "profit_flips": profit_flips[:2]}

def get_smart_training_picks(context, league_id, team_id, do_not_train=None):
    """Fetch squad from API and pick the best players to train for each coach.
    Priority: Starting XI (weakest bold first) > Bench > Young prospects.
    Excludes players in do_not_train list (sell candidates, etc.)."""
    if do_not_train is None:
        do_not_train = []
    try:
        res = context.request.get(
            f"https://web-api.onlinesoccermanager.com/api/v1/leagues/{league_id}/teams/{team_id}/players"
        )
        if not res.ok:
            log.warning("Could not fetch squad from API for smart training picks.")
            return {}
        players = res.json()
    except Exception as e:
        log.warning(f"Failed to fetch squad for training: {e}")
        return {}

    # Filter out players we don't want to train (sell candidates, etc.)
    players = [p for p in players if p.get("name", "").lower() not in do_not_train]

    def bold(p):
        return max(p.get("statAtt", 0), p.get("statDef", 0), p.get("statOvr", 0))

    def get_training_score(p):
        rating = bold(p)
        age = p.get("age", 99)
        
        # OSM Age Brackets
        if age <= 20: age_penalty = 0
        elif age <= 24: age_penalty = 10
        elif age <= 29: age_penalty = 30
        else: age_penalty = 100
        
        return rating + age_penalty

    def group_pos(p):
        pos = p.get("position", 0)
        if pos == 1: return "ATT"
        if pos == 2: return "MID"
        if pos == 3: return "DEF"
        if pos == 4: return "GK"
        return "MID"

    # Identify starting XI from the lineup field
    starting = [p for p in players if p.get("lineup", 0) > 0]
    starting_names = {p.get("name", "").lower() for p in starting}

    # Group ALL players by position, sorted best-first
    by_pos = {"ATT": [], "MID": [], "DEF": [], "GK": []}
    for p in players:
        by_pos[group_pos(p)].append(p)
    for g in by_pos:
        by_pos[g].sort(key=lambda p: bold(p), reverse=True)

    # Bench = next-best players after starters (not in starting XI)
    bench_counts = {"GK": 1, "DEF": 2, "MID": 2, "ATT": 2}
    bench_names = set()
    for g, count in bench_counts.items():
        found = 0
        for p in by_pos[g]:
            if p.get("name", "").lower() not in starting_names:
                bench_names.add(p.get("name", "").lower())
                found += 1
                if found >= count:
                    break

    lineup_names = starting_names | bench_names

    def pick_for_position(pos_group):
        """Pick players to train: starting XI weakest first, then bench weakest first."""
        candidates = by_pos.get(pos_group, [])
        in_starting = [p for p in candidates if p.get("name", "").lower() in starting_names]
        in_bench = [p for p in candidates if p.get("name", "").lower() in bench_names]
        # Sort by training score (factors in age and rating)
        in_starting.sort(key=lambda p: get_training_score(p))
        in_bench.sort(key=lambda p: get_training_score(p))
        return [p.get("name", "").lower() for p in in_starting + in_bench if p.get("name")]

    coach_picks = {
        "Attacking coach": pick_for_position("ATT"),
        "Midfielder coach": pick_for_position("MID"),
        "Defending coach": pick_for_position("DEF"),
        "Goalkeeping coach": pick_for_position("GK"),
    }

    # Universal coach: weakest player actually in the lineup (starting XI + bench)
    all_lineup = [p for p in players if p.get("lineup", 0) > 0]
    all_lineup.sort(key=lambda p: get_training_score(p))
    coach_picks["Universal coach"] = [p.get("name", "").lower() for p in all_lineup if p.get("name")]

    # Log what we picked
    log.info("Smart training picks calculated based on lineup and age.")

    return coach_picks

# ==========================================

def send_whatsapp_message(text):
    id_instance = os.getenv("GREEN_API_INSTANCE")
    api_token = os.getenv("GREEN_API_TOKEN")
    phone = os.getenv("WHATSAPP_PHONE")
    
    if not all([id_instance, api_token, phone]):
        return
        
    url = f"https://api.green-api.com/waInstance{id_instance}/sendMessage/{api_token}"
    
    clean_phone = phone.replace("+", "").replace(" ", "")
    chat_id = f"{clean_phone}@c.us"
    
    payload = json.dumps({
        "chatId": chat_id,
        "message": text
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    
    try:
        urllib.request.urlopen(req)
        log.info("Green API WhatsApp notification sent!")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        log.error(f"Failed to send Green API message: HTTP Error {e.code} - {error_body}")
    except Exception as e:
        log.error(f"Failed to send Green API message: {e}")

# Setup logging
logging.basicConfig(level=logging.CRITICAL, format="%(asctime)s │ %(levelname)-8s │ %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("training_ad_watcher")

BASE_URL = "https://en.onlinesoccermanager.com"
COOKIES_PATH = os.path.join(os.path.dirname(__file__), "cookies.json")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

def interactive_login(pw):
    """Opens a browser for the user to log in locally."""
    log.info("No cookies found. Opening browser for manual login...")
    try:
        browser = pw.chromium.launch(headless=False, channel="chrome")
    except Exception as e:
        log.error("Failed to launch visible browser. If you are on a cloud server, you must provide a valid cookies.json file!")
        raise e
        
    context = browser.new_context(viewport={"width": 1280, "height": 800}, user_agent=_USER_AGENT)
    page = context.new_page()
    page.goto(f"{BASE_URL}/Login")
    
    log.info("Please log in. Waiting for you to reach the Dashboard...")
    try:
        page.wait_for_url("**/Dashboard**", timeout=300000) # Wait up to 5 minutes
        cookies = context.cookies()
        with open(COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f)
        log.info("Login successful! Cookies saved to cookies.json.")
    except Exception as e:
        log.error("Login timed out or failed. Please try again.")
        raise e
    finally:
        browser.close()

def load_cookies():
    env_cookies = os.environ.get("OSM_COOKIES")
    if env_cookies:
        try:
            with open(COOKIES_PATH, "w", encoding="utf-8") as f:
                f.write(env_cookies)
            log.info("Loaded cookies from GitHub Secrets!")
        except Exception as e:
            log.error(f"Failed to load cookies from secrets: {e}")

def get_user_leagues(pw, cookies):
    """Fetch user league slots using API interception."""
    browser = pw.chromium.launch(headless=True, channel="chrome")
    context = browser.new_context(viewport={"width": 1280, "height": 800}, user_agent=_USER_AGENT)
    context.add_cookies(cookies)
    page = context.new_page()
    
    accounts_data = {"data": None}
    def handle_response(response):
        if "api/v1/" in response.url and "user/accounts" in response.url:
            try:
                if response.ok:
                    accounts_data["data"] = response.json()
            except: pass
            
    page.on("response", handle_response)
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    
    for _ in range(50):
        if accounts_data["data"]: break
        page.wait_for_timeout(100)
        
    leagues = []
    data = accounts_data["data"] or {}
    for slot_idx, slot_data in data.get("teamSlots", {}).items():
        if "team" in slot_data and "league" in slot_data:
            leagues.append({
                "slot_index": int(slot_idx),
                "league_id": slot_data["team"].get("leagueId", 0),
                "team_id": slot_data["team"].get("id", 0),
                "team_name": slot_data["team"].get("name", "Unknown"),
                "league_name": slot_data["league"].get("name", "Unknown"),
            })
            
    browser.close()
    return leagues

def switch_league_slot(pw, target_league):
    """Modifies the session cookie to switch slots."""
    with open(COOKIES_PATH, "r", encoding="utf-8") as f:
        cookies = json.load(f)
        
    for c in cookies:
        if c["name"] == "session":
            try:
                session_data = json.loads(urllib.parse.unquote(c["value"]))
                session_data["slotIndex"] = target_league["slot_index"]
                session_data["teamId"] = target_league["team_id"]
                session_data["leagueId"] = target_league["league_id"]
                c["value"] = urllib.parse.quote(json.dumps(session_data))
            except: pass
            
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 800}, user_agent=_USER_AGENT)
    context.add_cookies(cookies)
    page = context.new_page()
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    
    with open(COOKIES_PATH, "w", encoding="utf-8") as f:
        json.dump(context.cookies(), f)
    browser.close()

def training_loop():
    log.info("Starting Training Ad Watcher...")
    load_cookies()
    
    with sync_playwright() as pw:
        if not os.path.exists(COOKIES_PATH):
            interactive_login(pw)
            
        with open(COOKIES_PATH, "r", encoding="utf-8") as f:
            cookies = json.load(f)

        leagues = get_user_leagues(pw, cookies)
        target_slot = None
        target_league_data = None
        
        target_team_env = os.environ.get("TARGET_TEAM", "").strip().lower()
        target_league_env = os.environ.get("TARGET_LEAGUE", "").strip().lower()
        
        if target_team_env:
            log.info("Looking for Target Team in slots...")
            for l in leagues:
                team_match = target_team_env in l["team_name"].lower()
                league_match = not target_league_env or target_league_env in l["league_name"].lower()
                
                if team_match and league_match:
                    target_slot = l["slot_index"]
                    target_league_data = l
                    break

            if target_slot is None:
                log.warning("Could not find exact match for target team. Looking for any partial match...")
                for l in leagues:
                    if target_team_env in l["team_name"].lower():
                        target_slot = l["slot_index"]
                        target_league_data = l
                        break
                        
        if target_slot is not None:
            log.info(f"Switching to slot {target_slot}...")
            switch_league_slot(pw, target_league_data)
        elif target_team_env:
            log.error("Could not find target team in any slot. Continuing on current slot...")
        else:
            log.info("No TARGET_TEAM specified. Continuing on current active slot...")

        # Ensure target_league_data is always set (for API calls)
        if target_league_data is None and leagues:
            target_league_data = leagues[0]

        log.info("Launching browser...")
        browser = pw.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--mute-audio",
                "--window-position=-32000,-32000",
                "--window-size=1280,800"
            ]
        )
        context = browser.new_context(viewport={"width": 1280, "height": 800}, user_agent=_USER_AGENT)
        
        with open(COOKIES_PATH, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        context.add_cookies(cookies)
        
        page = context.new_page()
        
        # --- ADVISOR LOGIC ---
        sell_names = []
        whatsapp_msg = "▶️ OSM Trainer Started\n"
        
        try:
            squad = scrape_squad(page)
            market = scrape_market(page)
            sell_candidates = get_sell_candidates(squad)
            transfers = recommend_transfers(squad, market)
            
            if sell_candidates or (transfers and (transfers.get("buys") or transfers.get("profit_flips"))):
                whatsapp_msg += "\n💡 *OSM Advisor Report*\n"
                
                if sell_candidates:
                    whatsapp_msg += "\n🗑️ *Players to Sell (Useless):*\n"
                    for idx, p in enumerate(sell_candidates):
                        if idx < 6:
                            val = p.get('value', 0)
                            if val < 5000000: mult = 2.5
                            elif val < 15000000: mult = 2.0
                            elif val < 25000000: mult = 1.7
                            elif val < 35000000: mult = 1.5
                            else: mult = 1.3
                            sell_price_m = round((val * mult) / 1000000, 1)
                            
                            whatsapp_msg += f"- {p.get('name')}: 💰 {sell_price_m}M\n"
                        elif idx == 6:
                            whatsapp_msg += f"- ...and {len(sell_candidates) - 6} more (auto-blacklisted)\n"
                            
                        # Dynamically extract ALL names to blacklist!
                        sell_names.append(p.get("name", "").lower())
                        
                if transfers and (transfers.get("buys") or transfers.get("profit_flips")):
                    whatsapp_msg += "\n🛒 *Recommended Upgrades:*\n"
                    for b in transfers.get("buys", []):
                        whatsapp_msg += f"- {b.get('upgrade_msg')}: 💰 {b.get('price_formatted')}\n"
                        
                        # Also blacklist the player being REPLACED so we don't waste training on them
                        # upgrade_msg format: "Upgrade DEF (Furlong 75) ➡️ NewPlayer (85)"
                        msg = b.get("upgrade_msg", "")
                        if "(" in msg and ")" in msg:
                            inner = msg.split("(")[1].split(")")[0]  # "Furlong 75"
                            replaced_name = inner.rsplit(" ", 1)[0]  # "Furlong"
                            if replaced_name:
                                sell_names.append(replaced_name.lower())
                                log.info("Blacklisted a player from training (being replaced by upgrade)")
                        
                if transfers and transfers.get("profit_flips"):
                    whatsapp_msg += "\n📈 *Best Profit Flips:*\n"
                    for f in transfers.get("profit_flips"):
                        whatsapp_msg += f"- {f.get('name')} ({get_osm_rating(f)}): Buy 💰{f.get('buy_price')}M ➡️ Sell 💰{f.get('sell_price')}M (+{f.get('profit')}M Profit)\n"
        except Exception as e:
            log.error(f"Advisor module failed: {e}")
            
        # We will NOT send the message immediately. We will combine it with the training summary later to reduce spam.
        # ------------------------
        
        claimed_count = 0
        started_players = []
        ads_watched = 0
        failed_players = []
        
        while True:
            try:
                log.info("Navigating to Training...")
                page.goto(f"{BASE_URL}/Training", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)
                
                try:
                    accept_btn = page.locator("button:has-text('Accept'), button:has-text('Akkoord')").first
                    if accept_btn.is_visible(timeout=2000):
                        accept_btn.click()
                        page.wait_for_timeout(1000)
                except:
                    pass

                # Smart training: fetch lineup from API and pick players intelligently
                smart_picks = {}
                if target_league_data:
                    smart_picks = get_smart_training_picks(
                        context,
                        target_league_data["league_id"],
                        target_league_data["team_id"],
                        do_not_train=["maignan"] + sell_names
                    )

                coach_mapping = {
                    "Universal coach": smart_picks.get("Universal coach", []),
                    "Attacking coach": smart_picks.get("Attacking coach", []),
                    "Midfielder coach": smart_picks.get("Midfielder coach", []),
                    "Defending coach": smart_picks.get("Defending coach", []),
                    "Goalkeeping coach": smart_picks.get("Goalkeeping coach", [])
                }
                
                # Players to NEVER train (e.g., players you are selling, or old players)
                do_not_train = ["maignan"] + sell_names
                ad_attempts = {name: 0 for name in coach_mapping.keys()}
                
                phase = "START"
                
                while True:
                    action_taken = False
                    
                    if phase == "START":
                        for coach_name, preferred_players in coach_mapping.items():
                            coach_els = page.locator(f"text='{coach_name}'")
                            coach_box = None
                            for i in range(coach_els.count()):
                                if coach_els.nth(i).is_visible():
                                    coach_box = coach_els.nth(i).bounding_box()
                                    break
                                    
                            if not coach_box: continue
                                
                            def find_button_in_column(selectors):
                                for sel in selectors:
                                    btns = page.locator(sel)
                                    for i in range(btns.count()):
                                        if btns.nth(i).is_visible():
                                            btn_box = btns.nth(i).bounding_box()
                                            if btn_box:
                                                c_center = coach_box['x'] + (coach_box['width'] / 2)
                                                b_center = btn_box['x'] + (btn_box['width'] / 2)
                                                if abs(c_center - b_center) < 100:
                                                    return btns.nth(i)
                                return None

                            # Check 1: CLAIM
                            claim_btn = find_button_in_column(["text='Claim'", "text='Finish'", "text='Complete'", "text='Collect'"])
                            if claim_btn:
                                log.info(f"✅ Claiming finished player for {coach_name}...")
                                claim_btn.click()
                                claimed_count += 1
                                page.wait_for_timeout(3000)
                                action_taken = True
                                break
                                
                            # Check 2: START
                            start_btn = find_button_in_column(["text='Start'"])
                            if start_btn:
                                log.info(f"Found empty slot for {coach_name}! Clicking Start...")
                                start_btn.click()
                                page.wait_for_timeout(3000)
                                
                                player_rows = page.locator(".modal-dialog tr, .modal-dialog li, div[role='dialog'] tr, div[role='dialog'] li")
                                if player_rows.count() > 0:
                                    selected = False
                                    selected_name = "Top Prospect"
                                    
                                    for row_idx in range(player_rows.count()):
                                        try:
                                            row_text = player_rows.nth(row_idx).inner_text().lower()
                                            for pref_name in preferred_players:
                                                if pref_name.lower() in [f.lower() for f in failed_players]:
                                                    continue
                                                if pref_name.lower() in [b.lower() for b in do_not_train]:
                                                    continue
                                                if pref_name in row_text:
                                                    log.info(f"Found designated player for {coach_name}. Selecting...")
                                                    player_rows.nth(row_idx).click()
                                                    selected = True
                                                    selected_name = pref_name.title()
                                                    break
                                            if selected: break
                                        except Exception: pass
                                            
                                    if not selected:
                                        log.info(f"Designated player for {coach_name} not found. Scanning for the smartest prospect...")
                                        import re
                                        best_idx = None
                                        best_score = 9999
                                        
                                        for row_idx in range(player_rows.count()):
                                            try:
                                                row_text = player_rows.nth(row_idx).inner_text()
                                                # Skip header row
                                                if "age" in row_text.lower() or "pos" in row_text.lower() or "leeftijd" in row_text.lower():
                                                    continue
                                                    
                                                # Strip the shirt number (first number at the start of the row)
                                                cleaned_text = re.sub(r'^\s*\d+', '', row_text).strip()
                                                
                                                # Skip players that OSM previously rejected (e.g., in starting lineup)
                                                if any(f.lower() in cleaned_text.lower() for f in failed_players):
                                                    continue
                                                    
                                                # Skip players the user manually blacklisted
                                                if any(b.lower() in cleaned_text.lower() for b in do_not_train):
                                                    continue
                                                    
                                                # Find all remaining standalone numbers
                                                matches = re.findall(r'\b\d+\b', cleaned_text)
                                                if matches:
                                                    age = int(matches[0])
                                                    
                                                    # Next 3 numbers are usually Att, Def, Ovr. Take the max to find their primary stat.
                                                    stats = [int(x) for x in matches[1:4]]
                                                    rating = max(stats) if stats else 99
                                                    
                                                    # OSM Age Brackets
                                                    if age <= 20: age_penalty = 0
                                                    elif age <= 24: age_penalty = 20
                                                    elif age <= 29: age_penalty = 50
                                                    else: age_penalty = 200
                                                    
                                                    # Score prioritizes lowest primary rating, but adds a penalty if they are older
                                                    score = rating + age_penalty
                                                    
                                                    if score < best_score:
                                                        best_score = score
                                                        best_idx = row_idx
                                            except Exception: pass
                                            
                                        if best_idx is not None:
                                            log.info("Selected best balanced prospect based on age and stats.")
                                            try:
                                                raw_text = player_rows.nth(best_idx).inner_text()
                                                cleaned_text = re.sub(r'^\s*\d+', '', raw_text).strip()
                                                selected_name = cleaned_text.split('\n')[0].split('\t')[0].strip()
                                            except:
                                                selected_name = f"Player {best_idx}"
                                                
                                            player_rows.nth(best_idx).click()
                                        else:
                                            log.warning("No valid players found to train! Escaping...")
                                            page.keyboard.press("Escape")
                                            page.wait_for_timeout(2000)
                                            continue
                                        
                                    page.wait_for_timeout(2000)
                                    
                                    # Verification: Did the modal actually close?
                                    modal_locators = page.locator(".modal-dialog, div[role='dialog']")
                                    is_any_modal_visible = False
                                    for i in range(modal_locators.count()):
                                        if modal_locators.nth(i).is_visible():
                                            is_any_modal_visible = True
                                            break
                                            
                                    if is_any_modal_visible:
                                        log.warning("❌ OSM rejected the selected player! (Likely in starting 11 or an alert popped up). Adding to ignore list.")
                                        failed_players.append(selected_name)
                                        page.keyboard.press("Escape")
                                        page.wait_for_timeout(2000)
                                        # Press escape again in case there's an alert modal AND the train modal
                                        page.keyboard.press("Escape")
                                        page.wait_for_timeout(1000)
                                        action_taken = True
                                        break
                                        
                                    log.info(f"✅ Started training for a player in {coach_name}")
                                    started_players.append(selected_name)
                                    page.wait_for_timeout(2000)
                                else:
                                    log.warning("Modal did not appear or no players found. Escaping...")
                                    page.keyboard.press("Escape")
                                    page.wait_for_timeout(2000)
                                    
                                action_taken = True
                                break
                                
                        if action_taken:
                            page.reload(wait_until="domcontentloaded")
                            page.wait_for_timeout(4000)
                            continue
                        else:
                            log.info("All slots are filled and training! Moving to Ads phase...")
                            phase = "ADS"
                            continue
                            
                    elif phase == "ADS":
                        for coach_name, preferred_players in coach_mapping.items():
                            coach_els = page.locator(f"text='{coach_name}'")
                            coach_box = None
                            for i in range(coach_els.count()):
                                if coach_els.nth(i).is_visible():
                                    coach_box = coach_els.nth(i).bounding_box()
                                    break
                                    
                            if not coach_box: continue
                                
                            def find_button_in_column(selectors):
                                for sel in selectors:
                                    btns = page.locator(sel)
                                    for i in range(btns.count()):
                                        if btns.nth(i).is_visible():
                                            btn_box = btns.nth(i).bounding_box()
                                            if btn_box:
                                                c_center = coach_box['x'] + (coach_box['width'] / 2)
                                                b_center = btn_box['x'] + (btn_box['width'] / 2)
                                                if abs(c_center - b_center) < 100:
                                                    return btns.nth(i)
                                return None

                            if ad_attempts[coach_name] < 3:
                                ad_btn = find_button_in_column(["button[data-bind*='boostTrainingSessionWithVideo']"])
                                if ad_btn:
                                    log.info(f"📺 Found an ad button for {coach_name}! Watching...")
                                    ad_btn.click()
                                    
                                    page.wait_for_timeout(3000)
                                    limit_popup = page.locator("text=maximum of videos").first
                                    
                                    if limit_popup.is_visible():
                                        log.warning(f"Ads exhausted globally! Exiting ad loop.")
                                        for k in ad_attempts.keys():
                                            ad_attempts[k] = 3
                                            
                                        ok_btn = page.locator("button:has-text('Ok'), .btn:has-text('Ok')").first
                                        if ok_btn.is_visible():
                                            ok_btn.click()
                                            page.wait_for_timeout(1000)
                                            
                                        # Break the for loop completely. action_taken remains False.
                                        break
                                    else:
                                        ad_attempts[coach_name] += 1
                                        ads_watched += 1
                                        send_whatsapp_message(f"📺 Watching ad #{ads_watched} for {coach_name} to speed up training (waiting 65s)...")
                                        page.wait_for_timeout(65000)
                                        
                                        action_taken = True
                                        continue
                                    
                        if action_taken:
                            page.reload(wait_until="domcontentloaded")
                            page.wait_for_timeout(4000)
                            # Revert to START phase in case an ad finished a training timer!
                            phase = "START"
                            continue
                        else:
                            # No more actions available anywhere! Break out of while True loop.
                            break
                        
                # If we reach here, NO actions were taken for ANY coach. We are completely done.
                log.info("No actions left for any coach! All slots are training and in cooldown.")
                
                summary = "✅ *Training Update*\n"
                if claimed_count > 0:
                    summary += f"\n🏆 Claimed {claimed_count} finished players!"
                if started_players:
                    summary += f"\n⚽ Started training: {', '.join(started_players)}"
                if ads_watched > 0:
                    summary += f"\n📺 Watched {ads_watched} ads to reduce timers!"
                
                if claimed_count == 0 and not started_players and ads_watched == 0:
                    summary += "\nNo actions needed. Training in progress."
                    
                final_msg = whatsapp_msg + "\n\n" + summary + "\n----------------------------"
                send_whatsapp_message(final_msg)
                return
                    
            except Exception as e:
                log.error(f"Error in training loop: {e}")
                log.info("Exiting on error...")
                return

if __name__ == "__main__":
    training_loop()
