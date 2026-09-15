# 06.06.25

from django.urls import path

from . import api_bot, views

urlpatterns = [
    # Search is the home page.
    path("", views.cinema_search, name="search_home"),
    path("", views.cinema_search, name="search_page"),
    path("search/", views.search, name="search"),
    path("api/resolve-tmdb-posters/", views.resolve_tmdb_posters, name="resolve_tmdb_posters"),
    path("download/", views.start_download, name="start_download"),
    path("series-metadata/", views.series_metadata, name="series_metadata"),
    path("series-detail/", views.series_detail, name="series_detail"),

    # Download (formerly the home dashboard)
    path("downloads/", views.cinema_download, name="download_dashboard"),
    path("api/get-downloads/", views.get_downloads_json, name="get_downloads_json"),
    path("api/kill-download/", views.kill_download, name="kill_download"),
    path("api/kill-and-clear-queue/", views.kill_and_clear_queue, name="kill_and_clear_queue"),
    path("api/clear-history/", views.clear_download_history, name="clear_download_history"),

    # Watchlist
    path("watchlist/", views.cinema_watchlist, name="watchlist"),
    path("watchlist/add/", views.add_to_watchlist, name="add_to_watchlist"),
    path("watchlist/remove/<int:item_id>/", views.remove_from_watchlist, name="remove_from_watchlist"),
    path("watchlist/update/<int:item_id>/", views.update_watchlist_item, name="update_watchlist_item"),
    path("watchlist/update-all/", views.update_all_watchlist, name="update_all_watchlist"),
    path("watchlist/auto/<int:item_id>/", views.update_watchlist_auto, name="update_watchlist_auto"),
    path("watchlist/auto-run/", views.run_watchlist_auto_now, name="run_watchlist_auto_now"),
    path("watchlist/auto-interval/", views.set_watchlist_polling_interval, name="set_watchlist_polling_interval"),
    path("watchlist/clear/", views.clear_watchlist, name="clear_watchlist"),
    path("api/watchlist-status/", views.watchlist_status, name="watchlist_status"),

    # Settings
    path("settings/", views.cinema_system, name="settings_editor"),
    path("api/save-settings/", views.save_settings, name="save_settings"),
    path("api/reload-config/", views.reload_config, name="reload_config"),
    path("api/upload-service/", views.upload_service_zip, name="upload_service_zip"),
    path("api/registry-status/", views.registry_status, name="registry_status"),
    path("api/site-cli-options/", views.site_cli_options_schema, name="site_cli_options_schema"),

    # Logs
    path("logs/", views.cinema_logs, name="logs_page"),
    path("api/logs/list/", views.logs_list, name="logs_list"),
    path("api/logs/content/", views.logs_content, name="logs_content"),

    # ARR Integration
    path("api/arr/webhook/seerr/", views.seerr_webhook, name="seerr_webhook"),
    path("api/arr/webhook/sonarr/", views.sonarr_webhook, name="sonarr_webhook"),
    path("api/arr/webhook/radarr/", views.radarr_webhook, name="radarr_webhook"),
    path("api/arr/status/", views.arr_status, name="arr_status"),
    path("api/arr/trigger-sync/", views.arr_trigger_sync, name="arr_trigger_sync"),
    path("arr-stack/", views.arr_stack, name="arr_stack"),

    # In-app update
    path("api/version/check/", views.check_updates, name="check_updates"),
    path("api/version/update/", views.trigger_update, name="trigger_update"),
    path("api/binaries/update/", views.trigger_binaries_update, name="trigger_binaries_update"),

    # Telegram bot bridge (docker/telegram_bot/, trigger-download only — see api_bot.py)
    path("api/bot/search/", api_bot.bot_search, name="bot_search"),
    path("api/bot/seasons/", api_bot.bot_seasons, name="bot_seasons"),
    path("api/bot/download/", api_bot.bot_download, name="bot_download"),
    path("api/bot/sites/", api_bot.bot_sites, name="bot_sites"),
    path("api/bot/cancel/", api_bot.bot_cancel, name="bot_cancel"),
    path("api/bot/status/", api_bot.bot_status, name="bot_status"),
    path("api/bot/logs/", api_bot.bot_logs, name="bot_logs"),
]
