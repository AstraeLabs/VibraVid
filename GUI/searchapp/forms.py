# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


from django import forms
from GUI.searchapp.api import get_available_sites


def get_site_choices():
    sites = get_available_sites()
    return [(site, site.replace('_', ' ').title()) for site in sites]


class SearchForm(forms.Form):
    site = forms.ChoiceField(
        label="Sito",
        widget=forms.Select(
            attrs={
                "class": "block w-full appearance-none rounded-lg border border-gray-300 bg-white py-3 pl-12 pr-12 text-gray-900 placeholder-gray-500 shadow-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500",
            }
        ),
    )
    query = forms.CharField(
        max_length=200,
        label="Cosa cerchi?",
        widget=forms.TextInput(
            attrs={
                "class": "block w-full rounded-lg border border-gray-300 bg-white py-3 pl-12 pr-12 text-gray-900 placeholder-gray-500 shadow-sm focus:border-blue-500 focus:ring-2 focus:ring-blue-500",
                "placeholder": "Cerca titolo...",
                "autocomplete": "off",
            }
        ),
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].choices = get_site_choices()


class DownloadForm(forms.Form):
    source_alias = forms.CharField(widget=forms.HiddenInput)
    item_payload = forms.CharField(widget=forms.HiddenInput)
    season = forms.CharField(max_length=10, required=False, label="Stagione")
    episode = forms.CharField(max_length=20, required=False, label="Episodio (es: 1-3)")