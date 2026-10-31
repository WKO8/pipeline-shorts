from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.range_downloader import SelectiveRangeDownloader


def test_build_ydl_opts(tmp_path: Path):
    downloader = SelectiveRangeDownloader(
        cookies_file="/path/to/cookies.txt",
        cookies_from_browser="chrome",
    )
    out_path = tmp_path / "test_cut.mp4"
    opts = downloader._build_ydl_opts(start_sec=100.0, end_sec=250.0, out_path=out_path)

    assert opts["force_keyframes_at_cuts"] is False
    assert opts["outtmpl"] == str(out_path)
    assert opts["js_runtimes"] == {"node": {}}
    assert "download_ranges" in opts
    assert callable(opts["download_ranges"])
    assert opts["cookiefile"] == "/path/to/cookies.txt"
    assert opts["cookiesfrombrowser"] == ("chrome",)
    assert opts["nocheckcertificate"] is True
    assert "ffmpeg_i" in opts["external_downloader_args"]


def test_download_single_cut_retry(tmp_path: Path):
    downloader = SelectiveRangeDownloader()
    out_path = tmp_path / "test_retry.mp4"

    call_count = 0

    def mock_download_func(urls):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("ffmpeg exited with code 8")
        if call_count == 2:
            raise RuntimeError("Server returned 403 Forbidden")
        # 3rd call succeeds
        out_path.write_bytes(b"downloaded mp4" * 100)
        return 0

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.download.side_effect = mock_download_func
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

        result_path = downloader.download_single_cut(
            url="https://youtube.com/watch?v=dummy",
            start_sec=10.0,
            end_sec=20.0,
            out_path=out_path,
            stagger_delay=0.0,
            max_retries=3,
        )

        assert result_path.exists()
        assert call_count == 3


def test_download_cuts_parallel(tmp_path: Path):
    downloader = SelectiveRangeDownloader()

    cuts = [
        {"cut_id": 1, "start_second": 10.0, "end_second": 50.0},
        {"cut_id": 2, "start_second": 120.0, "end_second": 200.0},
    ]

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.download.return_value = 0
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

        # Create dummy target files so exists() is True
        for c in cuts:
            p = tmp_path / f"test_corte_{c['cut_id']}_{int(c['start_second'])}s_{int(c['end_second'])}s.mp4"
            p.write_bytes(b"dummy mp4 content" * 100)

        updated_cuts = downloader.download_cuts_parallel(
            url="https://youtube.com/watch?v=dummy",
            cuts=cuts,
            output_dir=tmp_path,
            video_slug="test",
            max_workers=2,
            stagger_seconds=0.01,
        )

        assert len(updated_cuts) == 2
        assert "video_file" in updated_cuts[0]
        assert Path(updated_cuts[0]["video_file"]).exists()
        assert "video_file" in updated_cuts[1]
        assert Path(updated_cuts[1]["video_file"]).exists()


def test_download_cuts_stream(tmp_path: Path):
    downloader = SelectiveRangeDownloader()

    cuts = [
        {"cut_id": 1, "start_second": 10.0, "end_second": 50.0},
        {"cut_id": 2, "start_second": 120.0, "end_second": 200.0},
    ]

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.download.return_value = 0
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

        for c in cuts:
            p = tmp_path / f"stream_corte_{c['cut_id']}_{int(c['start_second'])}s_{int(c['end_second'])}s.mp4"
            p.write_bytes(b"dummy mp4 content" * 100)

        stream = downloader.download_cuts_stream(
            url="https://youtube.com/watch?v=dummy",
            cuts=cuts,
            output_dir=tmp_path,
            video_slug="stream",
            max_workers=2,
            stagger_seconds=0.01,
        )

        streamed_cuts = []
        for cut in stream:
            streamed_cuts.append(cut)
            assert "video_file" in cut
            assert Path(cut["video_file"]).exists()

        assert len(streamed_cuts) == 2

