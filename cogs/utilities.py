import aiohttp
import re


heard_here_re = re.compile(r'\bheard here[\:]*$', re.IGNORECASE)
audio_re = re.compile(r'\[audio', re.IGNORECASE)
seen_here_re = re.compile(r'\bseen here\b', re.IGNORECASE)
# jservice = "http://localhost:3000/"
jservice = "http://jservice.io/"


async def jservice_get_json(session, path, params={}):
    print(path, params)
    async with session.get(jservice + path, params=params) as r:
        if r.status == 200:
            js = await r.json()
            return js
        else:
            return None


def is_audio_clue(clue):
    return (heard_here_re.search(clue['question']) or
            audio_re.match(clue['question']) or
            clue['question'].endswith(":"))


def is_video_clue(clue):
    return seen_here_re.search(clue['question'])


def is_valid_clue(clue, allow_audio=False, allow_video=False):
    return (not clue['invalid_count'] and clue['question'] and clue['answer'] and 
            clue['question'] != '=' and
            (allow_audio or not is_audio_clue(clue) and
            (allow_video or not is_video_clue(clue))))
