import sys
import os
import json
import requests
import time
from concurrent.futures import ThreadPoolExecutor
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFileDialog, QTextEdit, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

SCRYFALL_BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
MAX_RETRIES = 5  # Number of retries before giving up
BACKOFF_FACTOR = 2  # Exponential backoff multiplier
RATE_LIMIT_DELAY = 1  # Delay in seconds per request
MAX_THREADS = 10  # Maximum concurrent threads

class DownloadThread(QThread):
    progress = pyqtSignal(int)
    progress_text = pyqtSignal(str)
    log = pyqtSignal(str)
    finished_signal = pyqtSignal()
    
    def __init__(self, output_dir):
        super().__init__()
        self.output_dir = output_dir
        self.pause_flag = False
        self.cancel_flag = False
    
    def run(self):
        self.log.emit("Fetching bulk data URL from Scryfall...")
        bulk_data = self.make_request(SCRYFALL_BULK_DATA_URL)
        if not bulk_data:
            self.log.emit("Error: Could not fetch bulk data.")
            return
        
        json_url = next((entry['download_uri'] for entry in bulk_data['data'] if entry['type'] == "default_cards"), None)
        if not json_url:
            self.log.emit("Error: Could not find the correct bulk data URL.")
            return
        
        self.log.emit("Downloading bulk card data...")
        data = self.make_request(json_url)
        if not data:
            self.log.emit("Error: Failed to download card data.")
            return
        
        data_list = list(data)
        total_cards = len(data_list)
        if total_cards == 0:
            self.log.emit("No cards found in bulk data.")
            return
        
        os.makedirs(self.output_dir, exist_ok=True)
        completed = 0

        def download_card(card):
            nonlocal completed
            if self.cancel_flag:
                return  # Exit immediately if cancelled
            
            while self.pause_flag:
                self.log.emit("Download paused.")
                time.sleep(0.5)
            self.log.emit("Download resumed.")
            
            card_name = card.get("name", "unknown").replace("/", "-").replace(" ", "").lower()
            set_code = card.get("set", "unknown").upper()
            
            img_url = card.get("image_uris", {}).get("png")
            if not img_url:
                self.log.emit(f"Skipping {card_name} (no image URL found)")
                return
            
            set_folder = os.path.join(self.output_dir, set_code)
            os.makedirs(set_folder, exist_ok=True)
            image_path = os.path.join(set_folder, f"{card_name}.png")
            
            if os.path.exists(image_path):
                self.log.emit(f"Skipping {card_name}, already downloaded.")
                return
            
            try:
                image_response = self.make_request(img_url)
                if image_response:
                    with open(image_path, 'wb') as img_file:
                        img_file.write(image_response.content)
                    self.log.emit(f"Downloaded: {card_name}")
                else:
                    self.log.emit(f"Failed to download {card_name}")
            except requests.RequestException as e:
                self.log.emit(f"Error downloading {card_name}: {e}")
            
            completed += 1
            percentage = (completed / total_cards) * 100
            self.progress.emit(int(percentage))
            self.progress_text.emit(f"{percentage:.2f}%")
            time.sleep(RATE_LIMIT_DELAY)
        
        with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures = [executor.submit(download_card, card) for card in data_list]
            for future in futures:
                if self.cancel_flag:
                    break
                future.result()
        
        self.log.emit("Download complete.")
        self.finished_signal.emit()
    
    def make_request(self, url):
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                return response.json() if "json" in response.headers.get("Content-Type", "") else response
            except requests.RequestException as e:
                if attempt > 0:
                    self.log.emit(f"Retry {attempt + 1}/{MAX_RETRIES} for {url}: {e}")
                time.sleep(BACKOFF_FACTOR ** attempt)
        self.log.emit(f"Failed to fetch {url} after {MAX_RETRIES} retries.")
        return None

class MTGDownloader(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()
    
    def initUI(self):
        self.setWindowTitle("MTG Card Downloader")
        self.setGeometry(100, 100, 600, 400)
        layout = QVBoxLayout()
        
        button_layout = QHBoxLayout()
        self.output_button = QPushButton("Choose Folder")
        self.output_button.clicked.connect(self.select_output)
        self.output_label = QLabel("No folder selected")
        self.start_button = QPushButton("Start Download")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_download)
        button_layout.addWidget(self.output_button)
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.output_label)
        
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.pause_button.setEnabled(False)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("%p%")
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_download)
        
        layout.addLayout(button_layout)
        layout.addWidget(self.pause_button)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_output)
        layout.addWidget(self.cancel_button)
        
        self.setLayout(layout)
    
    def select_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_dir = folder
            self.output_label.setText(f"Output: {folder}")
            self.start_button.setEnabled(True)
    
    def start_download(self):
        self.thread = DownloadThread(self.output_dir)
        self.thread.progress.connect(self.progress_bar.setValue)
        self.thread.progress_text.connect(lambda val: self.progress_bar.setFormat(f"{val}"))
        self.thread.log.connect(self.log_output.append)
        self.thread.finished_signal.connect(self.on_download_complete)
        self.thread.start()
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
    
    def toggle_pause(self):
        self.thread.pause_flag = not self.thread.pause_flag
        self.pause_button.setText("Resume" if self.thread.pause_flag else "Pause")
    
    def cancel_download(self):
        self.thread.cancel_flag = True
    
    def on_download_complete(self):
        self.cancel_button.setText("OK")
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.close)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MTGDownloader()
    window.show()
    sys.exit(app.exec())
