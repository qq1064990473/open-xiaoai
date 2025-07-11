import aiohttp
import asyncio
import json
import time
from typing import Optional
from aiofiles import open as aioopen



# 用于避免重复播放
played_mids = set()

# 播放记录保存文件
playlist_file = "playlist.txt"

def build_search_body(query, search_type=0, page_num=1):
    return {
        "music.search.SearchCgiService": {
            "method": "DoSearchForQQMusicDesktop",
            "module": "music.search.SearchCgiService",
            "param": {
                "num_per_page": 20,
                "page_num": page_num,
                "query": query,
                "search_type": search_type
            }
        }
    }

async def search_song(session: aiohttp.ClientSession, query: str) -> Optional[dict]:
    body = build_search_body(query)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json"
    }
    async with session.post("https://u.y.qq.com/cgi-bin/musicu.fcg", json=body, headers=headers) as resp:
        text = await resp.text()
        data = json.loads(text)
        try:
            return data["music.search.SearchCgiService"]["data"]["body"]["song"]["list"][0]
        except Exception:
            return None

async def get_singer_songs(session: aiohttp.ClientSession, singer_name: str, page_num: int = 1) -> list:
    body = build_search_body(singer_name, search_type=0, page_num=page_num)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json"
    }
    async with session.post("https://u.y.qq.com/cgi-bin/musicu.fcg", json=body, headers=headers) as resp:
        text = await resp.text()
        data = json.loads(text)
        try:
            return data["music.search.SearchCgiService"]["data"]["body"]["song"]["list"]
        except Exception:
            return []

async def get_play_url(session: aiohttp.ClientSession, mid: str) -> Optional[str]:
    url = f"https://musicapi.haitangw.net/music/qq_song_kw.php?id={mid}"
    async with session.get(url) as resp:
        try:
            data = await resp.json()
            return data.get("data", {}).get("url")
        except Exception:
            return None

async def play_song_with_status_check(session, song: dict, speaker) -> bool:
    title = song["title"]
    artist = song["singer"][0]["name"]
    mid = song["mid"]
    interval = song.get("interval", 180)  # 秒，默认180秒

    url = await get_play_url(session, mid)
    if not url:
        print(f"\n❌ 播放失败: {title} - {artist}")
        return False

    print(f"\n🎵 正在播放: {title} - {artist}\n▶️ 播放链接: {url}")
    await speaker.play(url=url, blocking=False)

    # 记录播放
    played_mids.add(mid)
    async with aioopen(playlist_file, "a") as f:
        await f.write(f"{mid} # {title} - {artist}\n")

    # 初始化变量
    speaker.received_pause = False  # 清空打断记录
    start_time = time.time()

    while True:
        status = await speaker.get_playing(sync=True)
        elapsed = time.time() - start_time
        last_directive = getattr(speaker, "last_directive_name", None)

        if status != "idle":
            await asyncio.sleep(1)
            continue
        await asyncio.sleep(0.5)
        final_status = await speaker.get_playing(sync=True)
        final_directive = getattr(speaker, "last_directive_name", None)
        final_elapsed = time.time() - start_time
        if last_directive == "Finish":
            if (
                final_status == "idle"
                and final_directive == "Finish"
                and final_elapsed >= interval
            ):
                print(f"✅ 歌曲播放完成: {title} - {artist}")
                speaker.received_pause = False
                break
            else:
                print("⏸️ 播放被打断（Finish + idle）")
                speaker.received_pause = False
                played_mids.clear()
                return False
        else:
            print(f"⚠️ 状态 idle + 指令 {last_directive}，等待 Finish 中...")
            # 等待 Finish 指令到达
            while True:
                directive = getattr(speaker, "last_directive_name", None)
                print(f"[监听] 当前指令: {directive}")
                if directive == "Finish":
                    break
                await asyncio.sleep(0.5)
        if speaker.received_pause:
            print("⏸️ 播放被打断（Pause + Finish）")
            speaker.received_pause = False
            played_mids.clear()
            return False
        else:
            print("🔁 假打断，自动恢复播放")
            await speaker.play(url=url, blocking=False)
            start_time = time.time()
            speaker.received_pause = False
            continue

    return True

async def play_singer_playlist_with_status_check(singer_name: str, first_mid: str, speaker):
    page_num = 1

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                song_list = await get_singer_songs(session, singer_name, page_num)
                if not song_list:
                    print("📭 没有更多歌曲了。")
                    break

                for song in song_list:
                    mid = song["mid"]
                    if mid not in played_mids and song["singer"][0]["name"] == singer_name:
                        success = await play_song_with_status_check(session, song, speaker)
                        if not success:
                            print("⚠️ 播放异常或被打断，停止播放同歌手后续歌曲")
                            return  # 这里直接停止播放后续歌曲
                page_num += 1
            except Exception as e:
                print(f"⚠️ 获取或播放歌曲异常: {e}")
                break

async def start_play_with_status_check(query: str, speaker):
    async with aiohttp.ClientSession() as session:
        first_song = await search_song(session, query)
        if not first_song:
            print("❌ 未找到歌曲")
            return

        singer_name = first_song["singer"][0]["name"]
        first_mid = first_song["mid"]

        success = await play_song_with_status_check(session, first_song, speaker)
        if not success:
            print("❌ 播放失败或被打断")
            return

        # ✅ 继续播放同歌手其他歌曲
        await play_singer_playlist_with_status_check(singer_name, first_mid, speaker)
