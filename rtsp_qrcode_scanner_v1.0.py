#!/usr/bin/env python3
"""
RTSP Streamed QRCode Scanner V1 (RMCK - December 2025)
Single-file script with:
 - Borderless draggable window
 - Title bar
 - FFmpeg RTSP stream reader
 - OpenCV QR detection (no zbar)
 - CSV logging every 5s unless QR changes
 - View CSV button
 - Exit button
 - Auto-restart supervisor (stops restarting if Exit pressed)
"""

import os
import sys
import time
import threading
import traceback
import csv
import queue
from datetime import datetime
import subprocess

import ffmpeg
import numpy as np
import cv2
from PIL import Image, ImageTk
import tkinter as tk

# -----------------------------
# Configuration
# -----------------------------
RTSP_URL = "rtsp://192.168.1.207:8554/cam"
WIDTH = 1280
HEIGHT = 720
CSV_FILENAME = "captures.csv"
SAVE_COOLDOWN_SECONDS = 5.0
FFMPEG_BUFFER_SIZE = "1024000"
# -----------------------------

qr_detector = cv2.QRCodeDetector()
stop_supervisor = False  # global flag to stop supervisor restart

def ensure_csv_header(path):
    """Create CSV with header if missing."""
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp_iso", "qr_text"])


class CaptureSaver:
    """Debounced saving to CSV."""

    def __init__(self, filename, cooldown_seconds):
        self.filename = filename
        self.cooldown = cooldown_seconds
        self.last_saved_text = None
        self.last_saved_time = 0.0
        ensure_csv_header(self.filename)
        self.lock = threading.Lock()

    def maybe_save(self, qr_text):
        if not qr_text:
            return False

        now = time.time()

        with self.lock:
            should_save = False

            if self.last_saved_text is None:
                should_save = True
            elif qr_text != self.last_saved_text:
                should_save = True
            elif now - self.last_saved_time >= self.cooldown:
                should_save = True

            if should_save:
                iso = datetime.utcnow().isoformat() + "Z"
                with open(self.filename, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow([iso, qr_text])
                self.last_saved_text = qr_text
                self.last_saved_time = now
                return True

        return False


class FFmpegReader(threading.Thread):
    """Background RTSP reader."""

    def __init__(self, rtsp_url, width, height, frame_queue, stop_event):
        super().__init__(daemon=True)
        self.rtsp_url = rtsp_url
        self.width = width
        self.height = height
        self.frame_queue = frame_queue
        self.stop_event = stop_event
        self.process = None

    def run(self):
        frame_size = self.width * self.height * 3

        while not self.stop_event.is_set():
            try:
                self.process = (
                    ffmpeg.input(self.rtsp_url, rtsp_transport="tcp", buffer_size=FFMPEG_BUFFER_SIZE)
                    .output("pipe:", format="rawvideo", pix_fmt="bgr24",
                            vf=f"scale={self.width}:{self.height}")
                    .run_async(pipe_stdout=True, pipe_stderr=True)
                )

                stdout = self.process.stdout

                while not self.stop_event.is_set():
                    raw = stdout.read(frame_size)
                    if not raw or len(raw) < frame_size:
                        break

                    frame = np.frombuffer(raw, np.uint8).copy().reshape((self.height, self.width, 3))

                    if not self.frame_queue.empty():
                        try: self.frame_queue.get_nowait()
                        except queue.Empty: pass

                    self.frame_queue.put_nowait(frame)

                try:
                    if self.process:
                        self.process.kill()
                except:
                    pass

                if not self.stop_event.is_set():
                    time.sleep(1.0)

            except Exception as e:
                print("[FFmpegReader] ERROR:", e)
                traceback.print_exc()

                try:
                    if self.process:
                        self.process.kill()
                except:
                    pass

                time.sleep(2.0)

        try:
            if self.process:
                self.process.kill()
        except:
            pass


class QRApp:
    """Tkinter UI + QR detection logic."""

    def __init__(self, root, url, width, height):
        self.root = root
        self.rtsp_url = url
        self.width = width
        self.height = height

        global stop_supervisor
        stop_supervisor = False  # Reset flag on new run

        # ---------------- UI Setup ----------------
        root.overrideredirect(True)
        root.configure(bg="black")

        # Window movement
        root.bind("<ButtonPress-1>", self.start_move)
        root.bind("<B1-Motion>", self.do_move)
        self.drag = {"x": 0, "y": 0}

        # -------- Title Bar --------
        self.title_bar = tk.Frame(root, bg="black")
        self.title_bar.pack(fill=tk.X)

        self.title_label = tk.Label(
            self.title_bar,
            text="RTSP Streamed QRCode Scanner V1 (RMCK - December 2025)",
            bg="black", fg="white",
            font=("Helvetica", 14, "bold"),
            padx=10, pady=5
        )
        self.title_label.pack()

        self.title_bar.bind("<ButtonPress-1>", self.start_move)
        self.title_bar.bind("<B1-Motion>", self.do_move)

        # Video canvas
        self.canvas = tk.Canvas(root, width=self.width, height=self.height, highlightthickness=0)
        self.canvas.pack()

        # -------- Bottom Bar --------
        bottom = tk.Frame(root, bg="black")
        bottom.pack(fill=tk.X)

        self.qr_label = tk.Label(
            bottom, text="Detected QR Code: ", bg="black",
            fg="lime", font=("Helvetica", 14)
        )
        self.qr_label.pack(side=tk.LEFT, padx=10, pady=5)

        # View CSV button
        self.csv_btn = tk.Button(
            bottom, text="View CSV", bg="white", fg="black",
            command=self.open_csv, padx=10
        )
        self.csv_btn.pack(side=tk.RIGHT, padx=5)

        # Exit button
        self.exit_btn = tk.Button(
            bottom, text="Exit", bg="white", fg="black",
            command=self.quit_app, padx=10
        )
        self.exit_btn.pack(side=tk.RIGHT, padx=5)

        # Shortcuts
        root.bind("<Escape>", lambda e: self.quit_app())
        root.bind("q", lambda e: self.quit_app())

        # FFmpeg reader
        self.frame_queue = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.reader = FFmpegReader(url, width, height, self.frame_queue, self.stop_event)

        self.saver = CaptureSaver(CSV_FILENAME, SAVE_COOLDOWN_SECONDS)

        self.current_qr = ""
        self.tk_img = None

    # -------- Window Movement ----------
    def start_move(self, e): self.drag["x"], self.drag["y"] = e.x, e.y
    def do_move(self, e):
        x = self.root.winfo_x() + e.x - self.drag["x"]
        y = self.root.winfo_y() + e.y - self.drag["y"]
        self.root.geometry(f"+{x}+{y}")

    # -------- CSV Viewer ----------
    def open_csv(self):
        path = os.path.abspath(CSV_FILENAME)
        if sys.platform.startswith("linux"):
            subprocess.call(["xdg-open", path])
        elif sys.platform == "darwin":
            subprocess.call(["open", path])
        elif sys.platform.startswith("win"):
            os.startfile(path)

    # -------- Shutdown ----------
    def quit_app(self):
        global stop_supervisor
        stop_supervisor = True  # Prevent supervisor restart
        self.stop_event.set()
        try:
            if self.reader.is_alive():
                self.reader.join(timeout=1.0)
        except:
            pass
        self.root.quit()
        self.root.destroy()

    def start(self):
        self.reader.start()
        self.update_loop()

    # -------- UI Loop ----------
    def update_loop(self):
        try:
            frame = None
            try:
                frame = self.frame_queue.get_nowait()
            except queue.Empty:
                pass

            if frame is not None:
                # --- QR DETECTION ---
                data, points, _ = qr_detector.detectAndDecode(frame)

                if points is not None and data:
                    qr_text = data.strip()

                    pts = points.astype(int).reshape((-1, 2))
                    for i in range(4):
                        p1 = tuple(pts[i])
                        p2 = tuple(pts[(i + 1) % 4])
                        cv2.line(frame, p1, p2, (0, 255, 0), 3)

                    saved = self.saver.maybe_save(qr_text)
                    if saved:
                        print("[Saved]", qr_text)

                    self.current_qr = qr_text

                # --- Convert to Tk image ---
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil = Image.fromarray(rgb)
                self.tk_img = ImageTk.PhotoImage(pil)

                self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)

                label = f"Detected QR Code: {self.current_qr}" if self.current_qr else "Detected QR Code: "
                self.qr_label.config(text=label)

        except Exception as e:
            print("[UI ERROR]", e)
            traceback.print_exc()

        self.root.after(30, self.update_loop)


# -------- Supervisor loop --------
def supervised_run():
    global stop_supervisor
    while not stop_supervisor:
        try:
            root = tk.Tk()
            app = QRApp(root, RTSP_URL, WIDTH, HEIGHT)
            app.start()
            root.mainloop()

            if stop_supervisor:
                print("[Supervisor] Exit requested by user, stopping restart.")
                break

            print("[Supervisor] Restarting in 2 seconds...")
            time.sleep(2)

        except Exception as e:
            print("[Supervisor ERROR]", e)
            traceback.print_exc()
            time.sleep(2)
            continue


if __name__ == "__main__":
    supervised_run()
