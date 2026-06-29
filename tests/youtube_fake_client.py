"""Фейк YouTubeClient для sync-тестов: отдаёт заранее заданные ответы, считает вызовы."""
from __future__ import annotations


class FakeYouTubeClient:
    def __init__(self, *, channel, videos=None, channel_report=None, video_reports=None,
                 quota_on=None):
        self._channel = channel
        self._videos = videos or []
        self._channel_report = channel_report or {"columnHeaders": [], "rows": []}
        self._video_reports = video_reports or {}     # video_id -> report
        self._quota_on = quota_on or set()            # сегменты, бросающие quota
        self.calls = []

    def channel(self, channel_id):
        self.calls.append(("channel", channel_id))
        return self._channel

    def iter_playlist_items(self, playlist_id):
        self.calls.append(("playlist", playlist_id))
        yield from [v["id"] for v in self._videos]

    def videos(self, ids):
        self.calls.append(("videos", tuple(ids)))
        from kontur.connectors.youtube.client import YouTubeQuotaExceeded
        if "videos" in self._quota_on:
            raise YouTubeQuotaExceeded(403, "quotaExceeded", "out")
        vmap = {v["id"]: v for v in self._videos}
        return [vmap[i] for i in ids if i in vmap]

    def report(self, *, start_date, end_date, metrics, dimensions=None, filters=None,
               sort=None, ids="channel==MINE"):
        self.calls.append(("report", filters))
        from kontur.connectors.youtube.client import YouTubeQuotaExceeded
        if filters and "video==" in filters:
            if "video_report" in self._quota_on:
                raise YouTubeQuotaExceeded(403, "quotaExceeded", "out")
            vid = filters.split("video==", 1)[1]
            return self._video_reports.get(vid, {"columnHeaders": [], "rows": []})
        if "channel_report" in self._quota_on:
            raise YouTubeQuotaExceeded(403, "quotaExceeded", "out")
        return self._channel_report

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass
