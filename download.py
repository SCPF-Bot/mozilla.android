import sys
import os
import random
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import subprocess
import shutil
from PIL import Image
import zipfile
import re
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException

# Get inputs
main_url = sys.argv[1]
do_compress = sys.argv[2].lower() == 'true'
do_archive = sys.argv[3].lower() == 'true'

# Create files dir
os.makedirs("files", exist_ok=True)
os.chdir("files")

# User agent
ua = UserAgent()
user_agent = ua.random

# Function to get filename from URL or headers
def get_filename(link):
    try:
        head = requests.head(link, allow_redirects=True, headers={'User-Agent': user_agent})
        if 'Content-Disposition' in head.headers:
            cd = head.headers['Content-Disposition']
            if 'filename=' in cd:
                return cd.split('filename=')[1].strip('"')
        return os.path.basename(link.split('?')[0]) or 'downloaded_file'
    except:
        return 'downloaded_file'

# Function to download with aria2 (progress bar via aria2 output)
def aria_download(link, out=None):
    cmd = ['aria2c', '--user-agent', user_agent, '--dir=.', '--summary-interval=1', '--auto-file-renaming=false']
    if out:
        cmd += ['--out', out]
    cmd += [link]
    subprocess.run(cmd, check=True)

# Function to scrape magnet from page (with Selenium fallback for JS)
def scrape_magnet(page_url):
    headers = {
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.google.com/',
    }
    # First try with requests
    for attempt in range(3):
        try:
            resp = requests.get(page_url, headers=headers, timeout=10, allow_redirects=True)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, 'html.parser')
            # Find anchors with magnet href
            magnets = [a['href'] for a in soup.find_all('a', href=True) if a['href'].startswith('magnet:')]
            if magnets:
                return magnets[0]
            # Fallback: search for common classes or buttons
            for selector in ['a[href^="magnet:"]', 'a.btn-magnet', 'a.magnet-link', 'a[title*="Magnet"]']:
                found = soup.select(selector)
                if found and found[0].get('href'):
                    return found[0]['href']
            # Fallback regex on raw text
            magnet_pattern = re.compile(r'(magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^\s\'"]*)', re.IGNORECASE)
            found = magnet_pattern.findall(resp.text)
            if found:
                return found[0]
        except Exception as e:
            print(f"Requests scrape attempt {attempt+1} failed: {e}")
            time.sleep(2)

    # Fallback to Selenium for JS-rendered content
    try:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-infobars")
        options.add_argument("--remote-debugging-port=9222")
        options.add_argument(f"user-agent={user_agent}")
        service = Service(executable_path="chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        driver.get(page_url)
        time.sleep(5)  # Wait for JS to load

        # Find magnet links
        try:
            magnet_elements = driver.find_elements(By.CSS_SELECTOR, 'a[href^="magnet:"]')
            if magnet_elements:
                return magnet_elements[0].get_attribute('href')
        except NoSuchElementException:
            pass

        # Fallback selectors
        for selector in ['a.btn-magnet', 'a.magnet-link', 'a[title*="Magnet"]', 'button[data-magnet]', 'a[href*="magnet"]']:
            try:
                elem = driver.find_element(By.CSS_SELECTOR, selector)
                href = elem.get_attribute('href')
                if href and href.startswith('magnet:'):
                    return href
            except NoSuchElementException:
                pass

        # Regex on page source
        page_source = driver.page_source
        magnet_pattern = re.compile(r'(magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^\s\'"]*)', re.IGNORECASE)
        found = magnet_pattern.findall(page_source)
        if found:
            return found[0]

        driver.quit()
    except (TimeoutException, WebDriverException) as e:
        print(f"Selenium scrape failed: {e}")
        if 'driver' in locals():
            driver.quit()
    return None

# Function to handle a single link
def handle_link(link):
    if link.startswith('magnet:'):
        aria_download(link)
        return

    ext = os.path.splitext(link)[1].lower()
    if ext == '.torrent':
        torrent_file = get_filename(link) or 'temp.torrent'
        aria_download(link, torrent_file)
        aria_download(torrent_file)
        os.remove(torrent_file)
        return

    try:
        head = requests.head(link, allow_redirects=True, headers={'User-Agent': user_agent})
        ct = head.headers.get('Content-Type', '')
    except:
        ct = ''

    if 'text/html' in ct:
        magnet = scrape_magnet(link)
        if magnet:
            aria_download(magnet)
            return
    # Fallback to direct download
    fn = get_filename(link)
    aria_download(link, fn)

# Function to flatten any subdirectories (e.g., from multi-file torrents)
def flatten():
    for item in list(os.listdir('.')):
        if os.path.isdir(item):
            for sub in os.listdir(item):
                src = os.path.join(item, sub)
                dst = sub
                i = 1
                while os.path.exists(dst):
                    name, ext = os.path.splitext(sub)
                    dst = f"{name}_{i}{ext}"
                    i += 1
                shutil.move(src, dst)
            os.rmdir(item)

# Collect downloaded files
downloaded_files = []

# Function to handle and collect new files
def handle_and_collect(link):
    before = set(os.listdir('.'))
    handle_link(link)
    flatten()
    after = set(os.listdir('.'))
    new_files = list(after - before)
    downloaded_files.extend(new_files)

# Process main URL
is_txt_list = False
if main_url.lower().endswith('.txt'):
    temp_fn = 'temp.txt'
    aria_download(main_url, temp_fn)
    with open(temp_fn, 'r') as f:
        lines = [l.strip() for l in f if l.strip()]
    if all(l.startswith(('http://', 'https://', 'magnet:')) for l in lines):
        is_txt_list = True
        for l in lines:
            handle_and_collect(l)
        os.remove(temp_fn)
    else:
        fn = get_filename(main_url)
        os.rename(temp_fn, fn)
        downloaded_files.append(fn)
else:
    handle_and_collect(main_url)

# Rename files: replace spaces with ., add 4-digit random prefix
updated_files = []
for f in downloaded_files:
    base = f.replace(' ', '.')
    prefix = f"{random.randint(1000, 9999)}."
    new_name = prefix + base
    os.rename(f, new_name)
    updated_files.append(new_name)

# Compress if enabled
if do_compress:
    for i, file in enumerate(updated_files):
        ext = os.path.splitext(file)[1].lower()
        if ext in ['.mp4', '.mkv', '.avi']:
            # Compress video to 480p
            temp_fn = file + '_comp.mp4'
            subprocess.run(['ffmpeg', '-i', file, '-vf', 'scale=-2:480', '-c:v', 'libx264', '-crf', '23', '-preset', 'medium', '-c:a', 'aac', '-strict', 'experimental', temp_fn], check=True)
            os.remove(file)
            os.rename(temp_fn, file)
        elif ext in ['.jpg', '.jpeg', '.png']:
            # Reduce image size
            try:
                img = Image.open(file)
                img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
                img.save(file, quality=70 if ext in ['.jpg', '.jpeg'] else 75, optimize=True)
            except:
                pass
        elif ext == '.apk':
            # Compress images in APK
            temp_dir = 'temp_apk'
            os.makedirs(temp_dir, exist_ok=True)
            with zipfile.ZipFile(file, 'r') as z:
                z.extractall(temp_dir)
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                        path = os.path.join(root, f)
                        try:
                            img = Image.open(path)
                            img.save(path, quality=70 if '.jpg' in f.lower() or '.jpeg' in f.lower() else 75, optimize=True)
                        except:
                            pass
            base_name = file[:-4]
            shutil.make_archive(base_name, 'zip', temp_dir)
            os.remove(file)
            os.rename(base_name + '.zip', file)
            shutil.rmtree(temp_dir)

# Archive if enabled
if do_archive:
    archived_files = []
    for file in updated_files:
        archive_fn = file + '.7z'
        subprocess.run(['7z', 'a', '-mx=9', archive_fn, file], check=True)
        os.remove(file)
        archived_files.append(archive_fn)
    updated_files = archived_files

# Create release notes
repo = os.environ['GITHUB_REPOSITORY']
with open('../notes.md', 'w') as f:
    f.write('# Latest Downloads\n\nDownloaded files:\n')
    for file in updated_files:
        f.write(f"- [{file}](https://github.com/{repo}/releases/download/latest/{file})\n")
