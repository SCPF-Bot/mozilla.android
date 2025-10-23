import os
import random
import subprocess
import requests
import magic
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
import time
import sys
from PIL import Image
from io import BytesIO
from bs4 import BeautifulSoup
import shutil

# Realistic user agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/118.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/118.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Edg/118.0.2088.46",
]

def get_random_user_agent():
    return random.choice(USER_AGENTS)

def get_true_filename(url):
    user_agent = get_random_user_agent()
    try:
        response = requests.head(url, headers={"User-Agent": user_agent}, allow_redirects=True)
        if response.status_code == 200:
            content_disp = response.headers.get('Content-Disposition')
            if content_disp:
                import re
                fname = re.findall("filename=(.+)", content_disp)
                if fname:
                    return fname[0].strip('"')
            content_type = response.headers.get('Content-Type', '').split(';')[0]
            ext = mimetypes.guess_extension(content_type) or ''
            parsed = urlparse(url)
            fname = os.path.basename(parsed.path)
            if not fname.endswith(ext):
                fname += ext
            return fname
    except:
        pass
    parsed = urlparse(url)
    return os.path.basename(parsed.path) or 'downloaded_file'

def download_with_progress(url, output_path):
    user_agent = get_random_user_agent()
    # Try wget first
    try:
        subprocess.run(['wget', '--user-agent', user_agent, '--progress=bar:force:noscroll', '-O', output_path, url], check=True)
        return True
    except:
        pass
    # Fallback to curl
    try:
        subprocess.run(['curl', '-A', user_agent, '--progress-bar', '-o', output_path, url], check=True)
        return True
    except:
        return False

def setup_selenium():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"user-agent={get_random_user_agent()}")
    driver = webdriver.Chrome(options=options)
    return driver

def scrape_magnet(url):
    driver = setup_selenium()
    try:
        driver.get(url)
        time.sleep(5)  # Wait for load
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        magnet_links = [a['href'] for a in soup.find_all('a', href=True) if a['href'].startswith('magnet:')]
        if magnet_links:
            return magnet_links[0]  # Take first
        # Or find buttons, etc.
        try:
            magnet_button = driver.find_element(By.XPATH, "//a[contains(@href, 'magnet:')]")
            return magnet_button.get_attribute('href')
        except NoSuchElementException:
            pass
    finally:
        driver.quit()
    return None

def download_torrent_or_magnet(link, output_dir):
    if link.startswith('magnet:'):
        subprocess.run(['aria2c', '--dir', output_dir, '--summary-interval=0', link], check=True)
        return True
    elif link.endswith('.torrent'):
        torrent_file = os.path.join(output_dir, 'temp.torrent')
        if download_with_progress(link, torrent_file):
            subprocess.run(['aria2c', '--dir', output_dir, '--summary-interval=0', torrent_file], check=True)
            os.remove(torrent_file)
            return True
    else:
        # Assume it's a page with magnet
        magnet = scrape_magnet(link)
        if magnet:
            subprocess.run(['aria2c', '--dir', output_dir, '--summary-interval=0', magnet], check=True)
            return True
    return False

def is_torrent_related(link):
    return link.startswith('magnet:') or link.endswith('.torrent') or 'torrent' in link.lower()

def rename_file(file_path):
    dirname, filename = os.path.split(file_path)
    filename = filename.replace(' ', '.')
    prefix = f"{random.randint(0, 9999):04d}."
    new_name = prefix + filename
    new_path = os.path.join(dirname, new_name)
    os.rename(file_path, new_path)
    return new_path

def compress_file(file_path):
    mime = magic.Magic(mime=True).from_file(file_path)
    if 'video' in mime:
        output = file_path + '.compressed.mp4'
        subprocess.run(['ffmpeg', '-i', file_path, '-vf', 'scale=-2:480', '-crf', '28', '-preset', 'slow', output], check=True)
        os.remove(file_path)
        os.rename(output, file_path)
    elif 'image' in mime:
        with Image.open(file_path) as img:
            if img.mode in ('RGBA', 'LA'):
                img = img.convert('RGB')
            img.save(file_path, quality=50, optimize=True)
    elif 'audio' in mime:
        output = file_path + '.compressed.mp3'
        subprocess.run(['ffmpeg', '-i', file_path, '-b:a', '64k', output], check=True)
        os.remove(file_path)
        os.rename(output, file_path)
    # For APK (zip), simple recompress
    elif mime == 'application/zip' and file_path.endswith('.apk'):
        temp_dir = file_path + '_temp'
        os.makedirs(temp_dir)
        shutil.unpack_archive(file_path, temp_dir, 'zip')
        # Compress images inside
        for root, dirs, files in os.walk(temp_dir):
            for f in files:
                fp = os.path.join(root, f)
                if magic.Magic(mime=True).from_file(fp).startswith('image/'):
                    compress_file(fp)  # Recursive for images
        shutil.make_archive(file_path[:-4], 'zip', temp_dir)
        os.rename(file_path[:-4] + '.zip', file_path)
        shutil.rmtree(temp_dir)
    # Other types, skip

def archive_file(file_path):
    output = file_path + '.7z'
    subprocess.run(['7z', 'a', '-mx=9', output, file_path], check=True)
    os.remove(file_path)
    return output

def process_download(url, compress=False, archive=False):
    os.makedirs('files', exist_ok=True)
    if url.endswith('.txt'):
        txt_path = os.path.join('files', 'url.txt')
        if download_with_progress(url, txt_path):
            with open(txt_path, 'r') as f:
                lines = f.readlines()
            is_url_list = all(line.strip().startswith('http') for line in lines if line.strip())
            if not is_url_list:
                # Download normally
                new_path = rename_file(txt_path)
                if compress:
                    compress_file(new_path)
                if archive:
                    archive_file(new_path)
                return
            else:
                # Process each URL
                os.remove(txt_path)  # Don't keep url.txt
                for line in lines:
                    sub_url = line.strip()
                    if sub_url:
                        download_single(sub_url, compress, archive)
                return
    download_single(url, compress, archive)

def download_single(url, compress, archive):
    if is_torrent_related(url):
        # For torrents, aria2 downloads to dir, files will be there
        download_torrent_or_magnet(url, 'files')
        # Then process files in dir
        for f in os.listdir('files'):
            fp = os.path.join('files', f)
            if os.path.isfile(fp) and f not in ['download.py', 'url.txt']:
                new_fp = rename_file(fp)
                if compress:
                    compress_file(new_fp)
                if archive:
                    archive_file(new_fp)
    else:
        filename = get_true_filename(url)
        temp_path = os.path.join('files', filename)
        if download_with_progress(url, temp_path):
            new_path = rename_file(temp_path)
            if compress:
                compress_file(new_path)
            if archive:
                archive_file(new_path)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python download.py <url> [compress] [archive]")
        sys.exit(1)
    url = sys.argv[1]
    compress = sys.argv[2].lower() == 'true' if len(sys.argv) > 2 else False
    archive = sys.argv[3].lower() == 'true' if len(sys.argv) > 3 else False
    process_download(url, compress, archive)
