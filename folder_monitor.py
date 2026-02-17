"""
OneDrive folder monitoring module.
Watches for new PDF files and triggers renaming.
"""
import os
import time
from pathlib import Path
from typing import Callable, Optional
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

class PDFHandler(FileSystemEventHandler):
    """Handles PDF file system events."""
    
    def __init__(self, callback: Callable[[str], None], debounce_seconds: float = 2.0):
        """
        Initialize PDF handler.
        
        Args:
            callback: Function to call when a new PDF is detected
            debounce_seconds: Seconds to wait before processing (to ensure file is fully written)
        """
        super().__init__()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.pending_files = {}
    
    def on_created(self, event: FileSystemEvent):
        """Handle file creation event."""
        if event.is_directory:
            return
        
        file_path = event.src_path
        if file_path.lower().endswith('.pdf'):
            print(f"Detected new PDF: {file_path}")
            # Store the file with timestamp for debouncing
            self.pending_files[file_path] = time.time()
    
    def on_modified(self, event: FileSystemEvent):
        """Handle file modification event."""
        if event.is_directory:
            return
        
        file_path = event.src_path
        if file_path.lower().endswith('.pdf'):
            # Update timestamp if file is still being written
            if file_path in self.pending_files:
                self.pending_files[file_path] = time.time()
    
    def process_pending_files(self):
        """Process files that have finished being written."""
        current_time = time.time()
        files_to_process = []
        
        for file_path, timestamp in list(self.pending_files.items()):
            # Check if enough time has passed since last modification
            if current_time - timestamp >= self.debounce_seconds:
                # Verify file still exists and is accessible
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    files_to_process.append(file_path)
                del self.pending_files[file_path]
        
        # Process each file
        for file_path in files_to_process:
            try:
                self.callback(file_path)
            except Exception as e:
                print(f"Error processing {file_path}: {e}")


class OneDriveFolderMonitor:
    """Monitors a OneDrive folder for new PDF files."""
    
    def __init__(self, folder_path: str, callback: Callable[[str], None]):
        """
        Initialize folder monitor.
        
        Args:
            folder_path: Path to the OneDrive folder to monitor
            callback: Function to call when a new PDF is detected
        """
        self.folder_path = Path(folder_path).resolve()
        if not self.folder_path.exists():
            raise ValueError(f"Folder does not exist: {folder_path}")
        if not self.folder_path.is_dir():
            raise ValueError(f"Path is not a directory: {folder_path}")
        
        self.callback = callback
        self.event_handler = PDFHandler(callback)
        self.observer = Observer()
    
    def start(self):
        """Start monitoring the folder."""
        print(f"Starting to monitor folder: {self.folder_path}")
        recursive = os.getenv("MONITOR_RECURSIVE", "false").lower() == "true"
        self.observer.schedule(self.event_handler, str(self.folder_path), recursive=recursive)
        self.observer.start()
        
        print("Monitoring started. Press Ctrl+C to stop.")
        
        try:
            while True:
                time.sleep(1)
                # Process any pending files
                self.event_handler.process_pending_files()
        except KeyboardInterrupt:
            self.stop()
    
    def stop(self):
        """Stop monitoring the folder."""
        print("\nStopping monitor...")
        self.observer.stop()
        self.observer.join()
        print("Monitor stopped.")
    
    def scan_existing_files(self):
        """Scan for existing PDF files in the folder (for initial run)."""
        print(f"Scanning for existing PDF files in {self.folder_path}...")
        pdf_files = list(self.folder_path.glob("*.pdf"))
        
        if pdf_files:
            print(f"Found {len(pdf_files)} existing PDF file(s)")
            for pdf_file in pdf_files:
                print(f"  - {pdf_file.name}")
            
            response = input("Would you like to process existing files? (y/n): ").strip().lower()
            if response == 'y':
                for pdf_file in pdf_files:
                    try:
                        self.callback(str(pdf_file))
                    except Exception as e:
                        print(f"Error processing {pdf_file}: {e}")
        else:
            print("No existing PDF files found.")
