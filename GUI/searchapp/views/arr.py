# 06.06.25

import json
import logging
import threading
import time

from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import ArrMediaRequest, ArrProcessingQueue
from ._shared import _is_recent_webhook, _mark_native_webhook_seen

logger = logging.getLogger(__name__)


@require_http_methods(["GET"])
def arr_stack(request: HttpRequest) -> HttpResponse:
    """Display VibraVid's internal queue for media accepted from ARR."""
    from ..arr.arr_service import _load_arr_config

    cfg = _load_arr_config()
    status_filter = request.GET.get("status", "all")
    source_filter = request.GET.get("source", "all")
    sync_filter = request.GET.get("sync", "all")
    query = request.GET.get("q", "").strip()

    queue_entries = ArrProcessingQueue.objects.select_related("media_request").order_by("-enqueued_at")
    if status_filter != "all":
        queue_entries = queue_entries.filter(media_request__status=status_filter)
    if source_filter != "all":
        queue_entries = queue_entries.filter(media_request__arr_source=source_filter)
    if sync_filter != "all":
        queue_entries = queue_entries.filter(media_request__sync_source=sync_filter)
    if query:
        queue_entries = queue_entries.filter(
            Q(media_request__title__icontains=query)
            | Q(dedup_key__icontains=query)
            | Q(media_request__provider__icontains=query)
            | Q(media_request__tmdb_id__icontains=query)
            | Q(media_request__imdb_id__icontains=query)
            | Q(media_request__tvdb_id__icontains=query)
        )

    status_counts = {
        status: ArrMediaRequest.objects.filter(status=status).count()
        for status, _ in ArrMediaRequest.Status.choices
    }
    active_count = ArrProcessingQueue.objects.filter(completed_at__isnull=True).count()
    total_count = ArrProcessingQueue.objects.count()
    filtered_count = queue_entries.count()

    return render(request, "searchapp/cinema_arr.html", {
        # La coda ARR vive sotto Sistema: la voce di menu resta accesa quella.
        "nav_active": "sistema",
        "arr_enabled": cfg.get("enabled", False),
        "polling_enabled": cfg.get("enable_polling", False),
        "entries": queue_entries[:300],
        "shown_count": min(filtered_count, 300),
        "filtered_count": filtered_count,
        "total_count": total_count,
        "active_count": active_count,
        "status_counts": status_counts,
        "status_choices": ArrMediaRequest.Status.choices,
        "sync_choices": ArrMediaRequest.SyncSource.choices,
        "filters": {
            "q": request.GET.get("q", "").strip(),
            "status": request.GET.get("status", "all"),
            "source": request.GET.get("source", "all"),
            "sync": request.GET.get("sync", "all"),
        },
    })


@csrf_exempt
@require_http_methods(["POST"])
def seerr_webhook(request: HttpRequest) -> JsonResponse:
    """
    Seerr/Overseerr webhook endpoint.
    POST /api/arr/webhook/seerr/

    Validates X-Webhook-Token, logs the event, and triggers immediate sync.
    """
    try:
        from ..arr.arr_service import _load_arr_config, trigger_webhook_sync
        from ..models import ArrWebhookEvent

        # ── Log incoming request ──
        logger.info("=" * 60)
        logger.info("[SEERR WEBHOOK] Received request")
        logger.info(f"[SEERR WEBHOOK] Headers: {dict(request.headers)}")
        logger.info(f"[SEERR WEBHOOK] Method: {request.method}")
        logger.info(f"[SEERR WEBHOOK] Content-Type: {request.content_type}")
        logger.info(f"[SEERR WEBHOOK] Body (raw): {request.body[:2000]}")
        logger.info("=" * 60)

        cfg = _load_arr_config()
        logger.info(f"[SEERR WEBHOOK] ARR enabled: {cfg.get('enabled')}")
        logger.info(f"[SEERR WEBHOOK] Seerr webhook enabled: {cfg.get('enable_seerr_webhook')}")

        if not cfg.get("enabled"):
            logger.warning("[SEERR WEBHOOK] ARR services are disabled, ignoring webhook")
            return JsonResponse({"status": "disabled", "message": "ARR services are disabled"}, status=200)

        if not cfg.get("enable_seerr_webhook"):
            logger.warning("[SEERR WEBHOOK] Seerr webhook is disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "Seerr webhook is disabled"}, status=200)

        # Validate webhook token
        expected_secret = cfg.get("seerr", {}).get("webhook_secret", "")
        if expected_secret:
            token = request.headers.get("X-Webhook-Token", "")
            logger.info(f"[SEERR WEBHOOK] Validating token: {'present' if token else 'missing'}")
            if token != expected_secret:
                logger.warning("[SEERR WEBHOOK] Invalid webhook token")
                return JsonResponse({"status": "error", "message": "Invalid webhook token"}, status=403)

        # Parse payload
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            logger.error("[SEERR WEBHOOK] Invalid JSON in request body")
            return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

        logger.info(f"[SEERR WEBHOOK] Parsed payload: {json.dumps(payload, indent=2, ensure_ascii=False)[:1000]}")

        # Detect event type
        notification_type = payload.get("notification_type", "").upper()
        logger.info(f"[SEERR WEBHOOK] notification_type: {notification_type}")

        # Accept multiple variants: MEDIA_APPROVED, MEDIA_AUTO_APPROVED, MEDIA_PENDING, TEST_NOTIFICATION
        if notification_type in ("MEDIA_APPROVED", "MEDIA_AUTO_APPROVED", "MEDIA_PENDING", "TEST_NOTIFICATION"):
            event_type = notification_type
        else:
            event_type = "UNKNOWN"
            logger.warning(f"[SEERR WEBHOOK] Unknown notification_type: {notification_type}")

        # Log the event
        media = payload.get("media", {}) or {}
        media_type = str(media.get("media_type", "")).lower() or None
        tmdb_id = media.get("tmdbId")
        tmdb_id_str = str(tmdb_id) if tmdb_id is not None else None

        webhook_event = ArrWebhookEvent.objects.create(
            event_type=event_type,
            source="seerr",
            media_type=media_type,
            tmdb_id=tmdb_id_str,
            raw_payload=payload,
            processed=False,
        )
        logger.info(f"[SEERR WEBHOOK] Created ArrWebhookEvent id={webhook_event.id}, event_type={event_type}")

        # Handle test notification
        if event_type == "TEST_NOTIFICATION":
            webhook_event.processed = True
            webhook_event.save(update_fields=["processed"])
            webhook_event.event_type = "TEST"
            webhook_event.save(update_fields=["event_type"])
            logger.info("[SEERR WEBHOOK] Test notification processed successfully")
            return JsonResponse({"status": "ok", "message": "Test notification received"})

        # Handle media events
        if event_type in ("MEDIA_APPROVED", "MEDIA_AUTO_APPROVED", "MEDIA_PENDING"):
            logger.info(f"[SEERR WEBHOOK] Media: {media.get('title')} (tmdbId={media.get('tmdbId')}, type={media.get('media_type')})")
            webhook_priority_enabled = cfg.get("webhook_priority_enabled", True)
            native_window = int(cfg.get("native_webhook_priority_window_seconds", 120))
            fallback_delay = int(cfg.get("seerr_fallback_delay_seconds", 20))

            def _async_sync():
                try:
                    from django.db import close_old_connections
                    close_old_connections()

                    if webhook_priority_enabled and tmdb_id_str and media_type in {"tv", "movie"}:
                        preferred_source = "sonarr" if media_type == "tv" else "radarr"
                        if _is_recent_webhook(tmdb_id_str, source=preferred_source, window_seconds=native_window, touch=False):
                            webhook_event.processed = True
                            webhook_event.ignored_by_priority = True
                            webhook_event.save(update_fields=["processed", "ignored_by_priority"])
                            logger.info(
                                f"[SEERR WEBHOOK ASYNC] Skipped by priority; native {preferred_source} webhook already handled tmdbId={tmdb_id_str}"
                            )
                            return

                        if fallback_delay > 0:
                            time.sleep(fallback_delay)
                            if _is_recent_webhook(tmdb_id_str, source=preferred_source, window_seconds=native_window, touch=False):
                                webhook_event.processed = True
                                webhook_event.ignored_by_priority = True
                                webhook_event.save(update_fields=["processed", "ignored_by_priority"])
                                logger.info(
                                    f"[SEERR WEBHOOK ASYNC] Skipped after fallback delay; native {preferred_source} webhook arrived for tmdbId={tmdb_id_str}"
                                )
                                return

                    logger.info(f"[SEERR WEBHOOK ASYNC] Starting sync for event {webhook_event.id}")
                    count = trigger_webhook_sync(payload)
                    webhook_event.processed = True
                    webhook_event.save(update_fields=["processed"])
                    logger.info(f"[SEERR WEBHOOK ASYNC] Sync complete: {count} items enqueued")
                except Exception as exc:
                    webhook_event.error = str(exc)
                    webhook_event.save(update_fields=["error"])
                    logger.error(f"[SEERR WEBHOOK ASYNC] Sync error: {exc}", exc_info=True)

            threading.Thread(target=_async_sync, daemon=True).start()
            logger.info(f"[SEERR WEBHOOK] Started async sync thread for {event_type}")
            return JsonResponse({"status": "ok", "message": f"Processing {event_type}"})

        logger.info(f"[SEERR WEBHOOK] Event {event_type} acknowledged but not processed")
        return JsonResponse({"status": "ok", "message": f"Event {event_type} acknowledged"})

    except Exception as exc:
        logger.error(f"[SEERR WEBHOOK] Unexpected error: {exc}", exc_info=True)
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def sonarr_webhook(request: HttpRequest) -> JsonResponse:
    """
    Sonarr native webhook endpoint.
    POST /api/arr/webhook/sonarr/

    Validates X-Webhook-Token, logs the event, and triggers immediate targeted sync.
    """
    try:
        from ..arr.arr_service import _load_arr_config, _series_tmdb_id, trigger_sonarr_webhook_sync
        from ..models import ArrWebhookEvent

        # ── Log incoming request ──
        logger.info("=" * 60)
        logger.info("[SONARR WEBHOOK] Received request")
        logger.info(f"[SONARR WEBHOOK] Headers: {dict(request.headers)}")
        logger.info(f"[SONARR WEBHOOK] Body (raw): {request.body[:2000]}")
        logger.info("=" * 60)

        cfg = _load_arr_config()
        logger.info(f"[SONARR WEBHOOK] ARR enabled: {cfg.get('enabled')}")
        logger.info(f"[SONARR WEBHOOK] Sonarr webhook enabled: {cfg.get('enable_sonarr_webhook')}")

        if not cfg.get("enabled"):
            logger.warning("[SONARR WEBHOOK] ARR services are disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "ARR services are disabled"}, status=200)

        if not cfg.get("enable_sonarr_webhook"):
            logger.warning("[SONARR WEBHOOK] Sonarr webhook is disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "Sonarr webhook is disabled"}, status=200)

        expected_secret = cfg.get("sonarr_webhook", {}).get("webhook_secret", "")
        if expected_secret:
            token = request.headers.get("X-Webhook-Token", "")
            logger.info(f"[SONARR WEBHOOK] Validating token: {'present' if token else 'missing'}")
            if token != expected_secret:
                logger.warning("[SONARR WEBHOOK] Invalid webhook token")
                return JsonResponse({"status": "error", "message": "Invalid webhook token"}, status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            logger.error("[SONARR WEBHOOK] Invalid JSON in request body")
            return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

        logger.info(f"[SONARR WEBHOOK] Parsed payload: {json.dumps(payload, indent=2, ensure_ascii=False)[:1000]}")

        event_type = payload.get("eventType", "UNKNOWN").upper()
        logger.info(f"[SONARR WEBHOOK] eventType: {event_type}")

        series_data = payload.get("series", {}) or {}
        # Keep the request thread network-free. A TVDB-only payload is resolved
        # to TMDB in the async worker below; never store tvdbId as a tmdbId.
        tmdb_id = series_data.get("tmdbId")
        tmdb_id_str = str(tmdb_id) if tmdb_id is not None else None
        _mark_native_webhook_seen(tmdb_id_str, "sonarr")

        webhook_event = ArrWebhookEvent.objects.create(
            event_type=event_type,
            source="sonarr",
            media_type="tv",
            tmdb_id=tmdb_id_str,
            arr_item_id=series_data.get("id"),
            raw_payload=payload,
            processed=False,
        )
        logger.info(f"[SONARR WEBHOOK] Created ArrWebhookEvent id={webhook_event.id}")

        if event_type == "TEST":
            payload_str = json.dumps(payload, indent=2, ensure_ascii=False)
            logger.info(f"[SONARR WEBHOOK TEST] Payload:\n{payload_str}")
            webhook_event.processed = True
            webhook_event.save(update_fields=["processed"])
            return JsonResponse({
                "status": "ok",
                "message": "Test notification received",
                "payload_preview": payload,
            })

        # Handle events that should trigger sync
        # - DOWNLOAD/GRAB: Sonarr has downloaded episodes -> sync those episodes
        # - SeriesAdd: Series was added to Sonarr (usually after Seerr approval) -> sync missing episodes
        if event_type in ("DOWNLOAD", "GRAB", "SERIESADD"):
            series_data = payload.get("series", {})
            episodes = payload.get("episodes", [])
            logger.info(f"[SONARR WEBHOOK] Series: {series_data.get('title')} (id={series_data.get('id')})")
            logger.info(f"[SONARR WEBHOOK] Episodes count: {len(episodes)}")
            logger.info(f"[SONARR WEBHOOK] Triggering sync for event {event_type}")

            def _async_sync():
                try:
                    from django.db import close_old_connections
                    close_old_connections()
                    resolved_tmdb_id = _series_tmdb_id(series_data)
                    if resolved_tmdb_id:
                        resolved_tmdb_id = str(resolved_tmdb_id)
                        # Avoid resolving the same TVDB id again inside the
                        # targeted sync and make native-webhook priority work
                        # with Sonarr payloads that omit tmdbId.
                        series_data["tmdbId"] = resolved_tmdb_id
                        _mark_native_webhook_seen(resolved_tmdb_id, "sonarr")
                        if webhook_event.tmdb_id != resolved_tmdb_id:
                            webhook_event.tmdb_id = resolved_tmdb_id
                            webhook_event.save(update_fields=["tmdb_id"])
                    logger.info(f"[SONARR WEBHOOK ASYNC] Starting sync for event {webhook_event.id} (type={event_type})")
                    if event_type == "SERIESADD":
                        # For SeriesAdd, use trigger_sonarr_webhook_sync which is
                        # designed for Sonarr native webhooks and can find the series by ID
                        count = trigger_sonarr_webhook_sync(payload)
                    else:
                        count = trigger_sonarr_webhook_sync(payload)
                    webhook_event.processed = True
                    webhook_event.save(update_fields=["processed"])
                    logger.info(f"[SONARR WEBHOOK ASYNC] Sync complete: {count} items enqueued")
                except Exception as exc:
                    webhook_event.error = str(exc)
                    webhook_event.save(update_fields=["error"])
                    logger.error(f"[SONARR WEBHOOK ASYNC] Sync error: {exc}", exc_info=True)

            threading.Thread(target=_async_sync, daemon=True).start()
            logger.info(f"[SONARR WEBHOOK] Started async sync thread for {event_type}")
            return JsonResponse({"status": "ok", "message": f"Processing Sonarr event {event_type}"})

        elif event_type == "SERIESDELETE":
            logger.info("[SONARR WEBHOOK] Series deleted event ignored (no sync needed)")
            webhook_event.processed = True
            webhook_event.save(update_fields=["processed"])
            return JsonResponse({"status": "ok", "message": "SeriesDelete acknowledged, no action needed"})

        logger.info(f"[SONARR WEBHOOK] Event {event_type} acknowledged but not processed")
        return JsonResponse({"status": "ok", "message": f"Event {event_type} acknowledged"})

    except Exception as exc:
        logger.error(f"[SONARR WEBHOOK] Unexpected error: {exc}", exc_info=True)
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def radarr_webhook(request: HttpRequest) -> JsonResponse:
    """
    Radarr native webhook endpoint.
    POST /api/arr/webhook/radarr/

    Validates X-Webhook-Token, logs the event, and triggers immediate targeted sync.
    """
    try:
        from ..arr.arr_service import _load_arr_config, trigger_radarr_webhook_sync
        from ..models import ArrWebhookEvent

        # ── Log incoming request ──
        logger.info("=" * 60)
        logger.info("[RADARR WEBHOOK] Received request")
        logger.info(f"[RADARR WEBHOOK] Headers: {dict(request.headers)}")
        logger.info(f"[RADARR WEBHOOK] Body (raw): {request.body[:2000]}")
        logger.info("=" * 60)

        cfg = _load_arr_config()
        logger.info(f"[RADARR WEBHOOK] ARR enabled: {cfg.get('enabled')}")
        logger.info(f"[RADARR WEBHOOK] Radarr webhook enabled: {cfg.get('enable_radarr_webhook')}")

        if not cfg.get("enabled"):
            logger.warning("[RADARR WEBHOOK] ARR services are disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "ARR services are disabled"}, status=200)

        if not cfg.get("enable_radarr_webhook"):
            logger.warning("[RADARR WEBHOOK] Radarr webhook is disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "Radarr webhook is disabled"}, status=200)

        expected_secret = cfg.get("radarr_webhook", {}).get("webhook_secret", "")
        if expected_secret:
            token = request.headers.get("X-Webhook-Token", "")
            logger.info(f"[RADARR WEBHOOK] Validating token: {'present' if token else 'missing'}")
            if token != expected_secret:
                logger.warning("[RADARR WEBHOOK] Invalid webhook token")
                return JsonResponse({"status": "error", "message": "Invalid webhook token"}, status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            logger.error("[RADARR WEBHOOK] Invalid JSON in request body")
            return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

        logger.info(f"[RADARR WEBHOOK] Parsed payload: {json.dumps(payload, indent=2, ensure_ascii=False)[:1000]}")

        event_type = payload.get("eventType", "UNKNOWN").upper()
        logger.info(f"[RADARR WEBHOOK] eventType: {event_type}")

        movie_data = payload.get("movie", {}) or {}
        tmdb_id = movie_data.get("tmdbId")
        tmdb_id_str = str(tmdb_id) if tmdb_id is not None else None
        _mark_native_webhook_seen(tmdb_id_str, "radarr")

        webhook_event = ArrWebhookEvent.objects.create(
            event_type=event_type,
            source="radarr",
            media_type="movie",
            tmdb_id=tmdb_id_str,
            arr_item_id=movie_data.get("id"),
            raw_payload=payload,
            processed=False,
        )
        logger.info(f"[RADARR WEBHOOK] Created ArrWebhookEvent id={webhook_event.id}")

        if event_type == "TEST":
            payload_str = json.dumps(payload, indent=2, ensure_ascii=False)
            logger.info(f"[RADARR WEBHOOK TEST] Payload:\n{payload_str}")
            webhook_event.processed = True
            webhook_event.save(update_fields=["processed"])
            return JsonResponse({
                "status": "ok",
                "message": "Test notification received",
                "payload_preview": payload,
            })

        movie_data = payload.get("movie", {})
        logger.info(f"[RADARR WEBHOOK] Movie: {movie_data.get('title')} (id={movie_data.get('id')})")

        def _async_sync():
            try:
                from django.db import close_old_connections
                close_old_connections()
                logger.info(f"[RADARR WEBHOOK ASYNC] Starting sync for event {webhook_event.id}")
                count = trigger_radarr_webhook_sync(payload)
                webhook_event.processed = True
                webhook_event.save(update_fields=["processed"])
                logger.info(f"[RADARR WEBHOOK ASYNC] Sync complete: {count} items enqueued")
            except Exception as exc:
                webhook_event.error = str(exc)
                webhook_event.save(update_fields=["error"])
                logger.error(f"[RADARR WEBHOOK ASYNC] Sync error: {exc}", exc_info=True)

        threading.Thread(target=_async_sync, daemon=True).start()
        logger.info(f"[RADARR WEBHOOK] Started async sync thread for {event_type}")
        return JsonResponse({"status": "ok", "message": f"Processing Radarr event {event_type}"})

    except Exception as exc:
        logger.error(f"[RADARR WEBHOOK] Unexpected error: {exc}", exc_info=True)
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@require_http_methods(["GET"])
def arr_status(request: HttpRequest) -> JsonResponse:
    """
    ARR status endpoint.
    GET /api/arr/status/

    Returns current ARR configuration state and recent activity.
    """
    try:
        from ..arr.arr_service import _load_arr_config
        from ..models import ArrMediaRequest, ArrWebhookEvent

        cfg = _load_arr_config()

        # Recent counts
        pending_count = ArrMediaRequest.objects.filter(status="pending").count()
        downloading_count = ArrMediaRequest.objects.filter(status="downloading").count()
        completed_count = ArrMediaRequest.objects.filter(status="completed").count()
        failed_count = ArrMediaRequest.objects.filter(status="failed").count()
        webhook_count = ArrWebhookEvent.objects.count()

        # Latest test payloads per source
        def _latest_test(source_hint):
            qs = ArrWebhookEvent.objects.filter(
                event_type__iexact="test"
            ).order_by("-id")[:20]
            for ev in qs:
                p = ev.raw_payload or {}
                if source_hint == "sonarr" and "series" in p:
                    return p
                if source_hint == "radarr" and "movie" in p:
                    return p
                if source_hint == "seerr" and "notification_type" in p:
                    return p
            return None

        return JsonResponse({
            "enabled": cfg.get("enabled", False),
            "polling_enabled": cfg.get("enable_polling", False),
            "webhook_enabled": cfg.get("enable_seerr_webhook", False),
            "sonarr_webhook_enabled": cfg.get("enable_sonarr_webhook", False),
            "radarr_webhook_enabled": cfg.get("enable_radarr_webhook", False),
            "max_concurrent_downloads": cfg.get("max_concurrent_downloads", 1),
            "webhook_priority_enabled": cfg.get("webhook_priority_enabled", True),
            "native_webhook_priority_window_seconds": cfg.get("native_webhook_priority_window_seconds", 120),
            "seerr_fallback_delay_seconds": cfg.get("seerr_fallback_delay_seconds", 20),
            "polling_interval": cfg.get("polling_interval", 300),
            "full_resync_interval": cfg.get("full_resync_interval", 21600),
            "sonarr_configured": bool(cfg.get("sonarr", {}).get("url")),
            "radarr_configured": bool(cfg.get("radarr", {}).get("url")),
            "last_sonarr_test_payload": _latest_test("sonarr"),
            "last_radarr_test_payload": _latest_test("radarr"),
            "last_seerr_test_payload": _latest_test("seerr"),
            "stats": {
                "pending": pending_count,
                "downloading": downloading_count,
                "completed": completed_count,
                "failed": failed_count,
                "total_webhooks": webhook_count,
            },
        })
    except Exception as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@require_http_methods(["POST"])
def arr_trigger_sync(request: HttpRequest) -> JsonResponse:
    """
    Manually trigger ARR sync.
    POST /api/arr/trigger-sync/
    """
    try:
        from ..arr.arr_service import _load_arr_config, trigger_polling_sync

        cfg = _load_arr_config()
        if not cfg.get("enabled"):
            return JsonResponse({"status": "disabled", "message": "ARR services are disabled"})

        def _async_sync():
            try:
                from django.db import close_old_connections
                close_old_connections()
                count = trigger_polling_sync(full_resync=True)
                logger.info("Manual sync complete: %d items enqueued", count)
            except Exception as exc:
                logger.exception("Manual sync error: %s", exc)

        threading.Thread(target=_async_sync, daemon=True).start()
        return JsonResponse({"status": "ok", "message": "Sync triggered in background"})

    except Exception as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@require_http_methods(["POST"])
def arr_delete_request(request: HttpRequest, queue_id: int) -> JsonResponse:
    """
    Remove an ARR queue entry and its media request.
    POST /api/arr/queue/<queue_id>/delete/
    """
    try:
        with transaction.atomic():
            queue_entry = (
                ArrProcessingQueue.objects
                .select_for_update()
                .select_related("media_request")
                .get(pk=queue_id)
            )

            # A running worker would otherwise race with the next sync and
            # download the same item twice.
            if queue_entry.started_at and not queue_entry.completed_at:
                return JsonResponse(
                    {"status": "error", "message": "Download in progress, wait for it to finish"},
                    status=409,
                )

            media_request = queue_entry.media_request
            media_request_id = media_request.id

            # The queue row goes with it through on_delete=CASCADE.
            media_request.delete()

        logger.info(
            "Deleted ARR queue entry %s (media request %s): %s",
            queue_id, media_request_id, media_request.title,
        )
        return JsonResponse({"status": "ok", "message": "ARR request deleted"})

    except ArrProcessingQueue.DoesNotExist:
        return JsonResponse({"status": "error", "message": "ARR queue entry not found"}, status=404)

    except Exception as exc:
        logger.exception("Failed to delete ARR queue entry %s: %s", queue_id, exc)
        return JsonResponse({"status": "error", "message": "Could not delete ARR request"}, status=500)


__all__ = ['arr_stack', 'seerr_webhook', 'sonarr_webhook', 'radarr_webhook', 'arr_status', 'arr_trigger_sync', 'arr_delete_request']
