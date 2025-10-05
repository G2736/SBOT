# slot_bot_pro.py

# --- Core Imports ---
import logging
import threading
import json
import time
from datetime import datetime, timedelta
import schedule
import getpass
import os
import questionary
from colorama import Fore, Style, init
import random # <-- Import the random module

# --- New Feature Imports ---
from selenium_stealth import stealth
import pywhatkit

# --- Selenium Imports ---
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService

# --- Initialize Colorama ---
init(autoreset=True)

# --- Logging Configuration ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler('slot_bot.log', mode='w')
file_handler.setLevel(logging.INFO)
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(stream_handler)


# --- Configuration Loading ---
try:
    with open('config.json', 'r') as f:
        CONFIG = json.load(f)
    logger.info("config.json loaded successfully.")
except FileNotFoundError:
    logger.error("FATAL: config.json not found. Please create it before running.")
    exit()
except json.JSONDecodeError:
    logger.error("FATAL: config.json is malformed. Please check its syntax.")
    exit()


# --- Global State Management ---
slot_list = []
active_threads = []
active_drivers = []
is_running = True
session_config = { "username": "", "password": "" }


# --- Notification Function ---
def send_whatsapp_notification(message):
    if not CONFIG['whatsapp']['enabled']:
        return
    
    phone_number = CONFIG['whatsapp']['phone_number']
    if not phone_number or "0000000000" in phone_number:
        logger.warning("WhatsApp number not configured in config.json. Skipping notification.")
        return

    def send_in_thread():
        try:
            logger.info(f"Attempting to send WhatsApp notification to {phone_number}...")
            pywhatkit.sendwhatmsg_instantly(phone_number, message, wait_time=20, tab_close=True, close_time=10)
            logger.info("WhatsApp notification sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send WhatsApp notification: {e}")
            logger.warning("This may be due to not being logged into WhatsApp Web.")

    threading.Thread(target=send_in_thread, daemon=True, name="WhatsAppThread").start()


# --- Core Booking Logic ---
def check_gpu_availability():
    try:
        import GPUtil
        gpus = GPUtil.getGPUs()
        if gpus and any(gpu.load < 0.9 for gpu in gpus):
            return True, "--enable-gpu"
    except (ImportError, Exception):
        pass
    return False, "--disable-gpu"

def slot_booking_process(username, password, day, date, start_time, end_time, scheduler_url, room_name):
    driver = None
    try:
        use_gpu, gpu_arg = check_gpu_availability()
        logger.info(f"Thread started for slot '{start_time}'. Using {'GPU' if use_gpu else 'CPU'}.")
        
        browser_name = CONFIG['settings']['browser']
        is_headless = CONFIG['settings']['headless']
        
        # --- NEW: Read min and max refresh from config ---
        min_delay = CONFIG['stealth_settings']['min_refresh_seconds']
        max_delay = CONFIG['stealth_settings']['max_refresh_seconds']

        if browser_name == "Chrome":
            options = ChromeOptions()
            if is_headless:
                options.add_argument("--headless=new")
                options.add_argument("--no-sandbox")
            options.add_argument(gpu_arg)
            options.add_argument("--page-load-strategy=eager")
            options.add_argument("--disable-images")
            options.add_argument("--log-level=3")
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            
            stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)
        else:
            logger.error(f"Browser '{browser_name}' is not supported.")
            return

        active_drivers.append(driver)
        logger.info("Navigating to login page...")
        driver.get(CONFIG['urls']['login_page'])
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, 'username'))).send_keys(username)
        driver.find_element(By.NAME, 'password').send_keys(password)
        driver.find_element(By.ID, 'loginbtn').click()
        logger.info("Logged in successfully.")

        date_obj = datetime.strptime(date.strip(), "%d %m %Y")
        formatted_date = date_obj.strftime("%d %B %Y")
        expected_date_formats = [formatted_date, f"{day.strip()}, {formatted_date}", date_obj.strftime("%A, %d %B %Y")]
        logger.info(f"Targeting slot: {day}, {date}, {start_time}-{end_time}")

        while True:
            sleep_duration = random.uniform(min_delay, max_delay)
            try:
                logger.info(f"Navigating to scheduler and searching for slot '{start_time}'...")
                driver.get(scheduler_url)

                try:
                    WebDriverWait(driver, 2).until(
                        EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'No slots are available for booking at this time')]"))
                    )
                    logger.info(f"No slots available. Hunting for cancellations... retrying in {sleep_duration:.1f}s")
                    time.sleep(sleep_duration)
                    continue 
                except TimeoutException:
                    pass

                all_rows = WebDriverWait(driver, 10).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "table.generaltable tr")))
                
                slot_found_and_booked = False
                for row in all_rows:
                    if any(fmt in row.text for fmt in expected_date_formats):
                        if start_time in row.text and end_time in row.text:
                            logger.info(f"Found matching slot: {start_time}-{end_time}")
                            book_button = row.find_element(By.XPATH, ".//a[contains(text(), 'Book slot')] | .//button[contains(text(), 'Book slot')]")
                            driver.execute_script("arguments[0].click();", book_button)
                            
                            booking_confirmed = False
                            for _ in range(20):
                                try:
                                    note_field = driver.find_element(By.ID, "id_studentnote_editoreditable")
                                    note_field.send_keys("Automated booking")
                                    submit_button = driver.find_element(By.ID, "id_submitbutton")
                                    driver.execute_script("arguments[0].click();", submit_button)
                                    booking_confirmed = True
                                    break
                                except NoSuchElementException:
                                    time.sleep(0.05)

                            if booking_confirmed:
                                logger.info(f"SUCCESS! Slot booked for {start_time}-{end_time}! ✅")
                                success_message = f"✅ Slot Booked!\n\nRoom: {room_name}\nDate: {day}, {date}\nTime: {start_time} - {end_time}"
                                send_whatsapp_notification(success_message)
                                slot_found_and_booked = True
                                break
                            else:
                                logger.error("Found slot but failed to confirm booking.")
                if slot_found_and_booked:
                    break
                    
            except (StaleElementReferenceException, TimeoutException):
                logger.warning(f"Slot '{start_time}' not found or page refreshed. Retrying in {sleep_duration:.1f} seconds...")
            except Exception as e:
                logger.error(f"An unexpected error occurred in the search loop: {e}")
            
            time.sleep(sleep_duration)

    except Exception as e:
        logger.error(f"A critical error occurred in the booking thread: {e}", exc_info=True)
    finally:
        if driver and driver in active_drivers:
            active_drivers.remove(driver)
            driver.quit()
        logger.info(f"Thread for slot '{start_time}' finished.")


# --- CLI Functions ---
def add_slot_interactive():
    global slot_list
    logger.info("Entering intelligent slot adding menu.")
    
    schedules = CONFIG['urls']['schedules']
    room_choices = list(schedules.keys())
    selected_room_name = questionary.select("Which room do you want to book a slot in?", choices=room_choices).ask()
    if not selected_room_name: return

    scheduler_url = schedules[selected_room_name]
    
    date_str = None
    date_choice = questionary.select(
        "Which date?",
        choices=["Today", "Tomorrow", "Enter a specific date"]
    ).ask()

    if date_choice == "Today":
        date_obj = datetime.now()
        date_str = date_obj.strftime("%d %m %Y")
    elif date_choice == "Tomorrow":
        date_obj = datetime.now() + timedelta(days=1)
        date_str = date_obj.strftime("%d %m %Y")
    elif date_choice == "Enter a specific date":
        date_str = questionary.text("Enter the Date (e.g., 04 10 2025):").ask()
    
    if not date_str: return
    
    try:
        date_obj = datetime.strptime(date_str.strip(), "%d %m %Y")
        day = date_obj.strftime("%A")
    except ValueError:
        logger.error(f"User entered invalid date format: {date_str}"); return

    start_time, end_time = None, None
    if "2 hr slot" in selected_room_name:
        two_hour_slots = ["8:00 AM", "10:00 AM", "1:00 PM", "3:00 PM"]
        start_time = questionary.select("Select a 2-hour time slot:", choices=two_hour_slots).ask()
        if start_time:
            st_obj = datetime.strptime(start_time, "%I:%M %p")
            et_obj = st_obj + timedelta(hours=2)
            end_time = et_obj.strftime("%I:%M %p").replace(" 0", " ").lstrip('0')
    else:
        start_time = questionary.text("Enter your desired Start Time (e.g., 9:00 AM):").ask()
        if start_time:
            try:
                st_obj = datetime.strptime(start_time, "%I:%M %p")
                et_obj = st_obj + timedelta(hours=1)
                end_time = et_obj.strftime("%I:%M %p").replace(" 0", " ").lstrip('0')
            except ValueError:
                logger.error("Invalid time format entered by user."); return

    if all([day, date_str, start_time, end_time]):
        slot = {"room_name": selected_room_name, "scheduler_url": scheduler_url, "day": day, "date": date_str, "start_time": start_time, "end_time": end_time}
        slot_list.append(slot)
        logger.info(f"Slot added successfully: {slot}")
        questionary.print("✅ Slot added successfully!", style="bold fg:green")
    else:
        logger.warning("Slot creation cancelled or failed.")
    time.sleep(1.5)

def view_and_remove_slots():
    global slot_list
    logger.info("Entering 'view/remove slots' menu.")
    if not slot_list:
        questionary.print("ℹ️ No slots have been added yet.", style="bold fg:yellow")
        time.sleep(1.5); return

    slot_choices = [f"{s['room_name']} | {s['day']}, {s['date']}, {s['start_time']}-{s['end_time']}" for s in slot_list]
    slots_to_remove_str = questionary.checkbox('Select slots to remove (use spacebar, press enter):', choices=slot_choices).ask()

    if slots_to_remove_str:
        original_count = len(slot_list)
        slot_list = [slot for slot_str, slot in zip(slot_choices, slot_list) if slot_str not in slots_to_remove_str]
        removed_count = original_count - len(slot_list)
        logger.info(f"User removed {removed_count} slot(s).")
        questionary.print(f"✅ Removed {removed_count} slot(s).", style="bold fg:green")
        time.sleep(1.5)

def run_booking():
    username = session_config.get('username')
    password = session_config.get('password')
    
    if not all([username, password]):
        logger.error("Booking attempted without username or password being set.")
        return
    if not slot_list:
        logger.error("Booking attempted with no slots in the list.")
        return

    logger.info(f"Starting booking process for {len(slot_list)} slot(s)...")
    for slot in slot_list:
        thread = threading.Thread(
            target=slot_booking_process, 
            args=(username, password, slot["day"], slot["date"], slot["start_time"], slot["end_time"], slot["scheduler_url"], slot["room_name"]),
            daemon=True,
            name=f"SlotThread-{slot['start_time']}"
        )
        active_threads.append(thread)
        thread.start()
    questionary.print(f"🚀 Booking process started for {len(slot_list)} slot(s). Check logs for details.", style="bold fg:cyan")
    time.sleep(2)

def stop_all_processes():
    logger.info("Stop command issued by user. Stopping all processes...")
    schedule.clear()
    logger.info("All scheduled jobs cleared.")
    for driver in active_drivers[:]:
        try:
            driver.quit()
            active_drivers.remove(driver)
        except: pass
    logger.info("All active browser instances have been closed.")

def run_scheduler_in_background():
    while is_running:
        schedule.run_pending(); time.sleep(1)

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

# --- Updated Banner Function ---
def display_banner():
    """Prints a stylized banner for the bot in blue."""
    banner = """
######################################################################
###                                                                ###
###   #####   #        #####   #####    #####    #####   #####     ###
###   #       #        #   #     #      #   #    #   #     #       ###
###   #####   #        #   #     #      ####     #   #     #       ###
###       #   #        #   #     #      #   #    #   #     #       ###
###   #####   ######   #####     #      #####    #####     #       ###  
###                                                                ###
######################################################################
"""
    print(Fore.BLUE + Style.BRIGHT + banner)
    
def main_menu():
    global is_running
    scheduler_thread = threading.Thread(target=run_scheduler_in_background, daemon=True, name="SchedulerThread")
    scheduler_thread.start()
    
    clear_screen()
    display_banner()

    username_from_config = CONFIG['credentials'].get('username', '')
    if not username_from_config or "YOUR_REGISTER_NUMBER_HERE" in username_from_config:
        logger.warning("Username not found in config.json, asking user.")
        try:
            username = questionary.text("Please enter your LMS Username/ID to begin:").ask()
            if not username:
                logger.error("Username not provided. Exiting.")
                return
            session_config['username'] = username
        except KeyboardInterrupt:
            logger.warning("Username entry cancelled. Exiting.")
            return
    else:
        session_config['username'] = username_from_config

    password_from_config = CONFIG['credentials'].get('password', '')
    if password_from_config:
        logger.warning("Loading password from config.json. This is convenient but NOT secure.")
        session_config['password'] = password_from_config
    else:
        try:
            password = questionary.password("Please enter your LMS Password:").ask()
            if not password:
                logger.warning("No password entered. Exiting.")
                return
            session_config['password'] = password
        except KeyboardInterrupt:
            logger.warning("Password entry cancelled. Exiting.")
            return

    while True:
        question_text = f"What would you like to do? ({len(slot_list)} slots queued)"
        
        choice = questionary.select(
            question_text,
            choices=["Add Slot (Interactive)", "View / Remove Slots", "RUN BOOKING NOW", "Stop All Processes", "Exit"]
        ).ask()

        if choice == "Add Slot (Interactive)":
            clear_screen()
            display_banner()
            add_slot_interactive()
        elif choice == "View / Remove Slots":
            clear_screen()
            display_banner()
            view_and_remove_slots()
        elif choice == "RUN BOOKING NOW":
            run_booking()
        elif choice == "Stop All Processes":
            stop_all_processes()
        elif choice == "Exit" or choice is None:
            break
        
        clear_screen()
        display_banner()

    logger.info("Exit command received. Shutting down.")
    is_running = False
    stop_all_processes()

if __name__ == "__main__":
    logger.info("Application starting.")
    try:
        main_menu()
    except (KeyboardInterrupt):
        logger.info("Ctrl+C detected. Shutting down gracefully.")
    finally:
        is_running = False
        stop_all_processes()
        logger.info("Application has shut down.")