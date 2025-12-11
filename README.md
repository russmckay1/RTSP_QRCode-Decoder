Raspberry Pi PiZeroCam
======================

RTSP application installed onto Pi with:-
sudo apt update
sudo apt install snapd
sudo snap install mediamtx

RTSP application started with:-
rpicam-vid --vflip --hflip -t 0 --inline --flush --codec h264 -o - \
| ffmpeg -re -i - -c copy -f rtsp rtsp://localhost:8554/cam

URL used to display video in VLC:-
Rtsp://192.168.1.207:8554/cam

Starting the Decoder
python rtsp_qrcode_scanner_v1.0.py

The main Python code runs on a MAC and should run on other Linux based operarting systems
