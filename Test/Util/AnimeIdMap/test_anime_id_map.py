# 19.08.26
# ruff: noqa: E402

import sys
from pathlib import Path


workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))


from VibraVid.utils.media import anime_id_map

_results = {"pass": 0, "fail": 0}


def check(name: str, got, expected) -> None:
    ok = got == expected
    _results["pass" if ok else "fail"] += 1
    status = "[PASS]" if ok else "[FAIL]"
    print(f"{status} {name}")
    if not ok:
        print(f"        expected: {expected!r}")
        print(f"        got:      {got!r}")


_FIXTURE_ENTRIES = [
    {
        "type": "TV",
        "anidb_id": 70,
        "anilist_id": 306,
        "mal_id": 306,
        "imdb_id": ["tt0398412"],
        "themoviedb_id": {"tv": 12144},
        "tvdb_id": 73616,
        "season": {"tvdb": 1, "tmdb": 1},
    },
    {
        "type": "TV",
        "anidb_id": 14444,
        "anilist_id": 104578,
        "mal_id": 38524,
        "imdb_id": ["tt2560140"],
        "themoviedb_id": {"tv": 1429},
        "tvdb_id": 267440,
        "season": {"tvdb": 3, "tmdb": 3},
        "episode_offset": {"tvdb": 12, "tmdb": 12},
    },
    {
        "type": "Movie",
        "anilist_id": 99999,
        "mal_id": 99999,
        "themoviedb_id": {"movie": 555},
    },
    {
        "type": "TV",
        "anilist_id": 11111,
        "mal_id": 11111,
    },
]


def _reset_state():
    anime_id_map._by_mal = {}
    anime_id_map._by_anilist = {}
    anime_id_map._loaded_at = 0.0


def _use_fixture():
    _reset_state()
    anime_id_map._fetch_entries = lambda: list(_FIXTURE_ENTRIES)
    anime_id_map._crosswalk_enabled = lambda: True
    anime_id_map.disk_cache.load = lambda service, name: None
    anime_id_map.disk_cache.save = lambda service, name, data: None


def run_lookup_tests():
    print("\n" + "=" * 70)
    print("ANIME_ID_MAP - lookup() by mal_id / anilist_id")
    print("=" * 70)

    _use_fixture()

    check(
        "lookup by mal_id (Abenobashi)",
        anime_id_map.lookup(mal_id=306),
        {"themoviedb_id": {"tv": 12144}, "season": {"tvdb": 1, "tmdb": 1}, "episode_offset": None},
    )
    check(
        "lookup by anilist_id (Abenobashi)",
        anime_id_map.lookup(anilist_id=306),
        {"themoviedb_id": {"tv": 12144}, "season": {"tvdb": 1, "tmdb": 1}, "episode_offset": None},
    )
    check("lookup unknown mal_id returns None", anime_id_map.lookup(mal_id=987654321), None)
    check("lookup with no ids returns None", anime_id_map.lookup(), None)
    check(
        "entry with no themoviedb_id is not indexed",
        anime_id_map.lookup(mal_id=11111),
        None,
    )
    check(
        "mal_id takes priority when both mal_id and anilist_id are given",
        anime_id_map.lookup(mal_id=306, anilist_id=99999)["themoviedb_id"],
        {"tv": 12144},
    )


def run_resolve_tmdb_id_tests():
    print("\n" + "=" * 70)
    print("ANIME_ID_MAP - resolve_tmdb_id() media_type selection")
    print("=" * 70)

    _use_fixture()

    check("resolve tv id for a tv entry", anime_id_map.resolve_tmdb_id("tv", mal_id=306), "12144")
    check("resolve movie id for a movie entry", anime_id_map.resolve_tmdb_id("movie", mal_id=99999), "555")
    check(
        "wrong media_type for the entry returns None (tv entry asked as movie)",
        anime_id_map.resolve_tmdb_id("movie", mal_id=306),
        None,
    )
    check(
        "Attack on Titan 3 Part 2 -> shared TMDB show id 1429",
        anime_id_map.resolve_tmdb_id("tv", mal_id=38524),
        "1429",
    )
    check("unknown id resolves to None", anime_id_map.resolve_tmdb_id("tv", mal_id=0), None)


def run_episode_offset_tests():
    print("\n" + "=" * 70)
    print("ANIME_ID_MAP - split-cour season/episode_offset passthrough")
    print("=" * 70)

    _use_fixture()

    record = anime_id_map.lookup(mal_id=38524)
    check("season.tmdb passed through for split-cour entry", record["season"]["tmdb"], 3)
    check("episode_offset.tmdb passed through for split-cour entry", record["episode_offset"]["tmdb"], 12)

    record_no_offset = anime_id_map.lookup(mal_id=306)
    check("episode_offset absent on a normal (non-split) entry", record_no_offset["episode_offset"], None)


def run_toggle_and_resilience_tests():
    print("\n" + "=" * 70)
    print("ANIME_ID_MAP - config toggle & network-failure fallback")
    print("=" * 70)

    _use_fixture()
    anime_id_map._crosswalk_enabled = lambda: False
    check("lookup returns None when crosswalk is disabled via config", anime_id_map.lookup(mal_id=306), None)
    anime_id_map._crosswalk_enabled = lambda: True

    # Simulate: no in-memory index yet, network fetch fails, no disk cache either.
    _reset_state()
    anime_id_map._fetch_entries = lambda: None
    anime_id_map.disk_cache.load = lambda service, name: None
    check("network failure + no disk cache -> lookup returns None, no crash", anime_id_map.lookup(mal_id=306), None)

    # Simulate: network fetch fails, but a fresh on-disk cache exists -> must be used.
    _reset_state()
    stale_payload = anime_id_map._indexes_to_cache_payload(
        *anime_id_map._build_indexes([_FIXTURE_ENTRIES[0]])
    )
    anime_id_map._fetch_entries = lambda: None
    anime_id_map.disk_cache.load = lambda service, name: stale_payload
    check(
        "network failure falls back to on-disk cache",
        anime_id_map.lookup(mal_id=306),
        {"themoviedb_id": {"tv": 12144}, "season": {"tvdb": 1, "tmdb": 1}, "episode_offset": None},
    )


_AOT_PART1 = {"mal_id": 35760, "anilist_id": 99147, "episodes_count": 12}  # offset 0 (implicit)
_AOT_PART2 = {"mal_id": 38524, "anilist_id": 104578, "episodes_count": 10}  # offset 12


def _use_split_cour_fixture():
    _reset_state()
    entries = list(_FIXTURE_ENTRIES) + [
        {
            "type": "TV",
            "anilist_id": 99147,
            "mal_id": 35760,
            "themoviedb_id": {"tv": 1429},
            "season": {"tmdb": 3},
            # no episode_offset key at all: a normal/first part has none.
        },
    ]
    anime_id_map._fetch_entries = lambda: entries
    anime_id_map._crosswalk_enabled = lambda: True
    anime_id_map.disk_cache.load = lambda service, name: None
    anime_id_map.disk_cache.save = lambda service, name, data: None


def run_split_cour_tests():
    print("\n" + "=" * 70)
    print("ANIME_ID_MAP - resolve_split_cour_episode() Part 1/Part 2 disambiguation")
    print("=" * 70)

    _use_split_cour_fixture()
    candidates = [_AOT_PART1, _AOT_PART2]

    check(
        "absolute episode 5 (within Part 1) -> Part 1, local episode 5",
        anime_id_map.resolve_split_cour_episode(candidates, season_num=3, absolute_episode=5),
        {"candidate_index": 0, "local_episode": 5},
    )
    check(
        "absolute episode 13 (first of Part 2) -> Part 2, local episode 1",
        anime_id_map.resolve_split_cour_episode(candidates, season_num=3, absolute_episode=13),
        {"candidate_index": 1, "local_episode": 1},
    )
    check(
        "absolute episode 22 (last of Part 2) -> Part 2, local episode 10",
        anime_id_map.resolve_split_cour_episode(candidates, season_num=3, absolute_episode=22),
        {"candidate_index": 1, "local_episode": 10},
    )
    check(
        "absolute episode 23 (past both parts) -> None",
        anime_id_map.resolve_split_cour_episode(candidates, season_num=3, absolute_episode=23),
        None,
    )
    check(
        "wrong season_num -> None",
        anime_id_map.resolve_split_cour_episode(candidates, season_num=1, absolute_episode=5),
        None,
    )
    check(
        "candidate with unknown mal_id is skipped, not a crash",
        anime_id_map.resolve_split_cour_episode(
            [{"mal_id": 0}, _AOT_PART2], season_num=3, absolute_episode=13
        ),
        {"candidate_index": 1, "local_episode": 1},
    )
    check(
        "empty candidate list -> None",
        anime_id_map.resolve_split_cour_episode([], season_num=3, absolute_episode=1),
        None,
    )
    check(
        "single non-split candidate with no episodes_count still resolves via offset alone",
        anime_id_map.resolve_split_cour_episode([{"mal_id": 306}], season_num=1, absolute_episode=7),
        {"candidate_index": 0, "local_episode": 7},
    )


if __name__ == "__main__":
    # Preserve the real functions so we can restore them after monkeypatching.
    _real_fetch_entries = anime_id_map._fetch_entries
    _real_crosswalk_enabled = anime_id_map._crosswalk_enabled
    _real_disk_cache_load = anime_id_map.disk_cache.load

    try:
        run_lookup_tests()
        run_resolve_tmdb_id_tests()
        run_episode_offset_tests()
        run_toggle_and_resilience_tests()
        run_split_cour_tests()
    finally:
        anime_id_map._fetch_entries = _real_fetch_entries
        anime_id_map._crosswalk_enabled = _real_crosswalk_enabled
        anime_id_map.disk_cache.load = _real_disk_cache_load
        _reset_state()

    print("\n" + "=" * 80)
    print(f"RESULTS: {_results['pass']} passed, {_results['fail']} failed")
    print("=" * 80)
    sys.exit(1 if _results["fail"] else 0)
