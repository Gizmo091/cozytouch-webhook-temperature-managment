import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pyoverkiz.models import Command

from .client import (
    BadCredentialsException,
    NotAuthenticatedException,
    TooManyRequestsException,
    cozytouch,
)
from .config import settings
from .presets import PresetStore
from .schemas import (
    BatchCommandItem,
    BatchCommandRequest,
    BatchCommandResponse,
    BatchCommandResultItem,
    CommandAccepted,
    CommandRequest,
    DeviceOut,
    DeviceURLBody,
    GroupedDeviceOut,
    PresetIn,
    PresetOut,
    PresetUpdateIn,
    StateOut,
    build_place_map,
    group_devices,
    serialize_device,
    serialize_state,
)

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("cozytouch-api")

STATIC_DIR = Path(__file__).parent / "static"
preset_store = PresetStore(settings.presets_file)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Cozytouch API starting (server=%s, presets=%s)",
        settings.cozytouch_server,
        settings.presets_file,
    )
    try:
        yield
    finally:
        logger.info("Shutting down, closing Cozytouch client…")
        await cozytouch.close()


app = FastAPI(
    title="Cozytouch local app",
    description=(
        "Pilotage local des équipements Atlantic Cozytouch (radiateurs, thermostats…). "
        "L'app sert l'UI sur / et expose des routes HTTP utilisées par cette UI ainsi "
        "que par les webhooks publics (token-dans-l'URL). Pas d'auth Bearer : déploiement "
        "prévu pour localhost / réseau de confiance."
    ),
    version="0.3.0",
    lifespan=lifespan,
)


@app.exception_handler(BadCredentialsException)
async def _bad_creds(_, exc: BadCredentialsException):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"Cozytouch credentials rejected: {exc}"},
    )


@app.exception_handler(TooManyRequestsException)
async def _rate_limited(_, exc: TooManyRequestsException):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": f"Cozytouch rate limit hit: {exc}"},
    )


@app.exception_handler(NotAuthenticatedException)
async def _not_auth(_, exc: NotAuthenticatedException):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"Cozytouch session lost and could not be re-established: {exc}"},
    )


@app.exception_handler(Exception)
async def _unhandled(_, exc: Exception):
    """Generic fallback so the UI gets a useful error message instead of an opaque 500.

    pyoverkiz can raise its own non-imported exception types (rate limits beyond
    the simple TooManyRequestsException, maintenance windows, network errors,
    invalid commands, …). Without this handler the user sees a bare 500.
    """
    # Re-raise FastAPI's own exceptions so they keep their proper status code & body
    from fastapi.exceptions import HTTPException, RequestValidationError
    if isinstance(exc, (HTTPException, RequestValidationError)):
        raise exc
    logger.exception("Unhandled exception in Cozytouch route: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


# ----------------------------- meta -----------------------------


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "server": settings.cozytouch_server}


# --------------------------- devices ----------------------------


@app.get(
    "/devices",
    response_model=list[DeviceOut],
    tags=["devices"],
)
async def list_devices(
    refresh: bool = Query(
        default=False,
        description="Force pyoverkiz to re-fetch the setup from the cloud (catches renames). Slow.",
    ),
) -> list[DeviceOut]:
    setup = await cozytouch.call(lambda c: c.get_setup(refresh=refresh))
    place_map = build_place_map(setup)
    return [serialize_device(d, place_map=place_map) for d in (setup.devices or [])]


@app.get(
    "/devices/grouped",
    response_model=list[GroupedDeviceOut],
    tags=["devices"],
)
async def list_devices_grouped(
    refresh: bool = Query(
        default=False,
        description="Force pyoverkiz to re-fetch the setup from the cloud (catches renames). Slow.",
    ),
) -> list[GroupedDeviceOut]:
    """Same as /devices but companion subdevices (#2..#5: temp, contact, occupancy,
    energy) are merged into their parent radiator (#1).

    Each entry also surfaces `place_name` — the name of the room the device is
    assigned to in the Atlantic app (e.g. 'Salle à Manger', 'Couloir'). The
    label set in the Cozytouch app is usually the manufacturer default
    ('Radiateur'), so the room name is the right human-friendly identifier
    and the natural grouping key for "all radiators of the dining room".

    Multi-zone safety: if a base URL hosts 2+ HeatingSystem subdevices
    (typical of PassAPC zone controllers), each zone is returned independently
    instead of being silently folded into the first one.
    """
    setup = await cozytouch.call(lambda c: c.get_setup(refresh=refresh))
    place_map = build_place_map(setup)
    raw = [serialize_device(d, place_map=place_map) for d in (setup.devices or [])]
    return group_devices(raw)


@app.get(
    "/places",
    tags=["places"],
)
async def list_places(
    refresh: bool = Query(default=False),
) -> dict:
    """Liste des pièces (oid → label) telle qu'organisée dans l'app Atlantic.

    Permet à un client (UI, HA…) de proposer un sélecteur « par pièce » et
    de mapper côté serveur les commandes vers les `device_url` membres.
    """
    setup = await cozytouch.call(lambda c: c.get_setup(refresh=refresh))
    place_map = build_place_map(setup)
    devices = [
        serialize_device(d, place_map=place_map) for d in (setup.devices or [])
    ]
    rooms: dict[str, dict] = {}
    for d in devices:
        if not d.place_oid or not d.place_name:
            continue
        room = rooms.setdefault(
            d.place_oid,
            {"oid": d.place_oid, "name": d.place_name, "device_urls": []},
        )
        room["device_urls"].append(d.device_url)
    return {"places": list(rooms.values())}


@app.get(
    "/devices/state",
    response_model=list[StateOut],
    tags=["devices"],
)
async def get_device_state(
    device_url: str = Query(..., description="Overkiz device URL"),
) -> list[StateOut]:
    states = await cozytouch.call(lambda c: c.get_state(device_url))
    return [serialize_state(s) for s in states]


@app.post(
    "/devices/refresh",
    tags=["devices"],
)
async def refresh_all_states() -> dict:
    await cozytouch.call(lambda c: c.refresh_states())
    return {"status": "refresh requested"}


@app.post(
    "/devices/refresh/single",
    tags=["devices"],
)
async def refresh_single_device(body: DeviceURLBody) -> dict:
    await cozytouch.call(lambda c: c.refresh_device_states(body.device_url))
    return {"status": "refresh requested", "device_url": body.device_url}


@app.post(
    "/devices/commands",
    response_model=CommandAccepted,
    tags=["devices"],
)
async def execute_command(req: CommandRequest) -> CommandAccepted:
    cmd = Command(req.command, req.parameters or None)
    label = req.label or "cozytouch-rest-api"
    exec_id = await cozytouch.call(
        lambda c: c.execute_command(req.device_url, cmd, label=label)
    )
    return CommandAccepted(exec_id=str(exec_id))


async def _run_actions(
    actions: list[BatchCommandItem],
    label: str,
    stop_on_error: bool,
) -> list[BatchCommandResultItem]:
    results: list[BatchCommandResultItem] = []
    for a in actions:
        cmd = Command(a.command, a.parameters or None)
        try:
            exec_id = await cozytouch.call(
                lambda c, _a=a, _cmd=cmd: c.execute_command(_a.device_url, _cmd, label=label)
            )
            results.append(
                BatchCommandResultItem(
                    device_url=a.device_url,
                    command=a.command,
                    parameters=list(a.parameters),
                    ok=True,
                    exec_id=str(exec_id),
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                BatchCommandResultItem(
                    device_url=a.device_url,
                    command=a.command,
                    parameters=list(a.parameters),
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            if stop_on_error:
                break
    return results


@app.post(
    "/devices/commands/batch",
    response_model=BatchCommandResponse,
    tags=["devices"],
)
async def execute_commands_batch(req: BatchCommandRequest) -> BatchCommandResponse:
    label = req.label or "cozytouch-rest-api"
    results = await _run_actions(req.actions, label, req.stop_on_error)
    return BatchCommandResponse(results=results)


# ---------------------------- setup -----------------------------


@app.get(
    "/setup",
    tags=["debug"],
)
async def get_setup() -> dict:
    """Dump complet du setup Overkiz (équipements, gateways, location). Utile pour debug."""
    setup = await cozytouch.call(lambda c: c.get_setup(refresh=False))

    def _to_jsonable(o):
        if hasattr(o, "model_dump"):
            return o.model_dump()
        if hasattr(o, "__dict__"):
            return {k: _to_jsonable(v) for k, v in vars(o).items() if not k.startswith("_")}
        if isinstance(o, (list, tuple)):
            return [_to_jsonable(x) for x in o]
        if isinstance(o, dict):
            return {k: _to_jsonable(v) for k, v in o.items()}
        return o

    return {"setup": _to_jsonable(setup)}


# --------------------------- presets ----------------------------


def _preset_to_out(p: dict) -> PresetOut:
    return PresetOut(**p)


@app.get(
    "/presets",
    response_model=list[PresetOut],
    tags=["presets"],
)
async def list_presets() -> list[PresetOut]:
    return [_preset_to_out(p) for p in await preset_store.list()]


@app.post(
    "/presets",
    response_model=PresetOut,
    status_code=status.HTTP_201_CREATED,
    tags=["presets"],
)
async def create_preset(req: PresetIn) -> PresetOut:
    record = await preset_store.create(
        {
            "name": req.name,
            "description": req.description,
            "actions": [a.model_dump() for a in req.actions],
        }
    )
    return _preset_to_out(record)


@app.get(
    "/presets/{preset_id}",
    response_model=PresetOut,
    tags=["presets"],
)
async def get_preset(preset_id: str) -> PresetOut:
    p = await preset_store.get(preset_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Preset not found")
    return _preset_to_out(p)


@app.patch(
    "/presets/{preset_id}",
    response_model=PresetOut,
    tags=["presets"],
)
async def update_preset(preset_id: str, req: PresetUpdateIn) -> PresetOut:
    payload = req.model_dump(exclude_unset=True)
    if "actions" in payload and payload["actions"] is not None:
        payload["actions"] = [a if isinstance(a, dict) else a.model_dump() for a in payload["actions"]]
    p = await preset_store.update(preset_id, payload)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Preset not found")
    return _preset_to_out(p)


@app.delete(
    "/presets/{preset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["presets"],
)
async def delete_preset(preset_id: str) -> None:
    ok = await preset_store.delete(preset_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Preset not found")


@app.post(
    "/presets/{preset_id}/rotate-webhook",
    response_model=PresetOut,
    tags=["presets"],
)
async def rotate_preset_webhook(preset_id: str) -> PresetOut:
    p = await preset_store.rotate_webhook(preset_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Preset not found")
    return _preset_to_out(p)


@app.api_route(
    "/presets/{preset_id}/run",
    methods=["GET", "POST"],
    response_model=BatchCommandResponse,
    tags=["presets"],
)
async def run_preset(preset_id: str) -> BatchCommandResponse:
    p = await preset_store.get(preset_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Preset not found")
    actions = [BatchCommandItem(**a) for a in p.get("actions", [])]
    label = f"preset:{p.get('name')}"
    results = await _run_actions(actions, label, stop_on_error=False)
    return BatchCommandResponse(results=results)


# ----- public webhook (no Bearer; secret is in the URL token) -----


@app.api_route(
    "/webhooks/{webhook_token}/run",
    methods=["GET", "POST"],
    response_model=BatchCommandResponse,
    tags=["webhooks"],
)
async def run_preset_via_webhook(webhook_token: str) -> BatchCommandResponse:
    p = await preset_store.get_by_webhook(webhook_token)
    if p is None:
        # Same response shape for unknown / invalid token to limit enumeration.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    actions = [BatchCommandItem(**a) for a in p.get("actions", [])]
    label = f"webhook:{p.get('name')}"
    results = await _run_actions(actions, label, stop_on_error=False)
    return BatchCommandResponse(results=results)


# ----------------------------- UI -------------------------------


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))
