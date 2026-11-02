import json
import subprocess
from pathlib import Path
PROJECT = Path('/root/tool-phim')
SOURCE = PROJECT / 'data/channels/98240066/videos.jsonl'
COOKIES = PROJECT / 'secrets/bilibili/cookies_bilibili.txt'
YTDLP = PROJECT / '.venv/bin/yt-dlp'
def main():
    if not SOURCE.is_file():
        print('ERROR: Không tìm thấy danh sách video')
        return
    if not COOKIES.is_file():
        print('ERROR: Không tìm thấy cookie Bilibili')
        return
    with SOURCE.open(encoding='utf-8') as file:
        first = json.loads(next(file))
    bvid = first['id']
    url = f'https://www.bilibili.com/video/{bvid}'
    print(f'===== KIỂM TRA VIDEO {bvid} =====')
    command = [str(YTDLP), '--cookies', str(COOKIES), '--no-playlist', '--skip-download', '--dump-single-json', url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        print('ERROR: Quá thời gian kiểm tra')
        return
    if result.returncode != 0:
        print('ERROR: yt-dlp không lấy được metadata')
        print(result.stderr[-1500:])
        return
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print('ERROR: Metadata không phải JSON hợp lệ')
        return
    duration = data.get('duration')
    duration_text = (f'{int(duration) // 60} phút {int(duration) % 60} giây' if isinstance(duration, (int, float)) else 'Không xác định')
    print(json.dumps({'bvid': bvid, 'title': data.get('title'), 'duration': duration_text, 'uploader': data.get('uploader'), 'availability': data.get('availability'), 'format_count': len(data.get('formats') or []), 'status': 'UNKNOWN - chưa kiểm tra quyền Power'}, ensure_ascii=False, indent=2))
if __name__ == '__main__':
    main()
