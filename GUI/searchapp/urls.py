# 06.06.25

from django.urls import path

from . import api_bot, api_remote, views

urlpatterns = [
    # Search is the home page.
    path("", views.cinema_search, name="search_home"),
    path("", views.cinema_search, name="search_page"),
    path("search/", views.search, name="search"),
    path("api/resolve-tmdb-posters/", views.resolve_tmdb_posters, name="resolve_tmdb_posters"),
    path("download/", views.start_download, name="start_download"),
    path("api/available-qualities/", views.available_qualities, name="available_qualities"),
    path("series-metadata/", views.series_metadata, name="series_metadata"),
    path("series-detail/", views.series_detail, name="series_detail"),

    # Download (formerly the home dashboard)
    path("downloads/", views.cinema_download, name="download_dashboard"),
    path("api/get-downloads/", views.get_downloads_json, name="get_downloads_json"),
    path("api/confirm-manual-tracks/", views.confirm_manual_tracks, name="confirm_manual_tracks"),
    path("api/downloads-summary/", views.get_downloads_summary, name="get_downloads_summary"),
    path("api/kill-download/", views.kill_download, name="kill_download"),
    path("api/remove-queued-download/", views.remove_queued_download, name="remove_queued_download"),
    path("api/clear-queued-downloads/", views.clear_queued_downloads, name="clear_queued_downloads"),
    path("api/kill-and-clear-queue/", views.kill_and_clear_queue, name="kill_and_clear_queue"),
    path("api/stop-all-downloads/", views.stop_all_downloads, name="stop_all_downloads"),
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
    path("api/remove-service/", views.remove_service_view, name="remove_service"),
    path("api/registry-status/", views.registry_status, name="registry_status"),
    path("api/site-cli-options/", views.site_cli_options_schema, name="site_cli_options_schema"),

    # Logs
    path("logs/", views.cinema_logs, name="logs_page"),
    path("api/logs/list/", views.logs_list, name="logs_list"),
    path("api/logs/content/", views.logs_content, name="logs_content"),
    path("api/logs/clear/", views.logs_clear, name="logs_clear"),

    # ARR Integration
    path("api/arr/webhook/seerr/", views.seerr_webhook, name="seerr_webhook"),
    path("api/arr/webhook/sonarr/", views.sonarr_webhook, name="sonarr_webhook"),
    path("api/arr/webhook/radarr/", views.radarr_webhook, name="radarr_webhook"),
    path("api/arr/status/", views.arr_status, name="arr_status"),
    path("api/arr/trigger-sync/", views.arr_trigger_sync, name="arr_trigger_sync"),
    path("api/arr/queue/<int:queue_id>/delete/", views.arr_delete_request, name="arr_delete_request"),
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

    # Remote server mode (resolve on the server, download on the client — see api_remote.py)
    path("api/remote/health/", api_remote.remote_health, name="remote_health"),
    path("api/remote/sites/", api_remote.remote_sites, name="remote_sites"),
    path("api/remote/sites/<str:site>/info/", api_remote.remote_site_info, name="remote_site_info"),
    path("api/remote/search/", api_remote.remote_search, name="remote_search"),
    path("api/remote/seasons/", api_remote.remote_seasons, name="remote_seasons"),
    path("api/remote/resolve/", api_remote.remote_resolve, name="remote_resolve"),
    path("api/remote/login/", api_remote.remote_login_start, name="remote_login_start"),
    path("api/remote/login/<str:job_id>/", api_remote.remote_login, name="remote_login"),
    path("api/remote/login/<str:job_id>/answer/", api_remote.remote_login_answer, name="remote_login_answer"),
    path("api/remote/session/<str:session_id>/license/", api_remote.remote_license, name="remote_license"),
    path("api/remote/session/<str:session_id>/keys/", api_remote.remote_keys, name="remote_keys"),
    path("api/remote/session/<str:session_id>/refresh/", api_remote.remote_refresh, name="remote_refresh"),
    path("api/remote/session/<str:session_id>/", api_remote.remote_close, name="remote_close"),
]
