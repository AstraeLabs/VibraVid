# 26.10.26

from VibraVid.core.manifest.mpd import DashParser

MPD_QUERY = "channel=6956&outlet=dazn-italy&plang=en&dazn-token=st%3D1~acl%3D%2F*~hmac%3Dabc"


def _suffix(query: str = MPD_QUERY) -> str:
    return DashParser._compute_query_suffix(query)


def test_segment_without_query_gets_the_manifest_suffix():
    assert DashParser._inherit_query("https://h/dash/s-1.dash", _suffix()) == f"https://h/dash/s-1.dash?{_suffix()}"


def test_segment_with_own_query_keeps_it_and_gains_the_manifest_token():
    """DAZN: the SegmentTemplate has '?vod_type=..&outlet=..' and the token lives only on the MPD URL."""
    url = DashParser._inherit_query("https://h/dash/s-1.dash?vod_type=vod&outlet=dazn-italy", _suffix())
    assert url == "https://h/dash/s-1.dash?vod_type=vod&outlet=dazn-italy&dazn-token=st%3D1~acl%3D%2F*~hmac%3Dabc"


def test_segment_with_own_query_does_not_gain_non_auth_manifest_params():
    url = DashParser._inherit_query("https://h/s.m4s?a=1", _suffix("channel=1&plang=en"))
    assert url == "https://h/s.m4s?a=1"


def test_segment_token_is_never_duplicated_or_overridden():
    url = "https://h/s.m4s?a=1&dazn-token=own"
    assert DashParser._inherit_query(url, _suffix()) == url


def test_other_cdn_auth_schemes_are_kept_on_segments_with_own_query():
    for query in (
        "hdnts=exp%3D1~hmac%3Dx",
        "Policy=p&Signature=s&Key-Pair-Id=k",
        "Expires=1&Signature=s&Key-Pair-Id=k&Hash-Algorithm=SHA256",
        "X-Amz-Algorithm=A&X-Amz-Credential=c&X-Amz-Signature=s",
        "sig=abc&se=2026",
        "wmsAuthSign=abc",
        "md5=m&expires=1",
        "authparams=abc",
    ):
        url = DashParser._inherit_query("https://h/s.m4s?a=1", _suffix(f"channel=1&{query}"))
        assert url == f"https://h/s.m4s?a=1&{query.split('&se=')[0]}"
