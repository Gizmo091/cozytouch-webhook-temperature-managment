from typing import Any

from pydantic import BaseModel, Field


class StateOut(BaseModel):
    name: str
    value: Any = None
    type: Any = None


class DeviceOut(BaseModel):
    device_url: str
    label: str | None = None
    controllable_name: str | None = None
    ui_class: str | None = None
    widget: str | None = None
    available: bool | None = None
    enabled: bool | None = None
    place_oid: str | None = None
    place_name: str | None = None
    states: list[StateOut] = Field(default_factory=list)


class CommandRequest(BaseModel):
    device_url: str = Field(..., description="Overkiz device URL, e.g. io://1234-5678-9012/12345678")
    command: str = Field(..., description="Command name, e.g. setTargetTemperature")
    parameters: list[Any] = Field(default_factory=list)
    label: str | None = Field(default=None, description="Optional label shown in Cozytouch history")


class CommandAccepted(BaseModel):
    exec_id: str


class DeviceURLBody(BaseModel):
    device_url: str


class BatchCommandItem(BaseModel):
    device_url: str
    command: str
    parameters: list[Any] = Field(default_factory=list)


class BatchCommandRequest(BaseModel):
    actions: list[BatchCommandItem]
    label: str | None = None
    stop_on_error: bool = False


class BatchCommandResultItem(BaseModel):
    device_url: str
    command: str
    parameters: list[Any]
    ok: bool
    exec_id: str | None = None
    error: str | None = None


class BatchCommandResponse(BaseModel):
    results: list[BatchCommandResultItem]


class PresetActionIn(BaseModel):
    device_url: str
    command: str
    parameters: list[Any] = Field(default_factory=list)


class PresetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    actions: list[PresetActionIn] = Field(default_factory=list)


class PresetUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    actions: list[PresetActionIn] | None = None


class PresetOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    actions: list[PresetActionIn] = Field(default_factory=list)
    webhook_token: str
    created_at: int
    updated_at: int


# ----- Grouped device view (radiateur + capteurs fusionnés) -----

class GroupedSensors(BaseModel):
    room_temperature: float | None = None
    window_contact: str | None = None  # "open" / "closed"
    occupancy: str | None = None
    energy_consumption_wh: int | None = None


class GroupedDeviceOut(BaseModel):
    """A heater (or other device) with its companion sensors merged in."""

    device_url: str = Field(..., description="URL of the controllable device (the one to send commands to)")
    base_url: str = Field(..., description="Common URL prefix shared with companion sensors (without #N suffix)")
    label: str | None = None
    place_name: str | None = Field(
        default=None,
        description="Name of the room/place the device is assigned to (e.g. 'Salle à Manger'). "
                    "More user-friendly than the device label which is often the manufacturer default ('Radiateur').",
    )
    short_id: str | None = Field(
        default=None,
        description="Short numeric ID extracted from the URL, useful as a disambiguator when several devices share the same label",
    )
    category: str = Field(..., description="'heater' | 'pod' | 'gateway' | 'sensor' | 'other'")
    controllable_name: str | None = None
    ui_class: str | None = None
    widget: str | None = None
    supports_setpoint: bool = Field(
        default=False,
        description="True if this device understands setTargetTemperature (numeric setpoint)",
    )
    supports_heating_level: bool = Field(
        default=False,
        description="True if this device understands setHeatingLevel (eco/comfort/frostprotection/...)",
    )
    setpoint: float | None = None
    heating_level: str | None = None
    operating_mode: str | None = None
    on_off: str | None = None
    open_window_detection: str | None = None
    power_w: int | None = None
    model: str | None = None
    sensors: GroupedSensors = Field(default_factory=GroupedSensors)
    subdevice_urls: list[str] = Field(default_factory=list)
    states: list[StateOut] = Field(default_factory=list)


_SETPOINT_CONTROLLABLES = {
    "io:AtlanticElectricalHeaterWithAdjustableTemperatureSetpointIOComponent",
    "io:AtlanticElectricalTowelDryerIOComponent",
    "io:AtlanticPassAPCHeatingZoneComponent",
}


def _state_value(states: list[StateOut], *names: str) -> Any:
    for n in names:
        for s in states:
            if s.name == n:
                return s.value
    return None


def _short_id_from_url(url: str) -> str | None:
    """Extract the numeric/short identifier from an Overkiz URL.

    'io://0831-4903-4982/15038469#1' → '15038469'
    'internal://0831-4903-4982/pod/0' → 'pod/0'
    """
    if not url:
        return None
    base = url.rsplit("#", 1)[0]
    tail = base.rsplit("/", 1)[-1]
    return tail or None


_SENSOR_UI_CLASSES = {
    "TemperatureSensor",
    "ContactSensor",
    "OccupancySensor",
    "ElectricitySensor",
}

_CATEGORY_BY_UI_CLASS = {
    "HeatingSystem": "heater",
    "Pod": "pod",
    "ProtocolGateway": "gateway",
    "TemperatureSensor": "sensor",
    "ContactSensor": "sensor",
    "OccupancySensor": "sensor",
    "ElectricitySensor": "sensor",
}


def _categorize(ui_class: str | None) -> str:
    if not ui_class:
        return "other"
    return _CATEGORY_BY_UI_CLASS.get(ui_class, "other")


def _resolve_label(d: DeviceOut) -> str | None:
    """Prefer the live `core:NameState` over the cached top-level label.

    pyoverkiz caches the device.label at login. After a rename in the Atlantic
    app, the state usually refreshes faster than the cached label. Falling back
    on label only when the state is missing keeps things robust.
    """
    name_state = _state_value(d.states, "core:NameState")
    if name_state is not None:
        s = str(name_state).strip()
        if s:
            return s
    return d.label


def _merge_sensor(d: DeviceOut, sensors: GroupedSensors) -> None:
    if d.ui_class == "TemperatureSensor":
        v = _state_value(d.states, "core:TemperatureState")
        if v is not None and sensors.room_temperature is None:
            try:
                sensors.room_temperature = float(v)
            except (TypeError, ValueError):
                pass
    elif d.ui_class == "ContactSensor":
        v = _state_value(d.states, "core:ContactState")
        if v is not None and sensors.window_contact is None:
            sensors.window_contact = str(v)
    elif d.ui_class == "OccupancySensor":
        v = _state_value(d.states, "core:OccupancyState")
        if v is not None and sensors.occupancy is None:
            sensors.occupancy = str(v)
    elif d.ui_class == "ElectricitySensor":
        v = _state_value(d.states, "core:ElectricEnergyConsumptionState")
        if v is not None and sensors.energy_consumption_wh is None:
            try:
                sensors.energy_consumption_wh = int(v)
            except (TypeError, ValueError):
                pass


def _build_grouped(
    primary: DeviceOut, base: str, sensors_to_merge: list[DeviceOut]
) -> GroupedDeviceOut:
    sensors = GroupedSensors()
    for s in sensors_to_merge:
        _merge_sensor(s, sensors)

    category = _categorize(primary.ui_class)
    controllable = primary.controllable_name or ""
    supports_setpoint = controllable in _SETPOINT_CONTROLLABLES or any(
        s.name == "core:TargetTemperatureState" for s in primary.states
    )
    supports_heating_level = any(
        s.name in ("io:TargetHeatingLevelState", "core:TargetHeatingLevelState")
        for s in primary.states
    )

    setpoint_val = _state_value(primary.states, "core:TargetTemperatureState")
    try:
        setpoint = float(setpoint_val) if setpoint_val is not None else None
    except (TypeError, ValueError):
        setpoint = None

    power_val = _state_value(primary.states, "io:PowerState")
    try:
        power_w = int(power_val) if power_val is not None else None
    except (TypeError, ValueError):
        power_w = None

    return GroupedDeviceOut(
        device_url=primary.device_url,
        base_url=base,
        label=_resolve_label(primary),
        place_name=primary.place_name,
        short_id=_short_id_from_url(primary.device_url),
        category=category,
        controllable_name=primary.controllable_name,
        ui_class=primary.ui_class,
        widget=primary.widget,
        supports_setpoint=supports_setpoint,
        supports_heating_level=supports_heating_level,
        setpoint=setpoint,
        heating_level=_state_value(
            primary.states, "io:TargetHeatingLevelState", "core:TargetHeatingLevelState"
        ),
        operating_mode=_state_value(primary.states, "core:OperatingModeState"),
        on_off=_state_value(primary.states, "core:OnOffState"),
        open_window_detection=_state_value(
            primary.states, "core:OpenWindowDetectionActivationState"
        ),
        power_w=power_w,
        model=_state_value(primary.states, "io:ModelState"),
        sensors=sensors,
        subdevice_urls=[s.device_url for s in sensors_to_merge],
        states=primary.states,
    )


def group_devices(devices: list[DeviceOut]) -> list[GroupedDeviceOut]:
    """Merge companion sensor subdevices into their parent radiator.

    Each radiator on Atlantic Cozytouch typically appears as 5 Overkiz
    subdevices: `*#1` (the controllable HeatingSystem) and `*#2..#5` (companion
    Temperature/Contact/Occupancy/Electricity sensors). They share the same
    URL prefix, so we group by `device_url.rsplit("#")[0]` and fold the sensors
    into the parent.

    Multi-zone case: some controllers (e.g. PassAPC) expose several heaters
    under the same prefix (`*#1`, `*#2`, `*#3` are all HeatingSystems). In that
    case, folding sensors into "the first heater" would silently merge zones
    that the user expects to see separately. Detection rule: if a base group
    contains 2+ primaries (non-sensor), each primary stays independent and
    sensors in that group are surfaced standalone.
    """

    by_base: dict[str, list[DeviceOut]] = {}
    for d in devices:
        base = d.device_url.rsplit("#", 1)[0] if "#" in d.device_url else d.device_url
        by_base.setdefault(base, []).append(d)

    out: list[GroupedDeviceOut] = []
    for base, parts in by_base.items():
        primaries = [p for p in parts if p.ui_class not in _SENSOR_UI_CLASSES]
        sensors = [p for p in parts if p.ui_class in _SENSOR_UI_CLASSES]

        if len(primaries) == 1:
            # Classic case: 1 controllable + its companion sensors → fold
            out.append(_build_grouped(primaries[0], base, sensors))
        elif len(primaries) >= 2:
            # Multi-zone: keep each primary independent, sensors separate
            for p in primaries:
                out.append(_build_grouped(p, base, []))
            for s in sensors:
                out.append(_build_grouped(s, base, []))
        else:
            # No primary in this group (orphan sensors) → expose individually
            for s in sensors:
                out.append(_build_grouped(s, base, []))

    # Sort: heaters first, then by label/short_id for stable display
    category_order = {"heater": 0, "pod": 1, "gateway": 2, "sensor": 3, "other": 4}
    out.sort(key=lambda g: (category_order.get(g.category, 9), g.label or "", g.short_id or ""))
    return out


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def serialize_state(state: Any) -> StateOut:
    return StateOut(
        name=str(_attr(state, "name", default="")),
        value=_attr(state, "value"),
        type=_attr(state, "type"),
    )


def build_place_map(setup: Any) -> dict[str, str]:
    """Walk Setup.root_place → {place_oid: place_label}.

    pyoverkiz exposes a tree (root_place with sub_places). Devices link to a
    place via Device.place_oid. We flatten the tree so we can resolve a
    device's room name in O(1).
    """
    out: dict[str, str] = {}

    def walk(place: Any) -> None:
        if place is None:
            return
        oid = _attr(place, "oid", "id")
        label = _attr(place, "label")
        if oid:
            out[str(oid)] = str(label) if label else ""
        for sp in _attr(place, "sub_places", default=None) or []:
            walk(sp)

    walk(_attr(setup, "root_place"))
    return out


def serialize_device(device: Any, place_map: dict[str, str] | None = None) -> DeviceOut:
    raw_states = _attr(device, "states", default=None) or []
    place_oid = _attr(device, "place_oid", "placeOID")
    place_name: str | None = None
    if place_oid and place_map:
        resolved = place_map.get(str(place_oid))
        if resolved:
            place_name = resolved
    return DeviceOut(
        device_url=str(_attr(device, "device_url", "deviceURL", default="")),
        label=_attr(device, "label"),
        controllable_name=_attr(device, "controllable_name", "controllableName"),
        ui_class=_attr(device, "ui_class", "uiClass"),
        widget=_attr(device, "widget", "widget_name", "widgetName"),
        available=_attr(device, "available"),
        enabled=_attr(device, "enabled"),
        place_oid=str(place_oid) if place_oid else None,
        place_name=place_name,
        states=[serialize_state(s) for s in raw_states],
    )
