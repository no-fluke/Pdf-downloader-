import os
import re
import asyncio
import logging
import threading
import time
import random
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
RENDER_APP_URL = os.getenv('RENDER_APP_URL', '')

# Store user data
user_files = {}
user_processing = {}
user_progress_messages = {}

# Keep-alive configuration
KEEP_ALIVE_INTERVAL = 10 * 60
last_activity = time.time()

def create_progress_bar(current, total, bar_length=20):
    """Create a visual progress bar"""
    progress = current / total
    filled_length = int(bar_length * progress)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    percentage = int(progress * 100)
    return f"{bar} {percentage}%"

# Simple HTTP server for health checks
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global last_activity
        last_activity = time.time()
        
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        elif self.path == '/wake':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'AWAKE')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        return

def run_health_server():
    """Run a simple HTTP server for health checks"""
    port = int(os.getenv('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"Health server running on port {port}")
    server.serve_forever()

def keep_alive_ping():
    """Ping the app itself to keep it awake"""
    if RENDER_APP_URL:
        try:
            response = requests.get(f"{RENDER_APP_URL}/wake", timeout=10)
            logger.info(f"Keep-alive ping sent: {response.status_code}")
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")

def keep_alive_worker():
    """Background thread to keep the app alive"""
    while True:
        time.sleep(KEEP_ALIVE_INTERVAL)
        keep_alive_ping()

def update_activity():
    """Update the last activity timestamp"""
    global last_activity
    last_activity = time.time()

def setup_selenium():
    """Setup reliable headless Chrome for Selenium"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--remote-debugging-port=9222")
    
    # For Render environment
    chrome_options.binary_location = "/usr/bin/chromium"
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        return driver
    except Exception as e:
        logger.error(f"Failed to setup Selenium: {e}")
        return None

def extract_play_link_reliable(share_link, max_retries=3):
    """Extract play link with retries and proper error handling"""
    for attempt in range(max_retries):
        try:
            driver = setup_selenium()
            if not driver:
                time.sleep(2)
                continue
                
            logger.info(f"Attempt {attempt + 1} for: {share_link}")
            driver.get(share_link)
            
            # Wait with progressive delay
            wait_time = random.uniform(3, 6)
            time.sleep(wait_time)
            
            play_link = None
            
            # Strategy 1: Look for iframes
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                try:
                    src = iframe.get_attribute("src")
                    if src and "zoom.us/rec/play" in src:
                        play_link = src
                        logger.info(f"Found play link in iframe: {play_link[:60]}...")
                        break
                except Exception as e:
                    logger.warning(f"Error reading iframe: {e}")
                    continue
            
            # Strategy 2: Check current URL
            if not play_link:
                current_url = driver.current_url
                if "zoom.us/rec/play" in current_url:
                    play_link = current_url
                    logger.info(f"Found play link in URL: {play_link[:60]}...")
            
            # Strategy 3: Look for video elements
            if not play_link:
                videos = driver.find_elements(By.TAG_NAME, "video")
                for video in videos:
                    try:
                        src = video.get_attribute("src")
                        if src and "zoom.us" in src:
                            play_link = src
                            logger.info(f"Found play link in video: {play_link[:60]}...")
                            break
                    except Exception as e:
                        logger.warning(f"Error reading video: {e}")
                        continue
            
            driver.quit()
            
            if play_link:
                # Add necessary parameters
                if "?" not in play_link:
                    play_link += "?eagerLoadZvaPages=&isReferralProgramEnabled=false&isReferralProgramAvailable=false&accessLevel=meeting&canPlayFromShare=true&from=share_recording_detail&continueMode=true&componentName=rec-play"
                
                return play_link
            else:
                logger.warning(f"No play link found on attempt {attempt + 1}")
                time.sleep(2)
                
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            try:
                driver.quit()
            except:
                pass
            time.sleep(2)
    
    return None

async def process_links_sequentially(update, context, user_id, original_filename):
    """Process links one by one with progress tracking"""
    try:
        if user_id not in user_files:
            return
        
        content = user_files[user_id]
        lines = content.split('\n')
        total_links = 0
        link_positions = []
        
        # Find all links and their positions
        for i, line in enumerate(lines):
            if "zoom.us/rec/share" in line:
                share_link_match = re.search(r'https://[^\s]+', line)
                if share_link_match:
                    total_links += 1
                    link_positions.append((i, line, share_link_match.group(0)))
        
        if total_links == 0:
            await update.message.reply_text("❌ No Zoom share links found in the file!")
            return
        
        # Send initial progress message
        progress_msg = await update.message.reply_text(
            f"🔗 **Starting Processing**\n"
            f"📊 Progress: {create_progress_bar(0, total_links)}\n"
            f"⏳ Status: Initializing...\n"
            f"✅ Successful: 0\n"
            f"❌ Failed: 0"
        )
        user_progress_messages[user_id] = progress_msg.message_id
        
        updated_lines = lines.copy()
        success_count = 0
        failed_count = 0
        
        # Process each link one by one
        for current, (line_index, original_line, share_link) in enumerate(link_positions, 1):
            # Check if processing was stopped
            if user_id not in user_processing or not user_processing[user_id]:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=progress_msg.message_id,
                    text="❌ Processing stopped by user."
                )
                return
            
            # Update activity to prevent sleep
            update_activity()
            
            # Extract play link
            play_link = extract_play_link_reliable(share_link)
            
            if play_link:
                updated_line = original_line.replace(share_link, play_link)
                updated_lines[line_index] = updated_line
                success_count += 1
                logger.info(f"✅ Successfully converted link {current}/{total_links}")
            else:
                updated_lines[line_index] = f"# FAILED: {original_line}"
                failed_count += 1
                logger.warning(f"❌ Failed to convert link {current}/{total_links}")
            
            # Update progress message with progress bar
            progress_text = (
                f"🔗 **Processing Link {current}/{total_links}**\n"
                f"📊 Progress: {create_progress_bar(current, total_links)}\n"
                f"⏳ Status: Extracting playable link...\n"
                f"✅ Successful: {success_count}\n"
                f"❌ Failed: {failed_count}"
            )
            
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=progress_msg.message_id,
                text=progress_text
            )
            
            # Random delay between 1-10 seconds
            delay = random.uniform(1, 10)
            time.sleep(delay)
        
        # Final summary
        summary = f"# Processed {total_links} links, {success_count} successful, {failed_count} failed\n"
        if failed_count > 0:
            summary += f"# Failed links: {failed_count}\n"
        
        updated_content = summary + '\n'.join(updated_lines)
        
        # Send final result
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=progress_msg.message_id,
            text=f"✅ **Processing Complete!**\n"
                 f"📊 Total: {total_links} links\n"
                 f"✅ Successful: {success_count}\n"
                 f"❌ Failed: {failed_count}"
        )
        
        # Send the updated file with original filename
        await update.message.reply_document(
            document=updated_content.encode('utf-8'),
            filename=original_filename,
            caption=f"Processed: {success_count}/{total_links} links converted"
        )
        
        # Cleanup
        if user_id in user_files:
            del user_files[user_id]
        if user_id in user_processing:
            del user_processing[user_id]
        if user_id in user_progress_messages:
            del user_progress_messages[user_id]
            
    except Exception as e:
        logger.error(f"Error in sequential processing: {e}")
        await update.message.reply_text(f"❌ Processing error: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    update_activity()
    await update.message.reply_text(
        "🤖 **Zoom Link Extractor Bot**\n\n"
        "Send me a text file with Zoom share links and I'll extract playable links!\n\n"
        "**Commands:**\n"
        "/start - Show this message\n"
        "/process - Start processing\n"
        "/stop - Stop current processing\n"
        "/wake - Keep the bot awake\n\n"
        "**Features:**\n"
        "• One-by-one link processing\n"
        "• Visual progress bar\n"
        "• Real-time success/failure tracking\n"
        "• Original filename preserved\n"
        "• Reliable extraction with retries\n"
        "• Keep-alive mechanism to prevent sleeping"
    )

async def handle_wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual wake command"""
    update_activity()
    keep_alive_ping()
    await update.message.reply_text("🔔 Bot is awake and active!")

async def handle_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start processing links"""
    update_activity()
    user_id = update.effective_user.id
    
    if user_id not in user_files or not user_files[user_id]:
        await update.message.reply_text("❌ Please send a text file first, then use /process")
        return
    
    # Check if already processing
    if user_id in user_processing and user_processing[user_id]:
        await update.message.reply_text("⚠️ Processing is already running. Use /stop to cancel.")
        return
    
    # Get original filename from stored data
    original_filename = user_files.get(f"{user_id}_filename", "updated_links.txt")
    
    # Start processing
    user_processing[user_id] = True
    
    # Run processing in background
    asyncio.create_task(process_links_sequentially(update, context, user_id, original_filename))

async def handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop current processing"""
    update_activity()
    user_id = update.effective_user.id
    
    if user_id in user_processing and user_processing[user_id]:
        user_processing[user_id] = False
        await update.message.reply_text("🛑 Processing stopped.")
        
        # Cleanup progress message
        if user_id in user_progress_messages:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=user_progress_messages[user_id]
                )
            except:
                pass
            del user_progress_messages[user_id]
    else:
        await update.message.reply_text("❌ No active processing to stop.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document upload"""
    update_activity()
    try:
        document = update.message.document
        user_id = update.effective_user.id
        
        # Check if it's a text file
        if not document.mime_type == 'text/plain' and not document.file_name.endswith('.txt'):
            await update.message.reply_text("❌ Please send a text file (.txt)")
            return
        
        # Stop any existing processing
        if user_id in user_processing and user_processing[user_id]:
            user_processing[user_id] = False
            await update.message.reply_text("🛑 Stopped previous processing for new file.")
        
        # Download the file
        file = await context.bot.get_file(document.file_id)
        file_content = await file.download_as_bytearray()
        content = file_content.decode('utf-8')
        
        # Store file content and original filename
        user_files[user_id] = content
        user_files[f"{user_id}_filename"] = document.file_name
        
        total_links = len([l for l in content.split('\n') if 'zoom.us/rec/share' in l])
        
        await update.message.reply_text(
            f"📁 **File Received: {document.file_name}**\n"
            f"🔗 Found {total_links} Zoom links\n\n"
            f"**Commands:**\n"
            f"/process - Start processing\n"
            f"/stop - Stop processing\n"
            f"/wake - Keep bot awake\n\n"
            f"💡 **Processing will show:**\n"
            f"• Visual progress bar 📊\n"
            f"• Real-time success/failure counts\n"
            f"• Current link being processed\n"
            f"• Original filename preserved"
        )
        
    except Exception as e:
        logger.error(f"Error handling document: {e}")
        await update.message.reply_text(f"❌ Error reading file: {str(e)}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    error = context.error
    if "terminated by other getUpdates request" in str(error):
        logger.warning("Another bot instance is running. This is normal during deployment.")
        return
    logger.error(f"Update {update} caused error {error}")

def main():
    """Start the bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is not set!")
        return
    
    # Start health server in a separate thread
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    
    # Start keep-alive worker if RENDER_APP_URL is set
    if RENDER_APP_URL:
        keep_alive_thread = threading.Thread(target=keep_alive_worker, daemon=True)
        keep_alive_thread.start()
        logger.info("Keep-alive worker started")
    else:
        logger.warning("RENDER_APP_URL not set - keep-alive disabled")
    
    # Create and configure bot application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("process", handle_process))
    application.add_handler(CommandHandler("stop", handle_stop))
    application.add_handler(CommandHandler("wake", handle_wake))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_error_handler(error_handler)
    
    logger.info("Bot is starting with visual progress bar...")
    
    # Start the bot
    try:
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        time.sleep(10)
        logger.info("Retrying to start bot...")
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )

if __name__ == '__main__':
    main()
