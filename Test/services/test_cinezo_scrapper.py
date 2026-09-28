from VibraVid.services.cinezo import scrapper as cinezo_scrapper


def test_series_metadata_uses_real_tmdb_episode_data(monkeypatch):
    def fake_request(path, params=None):
        if path == "tv/1399":
            return {
                "name": "Game of Thrones",
                "first_air_date": "2011-04-17",
                "seasons": [
                    {
                        "id": 3624,
                        "season_number": 1,
                        "name": "Stagione 1",
                        "episode_count": 10,
                    }
                ],
            }

        if path == "tv/1399/season/1":
            return {
                "episodes": [
                    {
                        "id": 63056,
                        "episode_number": 1,
                        "name": "L'inverno sta arrivando",
                        "runtime": 62,
                        "overview": "Episode description",
                        "still_path": "/episode.jpg",
                        "air_date": "2011-04-17",
                    }
                ]
            }

        raise AssertionError(path)

    monkeypatch.setattr(cinezo_scrapper.tmdb_client, "_make_request", fake_request)

    scraper = cinezo_scrapper.GetSerieInfo(1399, "Game of Thrones")

    assert scraper.getNumberSeason() == 1
    assert scraper.series_name == "Game of Thrones"
    assert scraper.series_year == 2011

    episodes = scraper.getEpisodeSeasons(1)

    assert len(episodes) == 1
    assert episodes[0].id == 63056
    assert episodes[0].number == 1
    assert episodes[0].name == "L'inverno sta arrivando"
    assert episodes[0].duration == 62
    assert episodes[0].image == "https://image.tmdb.org/t/p/w780/episode.jpg"
