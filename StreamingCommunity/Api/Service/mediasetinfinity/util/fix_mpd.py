# 16.03.25

from urllib.parse import urlparse, urlunparse


# Internal utilities
from StreamingCommunity.Util.http_client import create_client


def try_mpd(url, qualities):
    """
    Given a url containing one of the qualities (hd/hr/sd), try to replace it with the others and check which manifest exists.
    """
    parsed = urlparse(url)
    path_parts = parsed.path.rsplit('/', 1)

    if len(path_parts) != 2:
        return None
    
    dir_path, filename = path_parts

    # Find the current quality in the filename
    def replace_quality(filename, old_q, new_q):
        if f"{old_q}_" in filename:
            return filename.replace(f"{old_q}_", f"{new_q}_", 1)
        elif filename.startswith(f"{old_q}_"):
            return f"{new_q}_" + filename[len(f"{old_q}_") :]
        return filename

    for q in qualities:

        # Search for which quality is present in the filename
        for old_q in qualities:
            if f"{old_q}_" in filename or filename.startswith(f"{old_q}_"):
                new_filename = replace_quality(filename, old_q, q)
                break

        else:
            new_filename = filename  # No quality found, use original filename

        new_path = f"{dir_path}/{new_filename}"
        mpd_url = urlunparse(parsed._replace(path=new_path)).strip()

        try:
            r = create_client().head(mpd_url)
            if r.status_code == 200:
                return mpd_url
            
        except Exception:
            pass

    return None

def get_manifest(base):
    """
    Try to get the manifest URL by checking different qualities.
    """
    manifest_qualities = ["hd", "hr", "sd"]

    mpd_url = try_mpd(base, manifest_qualities)
    if not mpd_url:
        exit(1)

    return mpd_url