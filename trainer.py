import os
import time
import random
import json
import logging
from playwright.sync_api import sync_playwright
import urllib.parse
import urllib.request

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
logging.basicConfig(level=logging.INFO, format="%(asctime)s │ %(levelname)-8s │ %(message)s", datefmt="%H:%M:%S")
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
        
        for l in leagues:
            if "liverpool" in l["team_name"].lower() and "winners cup" in l["league_name"].lower():
                target_slot = l["slot_index"]
                target_league_data = l
                break

        if target_slot is None:
            log.warning("Could not find Liverpool in Winners Cup exactly. Looking for any Liverpool...")
            for l in leagues:
                if "liverpool" in l["team_name"].lower():
                    target_slot = l["slot_index"]
                    target_league_data = l
                    break
                    
        if target_slot is not None:
            log.info(f"Switching to slot {target_slot}...")
            switch_league_slot(pw, target_league_data)
        else:
            log.error("Could not find Liverpool in any slot. Continuing anyway...")

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
        send_whatsapp_message("▶️ OSM Trainer Started\nChecking for finished training and ads...")
        
        claimed_count = 0
        started_players = []
        ads_watched = 0
        
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

                coach_mapping = {
                    "Universal coach": ["zaïre", "zaire", "emery"],
                    "Attacking coach": ["iwobi"],
                    "Midfielder coach": ["nmecha"],
                    "Defending coach": ["kalulu"],
                    "Goalkeeping coach": ["remiro"]
                }
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
                                continue
                                
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
                                                if pref_name in row_text:
                                                    log.info(f"Found designated player '{pref_name}' for {coach_name}. Selecting...")
                                                    player_rows.nth(row_idx).click()
                                                    selected = True
                                                    selected_name = pref_name.title()
                                                    break
                                            if selected: break
                                        except Exception: pass
                                            
                                    if not selected:
                                        log.info(f"Designated player for {coach_name} not found. Falling back to top prospect...")
                                        try:
                                            first_row_text = player_rows.nth(0).inner_text().lower()
                                            fallback_idx = 1 if "age" in first_row_text or "pos" in first_row_text else 0
                                            if player_rows.count() > fallback_idx:
                                                player_rows.nth(fallback_idx).click()
                                            else:
                                                player_rows.nth(0).click()
                                        except:
                                            player_rows.nth(0).click()
                                        
                                    log.info(f"Started training for: {selected_name} in {coach_name}")
                                    started_players.append(selected_name)
                                    page.wait_for_timeout(3000)
                                else:
                                    log.warning("Modal did not appear or no players found. Escaping...")
                                    page.keyboard.press("Escape")
                                    page.wait_for_timeout(2000)
                                    
                                action_taken = True
                                continue
                                
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
                        
                    send_whatsapp_message(summary)
                    send_whatsapp_message("----------------------------")
                    return
                    
            except Exception as e:
                log.error(f"Error in training loop: {e}")
                log.info("Exiting on error...")
                return

if __name__ == "__main__":
    training_loop()
