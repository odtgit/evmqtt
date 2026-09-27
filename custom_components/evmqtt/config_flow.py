"""Config and options flow for evmqtt."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from evmqtt.config import default_base_topic

from .const import (
    CONF_ENABLED_DEVICES,
    CONF_INCLUDE_VIRTUAL,
    CONF_KEYSTATES,
    CONF_MQTT_BASE_TOPIC,
    CONF_MQTT_MIRROR,
    CONF_NEW_DEVICES_ENABLED,
    CONF_RESCAN_INTERVAL,
    DEFAULT_KEYSTATES,
    DEFAULT_RESCAN_INTERVAL,
    DOMAIN,
    KEYSTATES,
    MAX_RESCAN_INTERVAL,
)
from .hub import EvmqttConfigEntry
from .scan import FoundDevice, InputAccessError, scan_devices

_TOPIC = re.compile(r"^[^#+\0]+(/[^#+\0]+)*$")


def _device_selector(options: list[SelectOptionDict]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options, multiple=True, mode=SelectSelectorMode.LIST
        )
    )


def _label(found: FoundDevice) -> str:
    label = f"{found.info.name} ({found.info.path})"
    return f"{label} [virtual]" if found.virtual else label


def mqtt_available(hass: HomeAssistant) -> bool:
    return any(
        entry.state is ConfigEntryState.LOADED
        for entry in hass.config_entries.async_entries("mqtt")
    )


class EvmqttConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._include_virtual = False
        self._device_form = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: EvmqttConfigEntry) -> EvmqttOptionsFlow:
        return EvmqttOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        submitted = user_input is not None and self._device_form
        if user_input is not None and self._device_form:
            include = bool(user_input.get(CONF_INCLUDE_VIRTUAL, False))
            if include != self._include_virtual:
                self._include_virtual = include
                submitted = False
        try:
            found = await self.hass.async_add_executor_job(scan_devices, True)
        except InputAccessError as err:
            self._device_form = False
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({}), errors={"base": err.reason}
            )
        candidates = [f for f in found if self._include_virtual or not f.virtual]
        ids = {f.info.id for f in candidates}
        if submitted and user_input is not None:
            enabled = [i for i in user_input.get(CONF_ENABLED_DEVICES, []) if i in ids]
            return self.async_create_entry(
                title="Input devices",
                data={},
                options={
                    CONF_ENABLED_DEVICES: sorted(enabled),
                    CONF_INCLUDE_VIRTUAL: self._include_virtual,
                    CONF_KEYSTATES: list(DEFAULT_KEYSTATES),
                    CONF_RESCAN_INTERVAL: DEFAULT_RESCAN_INTERVAL,
                    CONF_NEW_DEVICES_ENABLED: False,
                    CONF_MQTT_MIRROR: False,
                    CONF_MQTT_BASE_TOPIC: default_base_topic(),
                },
            )
        self._device_form = True
        selected = [
            i for i in (user_input or {}).get(CONF_ENABLED_DEVICES, []) if i in ids
        ]
        schema: dict[Any, Any] = {}
        if candidates:
            schema[vol.Optional(CONF_ENABLED_DEVICES, default=selected)] = (
                _device_selector(
                    [
                        SelectOptionDict(value=f.info.id, label=_label(f))
                        for f in candidates
                    ]
                )
            )
        schema[vol.Optional(CONF_INCLUDE_VIRTUAL, default=self._include_virtual)] = (
            BooleanSelector()
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema),
            errors={} if candidates else {"base": "no_devices"},
            description_placeholders={
                "count": str(len(candidates)),
                "virtual": str(sum(f.virtual for f in found)),
            },
        )


class EvmqttOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry: EvmqttConfigEntry = self.config_entry
        options = dict(entry.options)
        show_mqtt = mqtt_available(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_KEYSTATES):
                errors[CONF_KEYSTATES] = "keystates_required"
            topic = str(user_input.get(CONF_MQTT_BASE_TOPIC, "")).strip().strip("/")
            if (
                show_mqtt
                and user_input.get(CONF_MQTT_MIRROR)
                and not _TOPIC.match(topic)
            ):
                errors[CONF_MQTT_BASE_TOPIC] = "invalid_topic"
            if not errors:
                if show_mqtt:
                    user_input[CONF_MQTT_BASE_TOPIC] = topic or default_base_topic()
                new = {**options, **user_input}
                new[CONF_ENABLED_DEVICES] = sorted(new.get(CONF_ENABLED_DEVICES, []))
                new[CONF_RESCAN_INTERVAL] = int(new[CONF_RESCAN_INTERVAL])
                return self.async_create_entry(data=new)
            options.update(user_input)

        devices = self._device_options(entry)
        known = {d["value"] for d in devices}
        schema: dict[Any, Any] = {
            vol.Optional(
                CONF_ENABLED_DEVICES,
                default=[
                    i for i in options.get(CONF_ENABLED_DEVICES, []) if i in known
                ],
            ): _device_selector(devices),
            vol.Required(
                CONF_KEYSTATES, default=options.get(CONF_KEYSTATES, DEFAULT_KEYSTATES)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(KEYSTATES),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                    translation_key=CONF_KEYSTATES,
                )
            ),
            vol.Required(
                CONF_RESCAN_INTERVAL,
                default=options.get(CONF_RESCAN_INTERVAL, DEFAULT_RESCAN_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=MAX_RESCAN_INTERVAL,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_INCLUDE_VIRTUAL,
                default=options.get(CONF_INCLUDE_VIRTUAL, False),
            ): BooleanSelector(),
            vol.Required(
                CONF_NEW_DEVICES_ENABLED,
                default=options.get(CONF_NEW_DEVICES_ENABLED, False),
            ): BooleanSelector(),
        }
        if show_mqtt:
            schema[
                vol.Required(
                    CONF_MQTT_MIRROR, default=options.get(CONF_MQTT_MIRROR, False)
                )
            ] = BooleanSelector()
            schema[
                vol.Optional(
                    CONF_MQTT_BASE_TOPIC,
                    default=options.get(CONF_MQTT_BASE_TOPIC) or default_base_topic(),
                )
            ] = TextSelector()
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema), errors=errors
        )

    def _device_options(self, entry: EvmqttConfigEntry) -> list[SelectOptionDict]:
        labels: dict[str, str] = {}
        registry = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            for domain, device_id in device.identifiers:
                if domain == DOMAIN:
                    labels[device_id] = device.name_by_user or device.name or device_id
        if entry.state is ConfigEntryState.LOADED:
            for tracked in entry.runtime_data.devices.values():
                name = labels.get(tracked.id, tracked.name)
                if tracked.present and tracked.info is not None:
                    labels[tracked.id] = f"{name} ({tracked.info.path})"
                else:
                    labels[tracked.id] = f"{name} [unplugged]"
        return [
            SelectOptionDict(value=device_id, label=label)
            for device_id, label in sorted(labels.items(), key=lambda kv: kv[1])
        ]
