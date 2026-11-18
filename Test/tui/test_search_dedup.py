import pytest
from VibraVid.tui.screens.search import deduplicate_search_results

class MockItem:
    def __init__(self, name: str, year: int = 2021, is_movie: bool = False, is_song: bool = False):
        self.name = name
        self.year = year
        self.is_movie = is_movie
        self.is_song = is_song

def test_deduplicate_search_results_combines_providers():
    item1 = MockItem("Breaking Bad", 2008)
    item2 = MockItem("Breaking Bad", 2008)
    
    raw_results = [
        ("animeworld", item1),
        ("streamingcommunity", item2)
    ]
    
    deduped = deduplicate_search_results(raw_results)
    assert len(deduped) == 1
    
    primary_site, primary_item, providers = deduped[0]
    assert len(providers) == 2
    assert ("animeworld", item1) in providers
    assert ("streamingcommunity", item2) in providers
