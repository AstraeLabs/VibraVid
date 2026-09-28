# 06.06.25

from django import forms

from .api import get_available_sites, get_site_categories

GLOBAL_ALL_TOKEN = "__all__"
GLOBAL_CATEGORY_PREFIX = "__cat__:"
_CATEGORY_LABELS = {
    "Film_Serie": "Film e Serie",
    "Serie": "Serie TV",
    "Anime": "Anime",
    "song": "Musica",
    "Song": "Musica",
}


def get_site_choices():
    """Build grouped <select> choices: a 'Ricerca Globale' optgroup (all sites + per category)"""
    sites = get_available_sites()

    categories = sorted(set(get_site_categories().values()))
    global_opts = [(GLOBAL_ALL_TOKEN, "🌐 Tutti i siti")]
    for cat in categories:
        label = _CATEGORY_LABELS.get(cat, cat.replace("_", " ").title())
        global_opts.append((f"{GLOBAL_CATEGORY_PREFIX}{cat}", f"🌐 {label}"))

    single_opts = [(site, site.replace("_", " ").title()) for site in sites]

    return [
        ("Siti singoli", single_opts),
        ("Ricerca Globale", global_opts),
    ]


class SearchForm(forms.Form):
    site = forms.ChoiceField(
        label="Sito",
        widget=forms.Select(
            attrs={
                "class": "block w-full appearance-none rounded-xl border-2 border-line-1 bg-scrim/50 py-4 pl-6 pr-12 text-fg text-lg font-medium cursor-pointer focus:border-red-600 focus:outline-none focus:ring-4 focus:ring-red-600/20 transition-all",
                "id": "id_site",
            }
        ),
    )
    query = forms.CharField(
        max_length=200,
        label="Cosa cerchi?",
        widget=forms.TextInput(
            attrs={
                "class": "block w-full rounded-xl border-2 border-line-1 bg-scrim/50 py-4 pl-6 pr-6 text-fg text-lg placeholder-fg-faint focus:border-red-600 focus:outline-none focus:ring-4 focus:ring-red-600/20 transition-all",
                "placeholder": "Cerca titolo...",
                "autocomplete": "off",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["site"].choices = get_site_choices()


class DownloadForm(forms.Form):
    source_alias = forms.CharField(widget=forms.HiddenInput)
    item_payload = forms.CharField(widget=forms.HiddenInput, required=False)
    season = forms.CharField(max_length=100, required=False, label="Stagione")
    episode = forms.CharField(max_length=1000, required=False, label="Episodio (es: 1-3)")
    quality = forms.RegexField(regex=r"^(?:[1-9][0-9]{1,3}p)?$", max_length=5, required=False)
    audio_format = forms.CharField(max_length=16, required=False, label="Formato audio")

    def clean_quality(self):
        from VibraVid.core.utils.quality import normalize_quality
        try:
            return normalize_quality(self.cleaned_data.get("quality"))
        except ValueError as exc:
            raise forms.ValidationError("Choose a quality up to 1080p.") from exc
